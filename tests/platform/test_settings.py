"""V7 settings from the Notion Runtime Configuration source (shadow rule, last accepted recovery), and no new GitHub
value.
"""
import contextlib
import re
import unittest
from pathlib import Path
from unittest import mock

from lifeos.platform import settings
from tests.platform.settings_fixture import ENV, SOURCE, Source, from_lines, row  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
ID = "4183c5a0-5926-81be-9e19-e85714dc256f"

# Every GitHub secret or variable the workflows use.
FROZEN = frozenset({"LIFEOS_BWS_RUNTIME_TOKEN", "DOPPLER_TOKEN"})


def used():
    return {m.group(2) for path in (ROOT / ".github").rglob("*.yml") for m in re.finditer(r"\b(vars|secrets)\.([A-Z0-9_]+)", path.read_text())} - {"GITHUB_TOKEN"}


class NoNewGitHubValue(unittest.TestCase):
    def test_no_workflow_uses_a_github_value_outside_the_frozen_list(self):
        self.assertEqual(sorted(used() - FROZEN), [], "put a new setting on the Notion page 'V7 settings (machine read)' (lifeos/platform/settings.py) or in Bitwarden, never in GitHub")

    def test_the_frozen_list_only_shrinks(self):
        self.assertEqual(sorted(FROZEN - used()), [], "a name the workflows no longer use: delete it from FROZEN (and from GitHub, with Jim's yes)")

    def test_a_safety_value_is_never_movable(self):
        self.assertEqual(settings.SAFETY & settings.MOVABLE, frozenset())


def Page(lines, broken=False):
    """The settings source, built from `NAME value` lines (the shape the older tests used)."""
    return from_lines(lines, broken=broken)


def run(env, client=None, **kw):
    """settings.apply with the bootstrap ID declared, as every production step declares it."""
    added = "V7_RUNTIME_CONFIG_SOURCE_ID" not in env
    env["V7_RUNTIME_CONFIG_SOURCE_ID"] = env.get("V7_RUNTIME_CONFIG_SOURCE_ID", SOURCE)
    try:
        return settings.apply(env, client, **kw)
    finally:
        if added:
            env.pop("V7_RUNTIME_CONFIG_SOURCE_ID")


class FakeStore:
    def __init__(self):
        self.rows, self.saves = {}, 0

    def ensure(self, connection):
        pass

    def load(self, connection, key):
        return self.rows.get(key)

    def save(self, connection, key, snapshot):
        self.rows[key] = snapshot
        self.saves += 1


@contextlib.contextmanager
def connect():
    yield object()


class LastAccepted(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch("lifeos.platform.stored_pages.STORE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_good_read_is_remembered_once(self):
        page = Page(["GRANOLA_AUTO on", f"JIRA_CARD_BLOCK_ID {ID}"])
        self.assertTrue(run({}, page, connect=connect)["remembered"])
        self.assertFalse(run({}, page, connect=connect)["remembered"])
        self.assertEqual((self.store.saves, self.store.rows[settings.SNAPSHOT_KEY]["values"]), (1, {"GRANOLA_AUTO": "on", "JIRA_CARD_BLOCK_ID": ID}))

    def test_an_unreadable_page_falls_back_to_the_last_accepted_map_and_says_so(self):
        run({}, Page(["GRANOLA_AUTO on", f"JIRA_CARD_BLOCK_ID {ID}"]), connect=connect)
        self.store.rows[settings.SNAPSHOT_KEY]["values"]["V7_LIVE"] = "false"
        env = {"GRANOLA_AUTO": "", "JIRA_CARD_BLOCK_ID": "", "V7_LIVE": ""}
        out = run(env, Page([], broken=True), connect=connect)
        self.assertEqual(env, {"GRANOLA_AUTO": "on", "JIRA_CARD_BLOCK_ID": ID, "V7_LIVE": ""})
        self.assertEqual((out["status"], out["filled"]), ("unreadable:RUNTIME_CONFIG_UNREADABLE", ["GRANOLA_AUTO", "JIRA_CARD_BLOCK_ID"]))
        self.assertRegex(out["last_accepted"], r"^\d+/\d+ · \d+:\d\d [AP]M CT$")

    def test_no_saved_map_keeps_todays_behavior(self):
        env = {"GRANOLA_AUTO": ""}
        out = run(env, Page([], broken=True), connect=connect)
        self.assertEqual((env, "last_accepted" in out), ({"GRANOLA_AUTO": ""}, False))

    def test_a_step_without_a_notion_token_still_gets_the_last_accepted_map(self):
        run({}, Page(["GRANOLA_AUTO on"]), connect=connect)
        env = {"GITHUB_ACTIONS": "true", "GRANOLA_AUTO": ""}
        out = run(env, connect=connect)
        self.assertEqual((out["status"], env["GRANOLA_AUTO"]), ("no_token", "on"))

    def test_a_broken_store_never_fails_the_run(self):
        @contextlib.contextmanager
        def down():
            raise RuntimeError("STORE_SSH_TCP_TIMEOUT")
            yield
        out = run({}, Page(["GRANOLA_AUTO on"]), connect=down)
        self.assertEqual((out["status"], out["remember_status"]), ("ok", "STORE_SSH_TCP_TIMEOUT"))


class DeclaredOnly(unittest.TestCase):
    def test_a_name_the_step_never_declares_is_never_filled(self):
        env = {"GRANOLA_AUTO": ""}
        out = run(env, Page(["GRANOLA_AUTO on", "HIRING_AUTO_CLOSE true"]))
        self.assertEqual((env, out["filled"], out["undeclared"]), ({"GRANOLA_AUTO": "on"}, ["GRANOLA_AUTO"], 1))

    def test_a_step_that_declares_the_notion_ids_map_receives_its_names_from_the_page(self):
        env = {"LIFEOS_NOTION_IDS": ""}
        out = run(env, Page([f"LIFEOS_NOTION_DASHBOARD_PAGE {ID}", f"MEGIBOW_BLOCK_ID {ID}"]))
        self.assertEqual((env, out["filled"], out["undeclared"]), ({"LIFEOS_NOTION_IDS": "", "LIFEOS_NOTION_DASHBOARD_PAGE": ID}, ["LIFEOS_NOTION_DASHBOARD_PAGE"], 1))

    def test_the_notion_ids_map_counts_as_held(self):
        env = {"LIFEOS_NOTION_IDS": '{"DASHBOARD_PAGE": "%s"}' % ID, "LIFEOS_NOTION_REVIEW_SOURCE": ""}
        out = run(env, Page([f"LIFEOS_NOTION_DASHBOARD_PAGE {ID}", f"LIFEOS_NOTION_REVIEW_SOURCE {ID}"]))
        self.assertEqual((out["matched"], out["filled"]), (1, ["LIFEOS_NOTION_REVIEW_SOURCE"]))


class ShadowRule(unittest.TestCase):
    def test_notion_fills_only_what_the_environment_lacks_and_compares_the_rest(self):
        env = {"HIRING_AUTO_CLOSE": "on", "JIRA_CARD_BLOCK_ID": "x" * 32, "MEGIBOW_BLOCK_ID": "", "INTERVIEW_TEST_COMPANIES": ""}
        page = Page(["# addresses", f"MEGIBOW_BLOCK_ID {ID}", f"JIRA_CARD_BLOCK_ID {ID}", "# switches", "HIRING_AUTO_CLOSE on", "INTERVIEW_TEST_COMPANIES Smartsheet, Walmart"])
        out = run(env, page)
        self.assertEqual(env["MEGIBOW_BLOCK_ID"], ID)
        self.assertEqual(env["INTERVIEW_TEST_COMPANIES"], "Smartsheet, Walmart")
        self.assertEqual(env["JIRA_CARD_BLOCK_ID"], "x" * 32)
        self.assertEqual((out["status"], out["filled"], out["matched"], out["mismatched"]), ("ok", ["INTERVIEW_TEST_COMPANIES", "MEGIBOW_BLOCK_ID"], 1, ["JIRA_CARD_BLOCK_ID"]))

    def test_the_counts_name_every_matched_setting_and_never_a_value(self):
        """a per-name 24 hour match list needs the NAMES"""
        env = {"HIRING_AUTO_CLOSE": "on", "JIRA_CARD_BLOCK_ID": ID, "MEGIBOW_BLOCK_ID": "", "RECRUITER_OUTREACH": "off"}
        out = run(env, Page(["MEGIBOW_BLOCK_ID " + ID, "JIRA_CARD_BLOCK_ID " + ID, "HIRING_AUTO_CLOSE on", "RECRUITER_OUTREACH on"]))
        self.assertEqual((out["matched"], out["matched_names"], out["mismatched"], out["filled"]), (2, ["HIRING_AUTO_CLOSE", "JIRA_CARD_BLOCK_ID"], ["RECRUITER_OUTREACH"], ["MEGIBOW_BLOCK_ID"]))
        self.assertEqual(out["matched"], len(out["matched_names"]))
        self.assertNotIn(ID, repr(out))  # names only
        self.assertEqual(run({}, Page(["HIRING_AUTO_CLOSE on"]))["matched_names"], [])  # nothing declared, nothing matched

    def test_safety_values_credentials_unknown_and_bad_addresses_are_never_applied(self):
        env = {"MEGIBOW_BLOCK_ID": "", "JIRA_CARD_BLOCK_ID": "", "AMAZON_CARD_BLOCK_ID": ""}
        out = run(env, Page(["V7_LIVE true", "ALERTS_ENABLED off", "CLOUDFLARE_API_TOKEN abc", "SOMETHING_NEW 1", "MEGIBOW_BLOCK_ID not-an-id", "JIRA_CARD_BLOCK_ID " + ID, "JIRA_CARD_BLOCK_ID " + ID, "AMAZON_CARD_BLOCK_ID " + ID]))
        self.assertEqual(env, {"MEGIBOW_BLOCK_ID": "", "JIRA_CARD_BLOCK_ID": "", "AMAZON_CARD_BLOCK_ID": ID})  # a repeated name is applied from neither row
        self.assertEqual((out["refused"], out["unknown"], out["invalid"], out["repeated"]), (["V7_LIVE", "ALERTS_ENABLED", "CLOUDFLARE_API_TOKEN"], 1, ["MEGIBOW_BLOCK_ID"], ["JIRA_CARD_BLOCK_ID"]))

    def test_an_unreadable_empty_or_unset_source_changes_nothing(self):
        env = {"HIRING_AUTO_CLOSE": "on"}
        self.assertEqual(run(env, Page([], broken=True))["status"], "unreadable:RUNTIME_CONFIG_UNREADABLE")
        self.assertEqual(run(env, Page([]))["status"], "unreadable:RUNTIME_CONFIG_EMPTY")  # never an empty override
        self.assertEqual(settings.apply({"HIRING_AUTO_CLOSE": "on", "LIFEOS_SETTINGS": "on"}, Page(["GRANOLA_AUTO on"]))["status"], "no_source_id")
        self.assertEqual(env, {"HIRING_AUTO_CLOSE": "on"})

    def test_a_step_holding_only_another_notion_token_reads_the_source_and_never_writes_anything(self):
        """a step's own token (the Interview, Amazon or Recruiters one) can read the source."""
        page, seen = Page(["GRANOLA_AUTO on"]), []

        def factory(env):
            seen.append(env["NOTION_API_TOKEN"])
            return page
        env = {"GITHUB_ACTIONS": "true", "V7_RUNTIME_CONFIG_SOURCE_ID": SOURCE, "NOTION_INTERVIEW_TOKEN": "i", "GRANOLA_AUTO": "", "AMAZON_CARD_BLOCK_ID": "3d63c5a05926800ab78affd56c135611"}
        with mock.patch("lifeos.platform.notion_client.Client", factory):
            out = settings.apply(env)
        self.assertEqual((out["status"], env["GRANOLA_AUTO"], seen, out["read_only_token"]), ("ok", "on", ["i"], "NOTION_INTERVIEW_TOKEN"))
        self.assertEqual(page.calls, [("POST", f"/data_sources/{SOURCE}/query")])
        self.assertNotIn("seeded", out)

    def test_the_jira_or_api_token_is_still_preferred(self):
        page, seen = Page(["GRANOLA_AUTO on"]), []

        def factory(env):
            seen.append(env["NOTION_API_TOKEN"])
            return page
        env = {"GITHUB_ACTIONS": "true", "V7_RUNTIME_CONFIG_SOURCE_ID": SOURCE, "NOTION_INTERVIEW_TOKEN": "i", "NOTION_API_TOKEN": "a", "AMAZON_CARD_BLOCK_ID": "3d63c5a05926800ab78affd56c135611"}
        with mock.patch("lifeos.platform.notion_client.Client", factory):
            out = settings.apply(env, connect=connect)
        self.assertEqual((seen, "read_only_token" in out), (["a"], False))

    def test_only_a_notion_token_name_counts_as_a_token(self):
        env = {"GITHUB_ACTIONS": "true", "NOTION_TOKENIZER": "x", "TINYFISH_API_KEY": "y", "NOTION_AMAZON_DATA_SOURCE_ID": ""}
        self.assertEqual(run(env)["status"], "no_token")

    def test_a_private_plain_value_written_to_the_job_environment_is_masked_but_a_switch_is_not(self):
        """the repository is public."""
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = folder + "/env"
            env = {"GITHUB_ACTIONS": "true", "V7_RUNTIME_CONFIG_SOURCE_ID": SOURCE, "GITHUB_ENV": path, "JIRA_BOARDS": "", "JIRA_GTV_EPIC": "", "GRANOLA_AUTO": ""}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                settings.main([], env, Page(["JIRA_BOARDS JFM:5,MEJ:38:all", "JIRA_GTV_EPIC JFM-165", "GRANOLA_AUTO on"]))
            written = open(path).read()
        printed = out.getvalue()
        self.assertIn("::add-mask::JFM:5,MEJ:38:all", printed)
        self.assertIn("::add-mask::JFM-165", printed)
        self.assertNotIn("::add-mask::on", printed)  # a switch is never masked
        self.assertEqual(sorted(written.strip().split("\n")), ["GRANOLA_AUTO=on", "JIRA_BOARDS=JFM:5,MEJ:38:all", "JIRA_GTV_EPIC=JFM-165"])

    def test_off_outside_production_and_without_a_token(self):
        self.assertEqual(run({"LIFEOS_SETTINGS": "off"}, Page(["GRANOLA_AUTO on"]))["status"], "off")
        self.assertEqual(run({"NOTION_API_TOKEN": "t"})["status"], "off")
        self.assertEqual(run({"GITHUB_ACTIONS": "true"})["status"], "no_token")


class OldPageRetired(unittest.TestCase):
    """the old settings page reader is gone."""
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch("lifeos.platform.stored_pages.STORE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_old_reader_and_its_seeding_no_longer_exist(self):
        for gone in ("TITLE", "read", "parse", "seed", "wanted", "wanted_settings", "SEEDABLE", "SettingsError", "_blocks"):
            self.assertFalse(hasattr(settings, gone), gone)
        text = (ROOT / "lifeos/platform/settings.py").read_text()
        self.assertNotIn("V7 settings (machine read)", text)
        self.assertNotIn('"/search"', text)

    def test_a_good_read_touches_only_the_source_by_id_and_writes_nothing_to_notion(self):
        page = Page(["GRANOLA_AUTO on"])
        env = {"GRANOLA_AUTO": ""}
        out = run(env, page, connect=connect)
        self.assertEqual((out["status"], out["source"], env["GRANOLA_AUTO"], page.calls), ("ok", "new", "on", [("POST", f"/data_sources/{SOURCE}/query")]))
        self.assertNotIn("seeded", out)

    def test_recovery_is_the_last_accepted_map_when_the_source_is_unreadable_and_never_the_old_page(self):
        run({}, Page(["GRANOLA_AUTO on", f"JIRA_CARD_BLOCK_ID {ID}"]), connect=connect)
        for label, client, env in (("broken", Page([], broken=True), {}), ("empty", Page([]), {}), ("unset id", Page(["GRANOLA_AUTO off"]), {"LIFEOS_SETTINGS": "on"})):
            env = {**env, "GRANOLA_AUTO": "", "JIRA_CARD_BLOCK_ID": ""}
            out = settings.apply(env, client, connect=connect) if label == "unset id" else run(env, client, connect=connect)
            self.assertEqual((env["GRANOLA_AUTO"], env["JIRA_CARD_BLOCK_ID"]), ("on", ID), label)  # the last accepted values, not the unset id's "off"
            self.assertRegex(out["last_accepted"], r"^\d+/\d+ · \d+:\d\d [AP]M CT$")
            self.assertTrue(out["status"].startswith("unreadable:") or out["status"] == "no_source_id", label)
            self.assertNotIn("source", out, label)  # the receipt never claims the new source

    def test_no_saved_map_and_no_source_leaves_the_environment_as_it_was(self):
        env = {"GRANOLA_AUTO": "off", "LIFEOS_SETTINGS": "on"}
        self.assertEqual((settings.apply(env, Page(["GRANOLA_AUTO on"]), connect=connect)["status"], env["GRANOLA_AUTO"]), ("no_source_id", "off"))


if __name__ == "__main__":
    unittest.main()
