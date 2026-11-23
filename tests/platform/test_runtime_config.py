"""LIFE-844 (synthetic): the stable-ID Runtime Configuration reader. Fixtures only: no Notion, no database, no workflow, no live write."""
import unittest
from pathlib import Path
from unittest import mock

from lifeos.platform import runtime_config as rc
from lifeos.platform import settings
from tests.platform.test_settings import FakeStore, Page, connect

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "5a1c0e57-3b2d-4f6a-9c1d-0e2f3a4b5c6d"
OTHER = "2ba672dd-026e-4d75-9f57-1ade91bcc7be"           # the Product Requirements database: the reader must never touch it
ID = "4183c5a0-5926-81be-9e19-e85714dc256f"
ID2 = "4183c5a0-5926-8115-b53c-c45f6efd8ec2"
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

    def test_the_new_bootstrap_name_is_registered_and_an_unreadable_source_leaves_the_old_page_result_unchanged(self):   # LIFE-892: the old page is the second source
        self.assertIn(rc.SOURCE_NAME + " ", (ROOT / "lifeos/platform/config.txt").read_text())
        plain, with_id = {"GRANOLA_AUTO": ""}, {"GRANOLA_AUTO": "", rc.SOURCE_NAME: SOURCE}
        got = settings.apply(with_id, Page(OLD_LINES))                                      # the page client cannot serve the source: it says so and the page is read
        self.assertEqual(got.pop("new_source"), "unreadable:RUNTIME_CONFIG_UNREADABLE")
        self.assertEqual(settings.apply(plain, Page(OLD_LINES)), got)


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


class Both:
    """Old page and new source behind one client, as the shadow read sees them."""
    def __init__(self, page, source):
        self.page, self.source = page, source

    def call(self, method, path, body=None):
        return (self.source if path.startswith("/data_sources/") else self.page).call(method, path, body)


class Shadow(unittest.TestCase):
    def test_no_bootstrap_id_or_no_token_reads_nothing(self):
        self.assertEqual(rc.shadow({}), {"status": "off"})
        self.assertEqual(rc.shadow(ENV), {"status": "off"})

    def test_matching_sources_report_no_difference_and_change_nothing(self):
        env, client = dict(ENV), Both(Page(OLD_LINES), Source(NEW_ROWS))
        out = rc.shadow(env, client)
        self.assertEqual((out["status"], out["matched"], out["blocking"]), ("ok", 4, False))
        self.assertEqual(env, ENV)

    def test_a_difference_is_counted_by_name_only(self):
        rows = NEW_ROWS[:3] + [row("INTERVIEW_TEST_COMPANIES", SECRET)]
        out = rc.shadow(dict(ENV), Both(Page(OLD_LINES), Source(rows)))
        self.assertTrue(out["blocking"])
        self.assertEqual(out["mismatched"], ["INTERVIEW_TEST_COMPANIES"])
        self.assertNotIn(SECRET, str(out))

    def test_an_unreadable_source_never_raises_and_leaks_nothing(self):
        out = rc.shadow(dict(ENV), Both(Page(OLD_LINES), Source([], broken=True)))
        self.assertEqual(out, {"status": "unreadable:RUNTIME_CONFIG_UNREADABLE"})


TOKENS7 = list(rc.PROOF_TOKENS)
SENTINEL_TOKENS = {name: f"synthetic-token-{i}-{SECRET}" for i, name in enumerate(TOKENS7)}


def clients(broken=(), empty=()):
    """make_client(token) -> a source client per token; a token in `broken` cannot read it (Notion refuses), a token in `empty` reads nothing usable."""
    def make(token):
        if token in broken:
            return Source([], broken=True)
        return Source([] if token in empty else NEW_ROWS)
    return make


class Phase1TokenProof(unittest.TestCase):
    """LIFE-858 Phase 1: each of the seven token identities is probed by name; PASS or FAIL only, never a token or a value."""

    def test_the_seven_identities_are_exactly_the_ones_the_ticket_names(self):
        self.assertEqual(TOKENS7, ["NOTION_JIRA_TOKEN", "NOTION_API_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_RECRUITERS_TOKEN", "NOTION_AMAZON_TOKEN", "NOTION_BILLS_TOKEN", "NOTION_FIT_SYNC_TOKEN"])

    def test_every_token_the_step_holds_that_can_read_the_source_is_pass(self):
        out = rc.probe({**ENV, **SENTINEL_TOKENS}, clients())
        self.assertEqual(out, {name: "PASS" for name in TOKENS7})

    def test_a_token_that_cannot_read_is_fail_with_a_fixed_code_and_the_others_still_pass(self):
        bad = SENTINEL_TOKENS["NOTION_BILLS_TOKEN"]
        out = rc.probe({**ENV, **SENTINEL_TOKENS}, clients(broken={bad}))
        self.assertEqual(out["NOTION_BILLS_TOKEN"], "FAIL:RUNTIME_CONFIG_UNREADABLE")
        self.assertEqual([n for n, v in out.items() if v == "PASS"], [n for n in TOKENS7 if n != "NOTION_BILLS_TOKEN"])

    def test_a_source_with_no_usable_row_is_fail_not_pass(self):
        out = rc.probe({**ENV, "NOTION_JIRA_TOKEN": "t1"}, clients(empty={"t1"}))
        self.assertEqual(out, {"NOTION_JIRA_TOKEN": "FAIL:RUNTIME_CONFIG_EMPTY"})

    def test_a_token_the_step_does_not_hold_is_not_listed_and_nothing_is_claimed_for_it(self):
        out = rc.probe({**ENV, "NOTION_INTERVIEW_TOKEN": "t2", "NOTION_JIRA_TOKEN": "   "}, clients())
        self.assertEqual(out, {"NOTION_INTERVIEW_TOKEN": "PASS"})
        self.assertEqual(rc.probe(dict(ENV), clients()), {})

    def test_a_name_outside_the_seven_is_never_probed(self):
        calls = []
        rc.probe({**ENV, "NOTION_OTHER_TOKEN": "x", "NOTION_JIRA_TOKEN": "t"}, lambda t: calls.append(t) or Source(NEW_ROWS))
        self.assertEqual(calls, ["t"])

    def test_no_token_value_or_error_message_ever_reaches_the_result(self):
        out = rc.probe({**ENV, **SENTINEL_TOKENS}, clients(broken=set(SENTINEL_TOKENS.values())))
        self.assertTrue(all(v == "FAIL:RUNTIME_CONFIG_UNREADABLE" for v in out.values()))
        self.assertNotIn(SECRET, str(out))
        self.assertNotIn("synthetic-token", str(out))

    def test_a_client_that_cannot_even_be_built_is_a_fixed_fail_code(self):
        def boom(token):
            raise ValueError(SECRET)
        out = rc.probe({**ENV, "NOTION_API_TOKEN": "t"}, boom)
        self.assertEqual(out, {"NOTION_API_TOKEN": "FAIL:ValueError"})
        self.assertNotIn(SECRET, str(out))


class Phase1CompareReceipt(unittest.TestCase):
    def shadow(self, rows=NEW_ROWS, env=None, **kw):
        return rc.shadow({**ENV, **(env or {})}, Both(Page(OLD_LINES), Source(rows)), **kw)

    def test_the_receipt_counts_rows_and_kinds_and_costs_and_leaks_no_value(self):
        out = self.shadow()
        self.assertEqual((out["status"], out["rows"], out["kinds"], out["blocking"]), ("ok", {"old": 4, "new": 4}, {"address": 2, "switch": 2}, False))
        self.assertIsInstance(out["seconds"], float)
        self.assertNotIn("names", out)
        self.assertNotIn("tokens", out)
        self.assertNotIn(ID, str(out))

    def test_a_refused_invalid_or_repeated_row_is_named_in_the_receipt_and_blocks_nothing_by_itself(self):
        rows = NEW_ROWS + [row("GITHUB_TOKEN", SECRET), row("DUP", "on"), row("DUP", "on")]
        out = self.shadow(rows)
        self.assertEqual(out["new_counts"]["refused"], ["GITHUB_TOKEN"])
        self.assertNotIn(SECRET, str(out))

    def test_a_missing_old_or_new_name_blocks_and_is_named(self):
        out = self.shadow(NEW_ROWS[:3])
        self.assertEqual((out["only_old"], out["blocking"]), (["INTERVIEW_TEST_COMPANIES"], True))

    def test_token_proof_rides_the_receipt_only_when_asked_and_names_only_on_a_manual_dispatch(self):
        out = self.shadow(env={"GITHUB_EVENT_NAME": "schedule", **SENTINEL_TOKENS}, make_client=clients())
        self.assertEqual(out["tokens"], {name: "PASS" for name in TOKENS7})
        self.assertNotIn("names", out)
        manual = self.shadow(env={"GITHUB_EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(manual["names"], {"old": sorted(["JIRA_CARD_BLOCK_ID", "MEGIBOW_BLOCK_ID", "GRANOLA_AUTO", "INTERVIEW_TEST_COMPANIES"]), "new": sorted(r["id"].removeprefix("row-") for r in NEW_ROWS)})
        self.assertNotIn(SECRET, str(manual))

    def test_the_environment_is_unchanged_whatever_the_sources_say(self):
        env = {**ENV, "GRANOLA_AUTO": "off", **SENTINEL_TOKENS}
        before = dict(env)
        rc.shadow(env, Both(Page(OLD_LINES), Source([row("GRANOLA_AUTO", "on")])), make_client=clients())
        self.assertEqual(env, before)

    def test_missing_credentials_or_an_unreadable_source_are_off_or_a_fixed_code(self):
        self.assertEqual(rc.shadow(dict(ENV)), {"status": "off"})                                    # the ID but no Notion token: nothing was read
        self.assertEqual(rc.shadow({rc.SOURCE_NAME: "not-an-id"}), {"status": "unreadable:RUNTIME_CONFIG_SOURCE_ID_INVALID"})
        self.assertEqual(rc.shadow(dict(ENV), Both(Page(OLD_LINES), Source([], broken=True))), {"status": "unreadable:RUNTIME_CONFIG_UNREADABLE"})


class Phase2SwitchNewSourceFirst(unittest.TestCase):
    """LIFE-892 (Jim, Nov 22 2026, 6:14 PM CT): settings.apply reads the V7 Runtime Configuration source first, the old page second; rollback is an empty bootstrap ID."""
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch("lifeos.platform.stored_pages.STORE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_new_source_wins_and_is_remembered_and_the_receipt_says_so(self):
        env = {"GRANOLA_AUTO": "", **ENV}
        page = Page(OLD_LINES)
        out = settings.apply(env, client=Both(page, Source([row("GRANOLA_AUTO", "off")])), connect=connect)
        self.assertEqual((env["GRANOLA_AUTO"], out["status"], out["source"], out["remembered"]), ("off", "ok", "new", True))
        self.assertNotIn("new_source", out)
        self.assertEqual(self.store.rows[settings.SNAPSHOT_KEY]["values"], {"GRANOLA_AUTO": "off"})

    def test_the_old_page_is_not_read_while_the_new_source_is_good_and_does_not_seed(self):
        class Spy(Both):
            def call(self, method, path, body=None):
                assert path.startswith("/data_sources/"), (method, path)                  # no page search, no block read, no seed write
                return super().call(method, path, body)
        env = {"GRANOLA_AUTO": "", **ENV}
        out = settings.apply(env, client=Spy(Page(OLD_LINES), Source(NEW_ROWS)), copy=True, connect=connect)
        self.assertEqual((out["source"], "seeded" in out), ("new", False))

    def test_an_unreadable_or_empty_new_source_falls_to_the_old_page_second_and_names_why(self):
        for rows, broken, code in (([], False, "RUNTIME_CONFIG_EMPTY"), (NEW_ROWS, True, "RUNTIME_CONFIG_UNREADABLE")):
            env = {"GRANOLA_AUTO": "", **ENV}
            out = settings.apply(env, client=Both(Page(OLD_LINES), Source(rows, broken=broken)), connect=connect)
            self.assertEqual((env["GRANOLA_AUTO"], out["status"], out["new_source"], "source" in out), ("on", "ok", "unreadable:" + code, False))
            self.assertNotIn(SECRET, repr(out))

    def test_with_both_sources_unreadable_the_last_accepted_map_is_used(self):
        settings.apply({}, Page(OLD_LINES), connect=connect)
        env = {"GRANOLA_AUTO": "", **ENV}
        out = settings.apply(env, client=Both(Page(OLD_LINES, broken=True), Source(NEW_ROWS, broken=True)), connect=connect)
        self.assertEqual((env["GRANOLA_AUTO"], out["status"], out["new_source"]), ("on", "unreadable:RuntimeError", "unreadable:RUNTIME_CONFIG_UNREADABLE"))
        self.assertRegex(out["last_accepted"], r"CT$")

    def test_an_empty_bootstrap_id_is_the_rollback_and_reads_only_the_old_page(self):
        for empty in ("", "  "):
            env_a, env_b = {"GRANOLA_AUTO": "", rc.SOURCE_NAME: empty}, {"GRANOLA_AUTO": ""}
            client = Both(Page(OLD_LINES), Source([row("GRANOLA_AUTO", "off")]))
            out = settings.apply(env_a, client=client)
            settings.apply(env_b, Page(OLD_LINES))
            self.assertEqual((env_a["GRANOLA_AUTO"], env_a, "source" in out, "new_source" in out), ("on", env_b | {rc.SOURCE_NAME: empty}, False, False))

    def test_the_shadow_rule_and_the_safety_refusals_apply_to_the_new_source_too(self):
        env = {"GRANOLA_AUTO": "on", "V7_LIVE": "", **ENV}
        out = settings.apply(env, client=Both(Page(OLD_LINES), Source([row("GRANOLA_AUTO", "off"), row("V7_LIVE", "on")])))
        self.assertEqual((env["GRANOLA_AUTO"], env["V7_LIVE"], out["mismatched"], "V7_LIVE" in out["refused"]), ("on", "", ["GRANOLA_AUTO"], True))

    def test_run_py_still_reports_the_compare_after_settings_apply(self):
        text = (ROOT / "lifeos/run.py").read_text()
        self.assertLess(text.index("settings.apply(os.environ)"), text.index("runtime_config.shadow(os.environ"))
        self.assertNotIn("runtime_config.apply", text)


class ShadowOncePerProcess(unittest.TestCase):
    """LIFE-858: the tick runs about 23 stages in one process; each used to repeat the whole compare (about 2.6 s each). `once=True` reads once and the rest are cached."""
    def setUp(self):
        rc.reset_once()
        self.addCleanup(rc.reset_once)

    def counting(self):
        client = Both(Page(OLD_LINES), Source(NEW_ROWS))
        calls = []
        original = client.call
        client.call = lambda *a, **k: calls.append(a[:2]) or original(*a, **k)
        return client, calls

    def test_the_second_call_in_a_process_makes_no_read_and_returns_a_compact_cached_receipt(self):
        client, calls = self.counting()
        first = rc.shadow(dict(ENV), client, once=True)
        reads = len(calls)
        self.assertGreater(reads, 0)
        again = rc.shadow(dict(ENV), client, once=True)
        self.assertEqual(len(calls), reads)                                          # zero more Notion reads
        self.assertEqual(again, {"status": "ok", "matched": first["matched"], "blocking": first["blocking"], "cached": True})
        self.assertNotIn("names", again)
        self.assertNotIn("tokens", again)

    def test_a_different_bootstrap_id_or_token_set_reads_again_and_without_once_every_call_reads(self):
        client, calls = self.counting()
        rc.shadow(dict(ENV), client, once=True)
        before = len(calls)
        rc.shadow(dict(ENV), client)                                                 # no once: always reads
        self.assertGreater(len(calls), before)
        after = len(calls)
        rc.shadow({**ENV, "NOTION_JIRA_TOKEN": "t"}, client, once=True, make_client=lambda token: Source(NEW_ROWS))
        self.assertGreater(len(calls), after)                                        # a different token set is a different key

    def test_off_and_unreadable_are_not_cached_so_a_later_good_read_still_happens(self):
        self.assertEqual(rc.shadow({}, once=True), {"status": "off"})
        broken = Both(Page(OLD_LINES), Source([], broken=True))
        self.assertEqual(rc.shadow(dict(ENV), broken, once=True), {"status": "unreadable:RUNTIME_CONFIG_UNREADABLE"})
        client, calls = self.counting()
        self.assertEqual(rc.shadow(dict(ENV), client, once=True)["status"], "ok")   # the failure did not poison the process
        self.assertGreater(len(calls), 0)

    def test_concurrent_callers_make_one_read(self):
        import threading
        client, calls = self.counting()
        results = []
        threads = [threading.Thread(target=lambda: results.append(rc.shadow(dict(ENV), client, once=True))) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if not r.get("cached")), 1)
        self.assertEqual(len(results), 8)


if __name__ == "__main__":
    unittest.main()
