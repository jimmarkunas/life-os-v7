"""LIFE-699: V7 settings from one Notion page (shadow rule), and no new GitHub value."""
import contextlib
import re
import unittest
from pathlib import Path
from unittest import mock

from lifeos.platform import settings

ROOT = Path(__file__).resolve().parents[2]
ID = "4183c5a0-5926-81be-9e19-e85714dc256f"

# Every GitHub secret or variable the workflows use (Nov 14, 2026; LIFE-703 removed 14 names that were never set in GitHub: their steps now declare them empty for the settings page to fill). This list may only shrink: LIFE-699 moves each one to the Notion settings page or Bitwarden,
# and the end state is LIFEOS_BWS_RUNTIME_TOKEN alone (GITHUB_TOKEN is GitHub's own and never counted). Adding a name here needs Jim's typed approval.
FROZEN = frozenset({"LIFEOS_BWS_RUNTIME_TOKEN"})


def used():
    return {m.group(2) for path in (ROOT / ".github").rglob("*.yml") for m in re.finditer(r"\b(vars|secrets)\.([A-Z0-9_]+)", path.read_text())} - {"GITHUB_TOKEN"}


class NoNewGitHubValue(unittest.TestCase):
    def test_no_workflow_uses_a_github_value_outside_the_frozen_list(self):
        self.assertEqual(sorted(used() - FROZEN), [], "put a new setting on the Notion page 'V7 settings (machine read)' (lifeos/platform/settings.py) or in Bitwarden, never in GitHub")

    def test_the_frozen_list_only_shrinks(self):
        self.assertEqual(sorted(FROZEN - used()), [], "a name the workflows no longer use: delete it from FROZEN (and from GitHub, with Jim's yes)")

    def test_a_safety_value_is_never_movable(self):
        self.assertEqual(settings.SAFETY & settings.MOVABLE, frozenset())


class Page:
    def __init__(self, lines, title=settings.TITLE, broken=False):
        self.lines, self.title, self.broken = lines, title, broken

    def call(self, method, path, body=None):
        if self.broken:
            raise RuntimeError("down")
        if path == "/search":
            return {"results": [{"id": "p", "properties": {"title": {"type": "title", "title": [{"plain_text": self.title}]}}}]}
        if path.startswith("/blocks/p/children"):
            return {"results": [{"id": "c", "type": "code", "code": {"rich_text": [{"plain_text": "\n".join(self.lines)}]}}], "has_more": False}
        raise AssertionError(path)


class Writable(Page):
    """A page with a switches block and an addresses block that accepts the copy."""
    def __init__(self, switches, addresses):
        super().__init__([])
        self.blocks = {"s": "\n".join(["# Switches and lists"] + switches), "a": "\n".join(["# Notion addresses"] + addresses)}
        self.patches = 0

    def call(self, method, path, body=None):
        if path.startswith("/blocks/p/children"):
            return {"results": [{"id": k, "type": "code", "code": {"language": "plain text", "rich_text": [{"plain_text": v}]}} for k, v in self.blocks.items()], "has_more": False}
        return super().call(method, path, body)

    def call_once(self, method, path, body=None):
        assert method == "PATCH" and path in ("/blocks/a", "/blocks/s"), path     # only the addresses and switches blocks are ever written
        self.blocks[path[-1]] = "".join(p["text"]["content"] for p in body["code"]["rich_text"])
        self.patches += 1


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
        self.assertTrue(settings.apply({}, page, connect=connect)["remembered"])
        self.assertFalse(settings.apply({}, page, connect=connect)["remembered"])
        self.assertEqual((self.store.saves, self.store.rows[settings.SNAPSHOT_KEY]["values"]), (1, {"GRANOLA_AUTO": "on", "JIRA_CARD_BLOCK_ID": ID}))

    def test_an_unreadable_page_falls_back_to_the_last_accepted_map_and_says_so(self):
        settings.apply({}, Page(["GRANOLA_AUTO on", f"JIRA_CARD_BLOCK_ID {ID}"]), connect=connect)
        self.store.rows[settings.SNAPSHOT_KEY]["values"]["V7_LIVE"] = "false"             # a safety value that got into the store is never restored
        env = {"GRANOLA_AUTO": "", "JIRA_CARD_BLOCK_ID": "", "V7_LIVE": ""}
        out = settings.apply(env, Page([], broken=True), connect=connect)
        self.assertEqual(env, {"GRANOLA_AUTO": "on", "JIRA_CARD_BLOCK_ID": ID, "V7_LIVE": ""})
        self.assertEqual((out["status"], out["filled"]), ("unreadable:RuntimeError", ["GRANOLA_AUTO", "JIRA_CARD_BLOCK_ID"]))
        self.assertRegex(out["last_accepted"], r"^\d+/\d+ · \d+:\d\d [AP]M CT$")

    def test_no_saved_map_keeps_todays_behavior(self):
        env = {"GRANOLA_AUTO": ""}
        out = settings.apply(env, Page([], broken=True), connect=connect)
        self.assertEqual((env, "last_accepted" in out), ({"GRANOLA_AUTO": ""}, False))

    def test_a_step_without_a_notion_token_still_gets_the_last_accepted_map(self):
        settings.apply({}, Page(["GRANOLA_AUTO on"]), connect=connect)
        env = {"GITHUB_ACTIONS": "true", "GRANOLA_AUTO": ""}
        out = settings.apply(env, connect=connect)
        self.assertEqual((out["status"], env["GRANOLA_AUTO"]), ("no_token", "on"))

    def test_a_broken_store_never_fails_the_run(self):
        @contextlib.contextmanager
        def down():
            raise RuntimeError("STORE_SSH_TCP_TIMEOUT")
            yield
        out = settings.apply({}, Page(["GRANOLA_AUTO on"]), connect=down)
        self.assertEqual((out["status"], out["remember_status"]), ("ok", "STORE_SSH_TCP_TIMEOUT"))


class DeclaredOnly(unittest.TestCase):
    def test_a_name_the_step_never_declares_is_never_filled(self):
        env = {"GRANOLA_AUTO": ""}
        out = settings.apply(env, Page(["GRANOLA_AUTO on", "HIRING_AUTO_CLOSE true"]))
        self.assertEqual((env, out["filled"], out["undeclared"]), ({"GRANOLA_AUTO": "on"}, ["GRANOLA_AUTO"], 1))


class CopyAddresses(unittest.TestCase):
    def test_a_production_run_copies_the_addresses_it_holds_once_and_never_a_switch(self):
        page = Writable(["HIRING_AUTO_CLOSE on"], [f"JIRA_CARD_BLOCK_ID {ID}"])                # the step does not declare HIRING_AUTO_CLOSE: never filled
        env = {"JIRA_CARD_BLOCK_ID": ID, "BILLS_CARD_BLOCK_ID": ID.replace("4", "5"), "NOTION_BILLS_DATA_SOURCE_ID": "collection://" + ID, "GRANOLA_AUTO": "on",
               "CLOUDFLARE_API_TOKEN": "secret", "LIFEOS_NOTION_IDS": '{"DASHBOARD_PAGE": "%s", "bad": "x"}' % ID.replace("-", ""), "AMAZON_CARD_BLOCK_ID": "not-an-id"}
        out = settings.apply(env, page, copy=True)
        self.assertEqual(out["seeded"], ["BILLS_CARD_BLOCK_ID", "LIFEOS_NOTION_DASHBOARD_PAGE", "NOTION_BILLS_DATA_SOURCE_ID"])
        self.assertNotIn("GRANOLA_AUTO", page.blocks["a"])
        self.assertNotIn("secret", page.blocks["a"] + page.blocks["s"])
        again = settings.apply(env, page, copy=True)
        self.assertEqual((again["seeded"], page.patches, again["matched"], again["mismatched"], again["undeclared"]), ([], 1, 4, [], 1))
        self.assertNotIn("HIRING_AUTO_CLOSE", env)

    def test_the_non_address_settings_are_copied_once_to_the_switches_block_one_line_each(self):
        """LIFE-699 phase 7: names only in the counts; a credential, an unlisted name and a value with a line break are never copied."""
        link = "https://www.notion.so/Hiring-Pipeline#" + ID.replace("-", "").upper()
        page = Writable(["GRANOLA_AUTO on"], [])
        env = {"JIRA_SITE_URL": "https://example.com", "JIRA_BOARDS": "A,B", "BUZZ_STAGE_CHANNEL": "{\"a\": 1}", "JIRA_GTV_EPIC": "{\n\"a\": 1}",
               "GCAL_CALENDAR_ID": "  ", "CLOUDFLARE_API_TOKEN": "secret", "HIRING_CARD_BLOCK_ID": link}
        out = settings.apply(env, page, copy=True)
        self.assertEqual(out["seeded"], ["HIRING_CARD_BLOCK_ID", "BUZZ_STAGE_CHANNEL", "JIRA_BOARDS", "JIRA_SITE_URL"])
        self.assertEqual(page.blocks["s"].splitlines()[1:], ["GRANOLA_AUTO on", 'BUZZ_STAGE_CHANNEL {"a": 1}', "JIRA_BOARDS A,B", "JIRA_SITE_URL https://example.com"])
        self.assertIn("HIRING_CARD_BLOCK_ID " + ID.replace("-", "").lower(), page.blocks["a"])         # a copied Notion link is written as its bare block id
        self.assertNotIn("secret", page.blocks["a"] + page.blocks["s"])
        again = settings.apply(env, page, copy=True)
        self.assertEqual((again["seeded"], page.patches, again["mismatched"]), ([], 2, []))           # nothing left to copy; the link and the bare id compare equal

    def test_the_seedable_names_are_settings_the_page_may_already_hold(self):
        self.assertLessEqual(settings.SEEDABLE, settings.MOVABLE)
        self.assertEqual(settings.SEEDABLE & settings.SAFETY, frozenset())
        self.assertFalse([n for n in settings.SEEDABLE if n.endswith(("_TOKEN", "_SECRET", "_KEY", "_PASSWORD"))])

    def test_a_step_that_declares_the_notion_ids_map_receives_its_names_from_the_page(self):
        env = {"LIFEOS_NOTION_IDS": ""}
        out = settings.apply(env, Page([f"LIFEOS_NOTION_DASHBOARD_PAGE {ID}", f"MEGIBOW_BLOCK_ID {ID}"]))
        self.assertEqual((env, out["filled"], out["undeclared"]), ({"LIFEOS_NOTION_IDS": "", "LIFEOS_NOTION_DASHBOARD_PAGE": ID}, ["LIFEOS_NOTION_DASHBOARD_PAGE"], 1))

    def test_the_notion_ids_map_counts_as_held(self):
        env = {"LIFEOS_NOTION_IDS": '{"DASHBOARD_PAGE": "%s"}' % ID, "LIFEOS_NOTION_REVIEW_SOURCE": ""}
        out = settings.apply(env, Page([f"LIFEOS_NOTION_DASHBOARD_PAGE {ID}", f"LIFEOS_NOTION_REVIEW_SOURCE {ID}"]))
        self.assertEqual((out["matched"], out["filled"]), (1, ["LIFEOS_NOTION_REVIEW_SOURCE"]))

    def test_a_copy_that_cannot_be_written_never_fails_the_run(self):
        page = Writable([], [])
        page.blocks.pop("a")
        out = settings.apply({"JIRA_CARD_BLOCK_ID": ID}, page, copy=True)
        self.assertEqual((out["status"], out["seed_status"]), ("ok", "SETTINGS_SEED_TARGET_MISSING"))

    def test_tests_and_local_runs_never_copy(self):
        page = Writable([], [])
        settings.apply({"JIRA_CARD_BLOCK_ID": ID}, page)
        self.assertEqual(page.patches, 0)


class ShadowRule(unittest.TestCase):
    def test_notion_fills_only_what_the_environment_lacks_and_compares_the_rest(self):
        env = {"HIRING_AUTO_CLOSE": "on", "JIRA_CARD_BLOCK_ID": "x" * 32, "MEGIBOW_BLOCK_ID": "", "INTERVIEW_TEST_COMPANIES": ""}
        page = Page(["# addresses", f"MEGIBOW_BLOCK_ID {ID}", f"JIRA_CARD_BLOCK_ID {ID}", "# switches", "HIRING_AUTO_CLOSE on", "INTERVIEW_TEST_COMPANIES Smartsheet, Walmart"])
        out = settings.apply(env, page)
        self.assertEqual(env["MEGIBOW_BLOCK_ID"], ID)
        self.assertEqual(env["INTERVIEW_TEST_COMPANIES"], "Smartsheet, Walmart")
        self.assertEqual(env["JIRA_CARD_BLOCK_ID"], "x" * 32)                   # GitHub still holds it: never overwritten, only compared
        self.assertEqual((out["status"], out["filled"], out["matched"], out["mismatched"]), ("ok", ["INTERVIEW_TEST_COMPANIES", "MEGIBOW_BLOCK_ID"], 1, ["JIRA_CARD_BLOCK_ID"]))

    def test_the_counts_name_every_matched_setting_and_never_a_value(self):
        """LIFE-703: a per-name 24 hour match list needs the NAMES; every older key keeps its meaning and no value reaches the counts."""
        env = {"HIRING_AUTO_CLOSE": "on", "JIRA_CARD_BLOCK_ID": ID, "MEGIBOW_BLOCK_ID": "", "RECRUITER_OUTREACH": "off"}
        out = settings.apply(env, Page(["MEGIBOW_BLOCK_ID " + ID, "JIRA_CARD_BLOCK_ID " + ID, "HIRING_AUTO_CLOSE on", "RECRUITER_OUTREACH on"]))
        self.assertEqual((out["matched"], out["matched_names"], out["mismatched"], out["filled"]), (2, ["HIRING_AUTO_CLOSE", "JIRA_CARD_BLOCK_ID"], ["RECRUITER_OUTREACH"], ["MEGIBOW_BLOCK_ID"]))
        self.assertEqual(out["matched"], len(out["matched_names"]))
        self.assertNotIn(ID, repr(out))                                          # names only
        self.assertEqual(settings.apply({}, Page(["HIRING_AUTO_CLOSE on"]))["matched_names"], [])      # nothing declared, nothing matched

    def test_safety_values_credentials_unknown_and_bad_addresses_are_never_applied(self):
        env = {"MEGIBOW_BLOCK_ID": ""}
        out = settings.apply(env, Page(["V7_LIVE true", "ALERTS_ENABLED off", "CLOUDFLARE_API_TOKEN abc", "SOMETHING_NEW 1", "MEGIBOW_BLOCK_ID not-an-id", "MEGIBOW_BLOCK_ID " + ID, "MEGIBOW_BLOCK_ID " + ID]))
        self.assertEqual(env, {"MEGIBOW_BLOCK_ID": ID})
        self.assertEqual((out["refused"], out["unknown"], out["invalid"], out["repeated"]), (["V7_LIVE", "ALERTS_ENABLED", "CLOUDFLARE_API_TOKEN"], 1, ["MEGIBOW_BLOCK_ID"], ["MEGIBOW_BLOCK_ID"]))

    def test_an_unreadable_or_missing_page_changes_nothing(self):
        env = {"HIRING_AUTO_CLOSE": "on"}
        self.assertEqual(settings.apply(env, Page([], broken=True))["status"], "unreadable:RuntimeError")
        self.assertEqual(settings.apply(env, Page(["GRANOLA_AUTO on"], title="Something else"))["status"], "unreadable:SETTINGS_PAGE_NOT_FOUND")
        self.assertEqual(env, {"HIRING_AUTO_CLOSE": "on"})

    def test_a_step_holding_only_another_notion_token_reads_the_page_but_never_writes_it(self):
        """LIFE-699: the root page is shared with every connection, so a step's own token (the Interview, Amazon or Recruiters one) can read the settings page. It only reads: it never seeds."""
        page, seen = Writable(["GRANOLA_AUTO on"], []), []

        def factory(env):
            seen.append(env["NOTION_API_TOKEN"])
            return page
        env = {"GITHUB_ACTIONS": "true", "NOTION_INTERVIEW_TOKEN": "i", "GRANOLA_AUTO": "", "AMAZON_CARD_BLOCK_ID": "3d63c5a05926800ab78affd56c135611"}
        with mock.patch("lifeos.platform.notion_client.Client", factory):
            out = settings.apply(env)
        self.assertEqual((out["status"], env["GRANOLA_AUTO"], seen, page.patches, "seeded" in out, out["read_only_token"]), ("ok", "on", ["i"], 0, False, "NOTION_INTERVIEW_TOKEN"))

    def test_the_jira_or_api_token_is_still_preferred_and_may_seed(self):
        page, seen = Writable(["GRANOLA_AUTO on"], []), []

        def factory(env):
            seen.append(env["NOTION_API_TOKEN"])
            return page
        env = {"GITHUB_ACTIONS": "true", "NOTION_INTERVIEW_TOKEN": "i", "NOTION_API_TOKEN": "a", "AMAZON_CARD_BLOCK_ID": "3d63c5a05926800ab78affd56c135611"}
        with mock.patch("lifeos.platform.notion_client.Client", factory):
            out = settings.apply(env)
        self.assertEqual((seen, "read_only_token" in out, page.patches > 0), (["a"], False, True))

    def test_only_a_notion_token_name_counts_as_a_token(self):
        env = {"GITHUB_ACTIONS": "true", "NOTION_TOKENIZER": "x", "TINYFISH_API_KEY": "y", "NOTION_AMAZON_DATA_SOURCE_ID": ""}
        self.assertEqual(settings.apply(env)["status"], "no_token")

    def test_a_private_plain_value_written_to_the_job_environment_is_masked_but_a_switch_is_not(self):
        """LIFE-699: the repository is public. The Jira boards, the epic and the other private plain settings were masked secrets, so the settings step masks them when it exports them; a switch such as `on` is never masked (it would empty every job output)."""
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = folder + "/env"
            env = {"GITHUB_ACTIONS": "true", "GITHUB_ENV": path, "JIRA_BOARDS": "", "JIRA_GTV_EPIC": "", "GRANOLA_AUTO": ""}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                settings.main([], env, Page(["JIRA_BOARDS JFM:5,MEJ:38:all", "JIRA_GTV_EPIC JFM-165", "GRANOLA_AUTO on"]))
            written = open(path).read()
        printed = out.getvalue()
        self.assertIn("::add-mask::JFM:5,MEJ:38:all", printed)
        self.assertIn("::add-mask::JFM-165", printed)
        self.assertNotIn("::add-mask::on", printed)                                       # a switch is never masked
        self.assertEqual(sorted(written.strip().split("\n")), ["GRANOLA_AUTO=on", "JIRA_BOARDS=JFM:5,MEJ:38:all", "JIRA_GTV_EPIC=JFM-165"])

    def test_off_outside_production_and_without_a_token(self):
        self.assertEqual(settings.apply({"LIFEOS_SETTINGS": "off"}, Page(["GRANOLA_AUTO on"]))["status"], "off")
        self.assertEqual(settings.apply({"NOTION_API_TOKEN": "t"})["status"], "off")             # not GitHub Actions, not switched on: no Notion call at all
        self.assertEqual(settings.apply({"GITHUB_ACTIONS": "true"})["status"], "no_token")


if __name__ == "__main__":
    unittest.main()
