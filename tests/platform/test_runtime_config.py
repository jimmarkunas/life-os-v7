"""LIFE-844 (synthetic): the stable-ID Runtime Configuration reader. Fixtures only: no Notion, no database, no workflow, no live write."""
import unittest
from pathlib import Path
from unittest import mock

from lifeos.platform import runtime_config as rc
from lifeos.platform import settings
from tests.platform.test_settings import FakeStore, Page, connect

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "5a1c0e57-3b2d-4f6a-9c1d-0e2f3a4b5c6d"
OTHER = "[removed]"           # the Product Requirements database: the reader must never touch it
ID = "[removed]"
ID2 = "[removed]"
SECRET = "synthetic-sentinel-value-7731"
ENV = {rc.SOURCE_NAME: SOURCE}


def row(name, value, kind=None, revision=1, active=True, **skip):
    kind = kind or ("address" if settings.is_address(name) else "switch")
    props = {
        "Name": {"type": "title", "title": [{"plain_text": name}]},
        "Value": {"type": "rich_text", "rich_text": [{"plain_text": value}] if value else []},
        "Kind": {"type": "select", "select": {"name": kind}},
        "Revision": {"type": "number", "number": revision},
        "Active": {"type": "checkbox", "checkbox": active},
    }
    for key in skip:
        props.pop(key)
    return {"id": f"row-{name}", "properties": props}


class Source:
    """A data source addressed by ID. Anything else (a title search, another source, a write) fails the test."""
    def __init__(self, rows, pages=1, broken=False, title="Runtime Configuration"):
        self.rows, self.pages, self.broken, self.title, self.calls = rows, pages, broken, title, []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if self.broken:
            raise RuntimeError(SECRET)
        assert (method, path) == ("POST", f"/data_sources/{SOURCE}/query"), (method, path)
        start = int((body or {}).get("start_cursor") or 0)
        size = -(-len(self.rows) // self.pages) or 1
        chunk = self.rows[start:start + size]
        more = start + size < len(self.rows)
        return {"results": chunk, "has_more": more, "next_cursor": str(start + size) if more else None}


OLD_LINES = ["# notion addresses", f"JIRA_CARD_BLOCK_ID {ID}", f"MEGIBOW_BLOCK_ID {ID2}", "# switches and lists", "GRANOLA_AUTO on", "INTERVIEW_TEST_COMPANIES Smartsheet, Walmart"]
NEW_ROWS = [row("JIRA_CARD_BLOCK_ID", ID), row("MEGIBOW_BLOCK_ID", ID2), row("GRANOLA_AUTO", "on"), row("INTERVIEW_TEST_COMPANIES", "Smartsheet, Walmart")]


class ReadByStableId(unittest.TestCase):
    def test_typed_rows_resolve_by_id_with_no_title_search_and_no_product_requirements_access(self):
        client = Source(NEW_ROWS)
        found, counts = rc.read(client, ENV)
        self.assertEqual(found, settings.parse(OLD_LINES)[0])
        self.assertEqual(client.calls, [("POST", f"/data_sources/{SOURCE}/query")])          # only the configured source; never /search; never OTHER
        self.assertNotIn(OTHER, repr(client.calls))
        self.assertEqual((counts["refused"], counts["unknown"], counts["invalid"], counts["repeated"], counts["inactive"]), ([], 0, [], [], 0))

    def test_a_retitled_source_still_reads_and_a_retitled_settings_page_does_not(self):
        self.assertEqual(rc.read(Source(NEW_ROWS, title="Renamed config"), ENV)[0]["GRANOLA_AUTO"], "on")
        with self.assertRaises(settings.SettingsError) as raised:
            settings.read(Page(OLD_LINES, title="Product Requirements moved page"))
        self.assertEqual(str(raised.exception), "SETTINGS_PAGE_NOT_FOUND")

    def test_a_source_of_several_pages_is_read_in_full(self):
        self.assertEqual(rc.read(Source(NEW_ROWS, pages=3), ENV)[0], settings.parse(OLD_LINES)[0])

    def test_the_bootstrap_id_is_required_and_must_look_like_an_address(self):
        for env, code in (({}, "RUNTIME_CONFIG_SOURCE_ID_MISSING"), ({rc.SOURCE_NAME: "not-an-id"}, "RUNTIME_CONFIG_SOURCE_ID_INVALID")):
            with self.assertRaises(rc.RuntimeConfigError) as raised:
                rc.source_id(env)
            self.assertEqual(str(raised.exception), code)
        self.assertEqual(rc.source_id({rc.SOURCE_NAME: f"collection://{SOURCE}"}), SOURCE)

    def test_the_reader_names_no_title_and_does_not_search(self):
        text = (ROOT / "lifeos/platform/runtime_config.py").read_text()
        code = "\n".join(line.split("#")[0] for line in text.splitlines() if not line.lstrip().startswith(('"""', "Why:", "Rows:")))
        self.assertNotIn("/search", code)
        self.assertNotIn("settings.TITLE", code)

    def test_the_new_bootstrap_name_is_registered_and_the_old_reader_ignores_it(self):
        self.assertIn(rc.SOURCE_NAME + " ", (ROOT / "lifeos/platform/config.txt").read_text())
        plain, with_id = {"GRANOLA_AUTO": ""}, {"GRANOLA_AUTO": "", rc.SOURCE_NAME: SOURCE}
        self.assertEqual(settings.apply(plain, Page(OLD_LINES)), settings.apply(with_id, Page(OLD_LINES)))


class RefusedNeverApplied(unittest.TestCase):
    def test_each_bad_row_is_counted_by_name_and_none_is_applied(self):
        rows = [
            row("V7_LIVE", "on"), row("V7_CLOUD_FIT", "on"), row("NOTION_JIRA_TOKEN", SECRET), row("ALERT_SETTINGS", SECRET),          # safety and credential-shaped
            row("NOT_A_SETTING", SECRET),                                                                                      # unknown
            row("JIRA_CARD_BLOCK_ID", "not-an-address"), row("GRANOLA_AUTO", "on", kind="address"), row("HIRING_AUTO_CLOSE", "on", revision=-1),
            row("RECRUITER_OUTREACH", "a\nb"), row("JOB_PAGE_OUTREACH", ""),                                                  # bad address, wrong kind, bad revision, line break, empty
            row("MAIL_PORTAL_URL", "x", Revision=None),                                                                       # a property missing
            row("INTERVIEW_LEADS", "on", active=False),                                                                       # inactive: skipped, counted
            row("TICK_MAX_PARALLEL", "2"),
        ]
        found, counts = rc.parse(rows)
        self.assertEqual(found, {"TICK_MAX_PARALLEL": "2"})
        self.assertEqual(sorted(counts["refused"]), ["ALERT_SETTINGS", "NOTION_JIRA_TOKEN", "V7_CLOUD_FIT", "V7_LIVE"])
        self.assertEqual(sorted(counts["invalid"]), ["GRANOLA_AUTO", "HIRING_AUTO_CLOSE", "JIRA_CARD_BLOCK_ID", "JOB_PAGE_OUTREACH", "RECRUITER_OUTREACH"])
        self.assertEqual((counts["unknown"], counts["inactive"]), (2, 1))
        self.assertNotIn(SECRET, repr((found, counts)))                                                                       # names and counts only

    def test_a_duplicate_active_name_is_applied_from_neither_row_but_an_inactive_copy_is_fine(self):
        found, counts = rc.parse([row("GRANOLA_AUTO", "on"), row("GRANOLA_AUTO", "off"), row("HIRING_AUTO_CLOSE", "on"), row("HIRING_AUTO_CLOSE", "off", active=False)])
        self.assertEqual((found, counts["repeated"]), ({"HIRING_AUTO_CLOSE": "on"}, ["GRANOLA_AUTO"]))

    def test_a_safety_name_is_never_movable_here_either(self):
        self.assertEqual(settings.SAFETY & {r["properties"]["Name"]["title"][0]["plain_text"] for r in NEW_ROWS}, set())
        self.assertEqual(rc.export_rows({"V7_LIVE": "on", "GRANOLA_AUTO": "on"}), [{"Name": "GRANOLA_AUTO", "Value": "on", "Kind": "switch", "Revision": 1, "Active": True}])


class DualReadParity(unittest.TestCase):
    ENVS = ({"GRANOLA_AUTO": "", "MEGIBOW_BLOCK_ID": "", "JIRA_CARD_BLOCK_ID": ID, "INTERVIEW_TEST_COMPANIES": "Other"}, {})

    def test_the_candidate_source_equals_the_page_value_for_value(self):
        old, new = settings.parse(OLD_LINES)[0], rc.read(Source(NEW_ROWS), ENV)[0]
        self.assertEqual(rc.compare(old, new), {"matched": 4, "mismatched": [], "only_old": [], "only_new": [], "blocking": False})
        self.assertEqual(rc.compare(old, {"JIRA_CARD_BLOCK_ID": ID.replace("-", "").upper(), **{k: v for k, v in new.items() if k != "JIRA_CARD_BLOCK_ID"}})["blocking"], False)   # an address compares as the same id

    def test_the_shadow_rule_gives_the_same_environment_and_counts_from_either_source(self):
        for env in self.ENVS:
            from_page, from_source = dict(env), {**env, rc.SOURCE_NAME: SOURCE}
            page_out, new_out = settings.apply(from_page, Page(OLD_LINES)), rc.apply(from_source, Source(NEW_ROWS))
            from_source.pop(rc.SOURCE_NAME)
            self.assertEqual(from_page, from_source)
            for key in ("filled", "matched", "matched_names", "mismatched", "undeclared"):
                self.assertEqual(page_out.get(key), new_out.get(key), key)

    def test_environment_after_applying_each_source_is_identical(self):
        env_page, env_new = {"GRANOLA_AUTO": "", "MEGIBOW_BLOCK_ID": "", "INTERVIEW_TEST_COMPANIES": "Other"}, {"GRANOLA_AUTO": "", "MEGIBOW_BLOCK_ID": "", "INTERVIEW_TEST_COMPANIES": "Other", rc.SOURCE_NAME: SOURCE}
        settings.apply(env_page, Page(OLD_LINES))
        rc.apply(env_new, Source(NEW_ROWS))
        env_new.pop(rc.SOURCE_NAME)
        self.assertEqual(env_page, env_new)
        self.assertEqual(env_page["INTERVIEW_TEST_COMPANIES"], "Other")                  # GitHub still holds it: compared, never overwritten

    def test_exporting_the_page_values_round_trips_through_the_new_reader(self):
        old = settings.parse(OLD_LINES)[0]
        exported = [row(r["Name"], r["Value"], r["Kind"], r["Revision"], r["Active"]) for r in rc.export_rows(old)]
        self.assertEqual(rc.compare(old, rc.parse(exported)[0])["blocking"], False)

    def test_any_difference_blocks_activation(self):
        old = settings.parse(OLD_LINES)[0]
        self.assertEqual(rc.compare(old, {**old, "GRANOLA_AUTO": "off"})["mismatched"], ["GRANOLA_AUTO"])
        self.assertEqual(rc.compare(old, {k: v for k, v in old.items() if k != "GRANOLA_AUTO"})["only_old"], ["GRANOLA_AUTO"])
        self.assertEqual(rc.compare(old, {**old, "TICK_MAX_PARALLEL": "2"})["only_new"], ["TICK_MAX_PARALLEL"])
        for changed in ({**old, "GRANOLA_AUTO": "off"}, {**old, "TICK_MAX_PARALLEL": "2"}):
            self.assertTrue(rc.compare(old, changed)["blocking"])


class FallbackAndRollback(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch("lifeos.platform.stored_pages.STORE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def remembered(self):
        settings.apply({}, Page(OLD_LINES), connect=connect)                              # today's reader leaves the last accepted map
        return dict(self.store.rows)

    def test_an_unreadable_source_falls_back_to_the_last_accepted_map_and_says_so(self):
        self.remembered()
        env = {"GRANOLA_AUTO": "", "V7_LIVE": "", rc.SOURCE_NAME: SOURCE}
        out = rc.apply(env, Source(NEW_ROWS, broken=True), connect=connect)
        self.assertEqual((out["status"], env["GRANOLA_AUTO"], env["V7_LIVE"]), ("unreadable:RUNTIME_CONFIG_UNREADABLE", "on", ""))
        self.assertRegex(out["last_accepted"], r"CT$")
        self.assertNotIn(SECRET, repr(out))                                               # the failure's own text never reaches the receipt

    def test_a_malformed_or_empty_source_is_never_applied_as_an_empty_override(self):
        self.remembered()
        for rows in ([], [row("V7_LIVE", "on")], [row("GRANOLA_AUTO", "on", active=False)], [{"id": "x", "properties": {}}]):
            env = {"GRANOLA_AUTO": "", rc.SOURCE_NAME: SOURCE}
            out = rc.apply(env, Source(rows), connect=connect)
            self.assertEqual((out["status"], env["GRANOLA_AUTO"]), ("unreadable:RUNTIME_CONFIG_EMPTY", "on"))      # the old map, not a blank

    def test_no_saved_map_or_no_store_access_leaves_the_environment_as_it_was(self):
        for kwargs in ({"connect": connect}, {}):
            env = {"GRANOLA_AUTO": "", rc.SOURCE_NAME: SOURCE}
            out = rc.apply(env, Source(NEW_ROWS, broken=True), **kwargs)
            self.assertEqual((env["GRANOLA_AUTO"], "last_accepted" in out), ("", False))

    def test_a_missing_bootstrap_changes_nothing_and_a_bad_one_falls_back(self):
        env = {"GRANOLA_AUTO": ""}
        client = Source(NEW_ROWS)
        self.assertEqual(rc.apply(env, client)["status"], "off")
        self.assertEqual((env, client.calls), ({"GRANOLA_AUTO": ""}, []))                   # no read at all
        self.remembered()
        bad = {"GRANOLA_AUTO": "", rc.SOURCE_NAME: "nope"}
        self.assertEqual((rc.apply(bad, client, connect=connect)["status"], bad["GRANOLA_AUTO"]), ("unreadable:RUNTIME_CONFIG_SOURCE_ID_INVALID", "on"))

    def test_a_safety_name_in_the_saved_map_is_still_never_applied(self):
        self.store.rows[settings.SNAPSHOT_KEY] = {"taken_at": "2026-11-19T12:00:00+00:00", "schema": 1, "values": {"V7_LIVE": "on", "GRANOLA_AUTO": "on"}}
        env = {"V7_LIVE": "", "GRANOLA_AUTO": "", rc.SOURCE_NAME: SOURCE}
        rc.apply(env, Source([], broken=True), connect=connect)
        self.assertEqual((env["V7_LIVE"], env["GRANOLA_AUTO"]), ("", "on"))

    def test_the_new_reader_leaves_the_last_accepted_map_alone_until_cutover(self):
        before = self.remembered()
        saves = self.store.saves
        rc.apply({rc.SOURCE_NAME: SOURCE}, Source([row("GRANOLA_AUTO", "off")]), connect=connect)
        self.assertEqual((self.store.rows, self.store.saves), (before, saves))
        out = rc.apply({rc.SOURCE_NAME: SOURCE}, Source([row("GRANOLA_AUTO", "off")]), connect=connect, remember=True)
        self.assertEqual((out["remembered"], self.store.rows[settings.SNAPSHOT_KEY]["values"]), (True, {"GRANOLA_AUTO": "off"}))

    def test_rollback_is_removing_the_bootstrap_id_and_the_page_reader_is_unchanged(self):
        env_a, env_b = {"GRANOLA_AUTO": "", "MEGIBOW_BLOCK_ID": ""}, {"GRANOLA_AUTO": "", "MEGIBOW_BLOCK_ID": ""}
        self.assertEqual(rc.apply(env_a, Source(NEW_ROWS))["status"], "off")                  # bootstrap removed: this reader does nothing
        settings.apply(env_a, Page(OLD_LINES))
        settings.apply(env_b, Page(OLD_LINES))
        self.assertEqual(env_a, env_b)
        self.assertEqual(env_a["GRANOLA_AUTO"], "on")


if __name__ == "__main__":
    unittest.main()
