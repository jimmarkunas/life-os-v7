"""(synthetic): the stable-ID Runtime Configuration reader."""

import unittest
from pathlib import Path
from unittest import mock

from lifeos.platform import runtime_config as rc
from lifeos.platform import settings
from tests.platform.settings_fixture import ENV, SECRET, SOURCE, Source, row
from tests.platform.test_settings import FakeStore, connect

ROOT = Path(__file__).resolve().parents[2]
OTHER = "[removed]"
ID = "[removed]"
ID2 = "[removed]"


NEW_ROWS = [
    row("JIRA_CARD_BLOCK_ID", ID),
    row("MEGIBOW_BLOCK_ID", ID2),
    row("GRANOLA_AUTO", "on"),
    row("INTERVIEW_TEST_COMPANIES", "Smartsheet, Walmart"),
]


TOKENS7 = list(rc.PROOF_TOKENS)
SENTINEL_TOKENS = {name: f"synthetic-token-{i}-{SECRET}" for i, name in enumerate(TOKENS7)}


def clients(broken=(), empty=()):
    """make_client(token) -> a source client per token"""

    def make(token):
        if token in broken:
            return Source([], broken=True)
        return Source([] if token in empty else NEW_ROWS)

    return make


class ReadByStableId(unittest.TestCase):
    def test_typed_rows_resolve_by_id_with_no_title_search_and_no_product_requirements_access(self):
        client = Source(NEW_ROWS)
        found, counts = rc.read(client, ENV)
        self.assertEqual(
            found,
            {
                "JIRA_CARD_BLOCK_ID": ID,
                "MEGIBOW_BLOCK_ID": ID2,
                "GRANOLA_AUTO": "on",
                "INTERVIEW_TEST_COMPANIES": "Smartsheet, Walmart",
            },
        )
        self.assertEqual(client.calls, [("POST", f"/data_sources/{SOURCE}/query")])  # only the configured source
        self.assertNotIn(OTHER, repr(client.calls))
        self.assertEqual(
            (counts["refused"], counts["unknown"], counts["invalid"], counts["repeated"], counts["inactive"]),
            ([], 0, [], [], 0),
        )

    def test_a_source_of_several_pages_is_read_in_full(self):
        self.assertEqual(rc.read(Source(NEW_ROWS, pages=3), ENV)[0], rc.read(Source(NEW_ROWS), ENV)[0])

    def test_the_bootstrap_id_is_required_and_must_look_like_an_address(self):
        for env, code in (
            ({}, "RUNTIME_CONFIG_SOURCE_ID_MISSING"),
            ({rc.SOURCE_NAME: "not-an-id"}, "RUNTIME_CONFIG_SOURCE_ID_INVALID"),
        ):
            with self.assertRaises(rc.RuntimeConfigError) as raised:
                rc.source_id(env)
            self.assertEqual(str(raised.exception), code)
        self.assertEqual(rc.source_id({rc.SOURCE_NAME: f"collection://{SOURCE}"}), SOURCE)

    def test_the_reader_names_no_title_and_does_not_search(self):
        text = (ROOT / "lifeos/platform/runtime_config.py").read_text()
        code = "\n".join(
            line.split("#")[0] for line in text.splitlines() if not line.lstrip().startswith(('"""', "Why:", "Rows:"))
        )
        self.assertNotIn("/search", code)
        self.assertNotIn("settings.TITLE", code)

    def test_the_bootstrap_name_is_registered(self):
        self.assertIn(rc.SOURCE_NAME + " ", (ROOT / "lifeos/platform/config.txt").read_text())


class RefusedNeverApplied(unittest.TestCase):
    def test_each_bad_row_is_counted_by_name_and_none_is_applied(self):
        rows = [
            row("V7_LIVE", "on"),
            row("V7_CLOUD_FIT", "on"),
            row("NOTION_JIRA_TOKEN", SECRET),
            row("ALERT_SETTINGS", SECRET),  # safety and credential-shaped
            row("NOT_A_SETTING", SECRET),  # unknown
            row("JIRA_CARD_BLOCK_ID", "not-an-address"),
            row("GRANOLA_AUTO", "on", kind="address"),
            row("HIRING_AUTO_CLOSE", "on", revision=-1),
            row("RECRUITER_OUTREACH", "a\nb"),
            row("JOB_PAGE_OUTREACH", ""),
            row("MAIL_PORTAL_URL", "x", Revision=None),  # a property missing
            row("INTERVIEW_LEADS", "on", active=False),  # inactive: skipped, counted
            row("TICK_MAX_PARALLEL", "2"),
        ]
        found, counts = rc.parse(rows)
        self.assertEqual(found, {"TICK_MAX_PARALLEL": "2"})
        self.assertEqual(sorted(counts["refused"]), ["ALERT_SETTINGS", "NOTION_JIRA_TOKEN", "V7_CLOUD_FIT", "V7_LIVE"])
        self.assertEqual(
            sorted(counts["invalid"]),
            ["GRANOLA_AUTO", "HIRING_AUTO_CLOSE", "JIRA_CARD_BLOCK_ID", "JOB_PAGE_OUTREACH", "RECRUITER_OUTREACH"],
        )
        self.assertEqual((counts["unknown"], counts["inactive"]), (2, 1))
        self.assertNotIn(SECRET, repr((found, counts)))  # names and counts only

    def test_a_duplicate_active_name_is_applied_from_neither_row_but_an_inactive_copy_is_fine(self):
        found, counts = rc.parse(
            [
                row("GRANOLA_AUTO", "on"),
                row("GRANOLA_AUTO", "off"),
                row("HIRING_AUTO_CLOSE", "on"),
                row("HIRING_AUTO_CLOSE", "off", active=False),
            ]
        )
        self.assertEqual((found, counts["repeated"]), ({"HIRING_AUTO_CLOSE": "on"}, ["GRANOLA_AUTO"]))

    def test_a_safety_name_is_never_movable_here_either(self):
        self.assertEqual(settings.SAFETY & {r["properties"]["Name"]["title"][0]["plain_text"] for r in NEW_ROWS}, set())
        self.assertEqual(
            rc.export_rows({"V7_LIVE": "on", "GRANOLA_AUTO": "on"}),
            [{"Name": "GRANOLA_AUTO", "Value": "on", "Kind": "switch", "Revision": 1, "Active": True}],
        )


class Phase1TokenProof(unittest.TestCase):
    """Phase 1: each of the seven token identities is probed by name"""

    def test_the_seven_identities_are_exactly_the_ones_the_ticket_names(self):
        self.assertEqual(
            TOKENS7,
            [
                "NOTION_JIRA_TOKEN",
                "NOTION_API_TOKEN",
                "NOTION_INTERVIEW_TOKEN",
                "NOTION_RECRUITERS_TOKEN",
                "NOTION_AMAZON_TOKEN",
                "NOTION_BILLS_TOKEN",
                "NOTION_FIT_SYNC_TOKEN",
            ],
        )

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
        rc.probe(
            {**ENV, "NOTION_OTHER_TOKEN": "x", "NOTION_JIRA_TOKEN": "t"}, lambda t: calls.append(t) or Source(NEW_ROWS)
        )
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
        return rc.shadow({**ENV, **(env or {})}, Source(rows), **kw)

    def test_the_receipt_counts_rows_and_kinds_and_costs_and_leaks_no_value(self):
        out = self.shadow()
        self.assertEqual((out["status"], out["rows"], out["kinds"]), ("ok", 4, {"address": 2, "switch": 2}))
        self.assertIsInstance(out["seconds"], float)
        self.assertNotIn("names", out)
        self.assertNotIn("tokens", out)
        self.assertNotIn(ID, str(out))

    def test_a_refused_invalid_or_repeated_row_is_named_in_the_receipt_and_blocks_nothing_by_itself(self):
        rows = NEW_ROWS + [row("GITHUB_TOKEN", SECRET), row("DUP", "on"), row("DUP", "on")]
        out = self.shadow(rows)
        self.assertEqual(out["new_counts"]["refused"], ["GITHUB_TOKEN"])
        self.assertNotIn(SECRET, str(out))

    def test_token_proof_rides_the_receipt_only_when_asked_and_names_only_on_a_manual_dispatch(self):
        out = self.shadow(env={"GITHUB_EVENT_NAME": "schedule", **SENTINEL_TOKENS}, make_client=clients())
        self.assertEqual(out["tokens"], {name: "PASS" for name in TOKENS7})
        self.assertNotIn("names", out)
        manual = self.shadow(env={"GITHUB_EVENT_NAME": "workflow_dispatch"})
        self.assertEqual(manual["names"], sorted(r["id"].removeprefix("row-") for r in NEW_ROWS))
        self.assertNotIn(SECRET, str(manual))

    def test_the_environment_is_unchanged_whatever_the_sources_say(self):
        env = {**ENV, "GRANOLA_AUTO": "off", **SENTINEL_TOKENS}
        before = dict(env)
        rc.shadow(env, Source([row("GRANOLA_AUTO", "on")]), make_client=clients())
        self.assertEqual(env, before)

    def test_missing_credentials_or_an_unreadable_source_are_off_or_a_fixed_code(self):
        self.assertEqual(rc.shadow(dict(ENV)), {"status": "off"})  # the ID but no Notion token: nothing was read
        self.assertEqual(
            rc.shadow({rc.SOURCE_NAME: "not-an-id"}), {"status": "unreadable:RUNTIME_CONFIG_SOURCE_ID_INVALID"}
        )
        self.assertEqual(
            rc.shadow(dict(ENV), Source([], broken=True)), {"status": "unreadable:RUNTIME_CONFIG_UNREADABLE"}
        )


class ShadowOncePerProcess(unittest.TestCase):
    """the tick runs about 23 stages in one process"""

    def setUp(self):
        rc.reset_once()
        self.addCleanup(rc.reset_once)

    def counting(self):
        client = Source(NEW_ROWS)
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
        self.assertEqual(len(calls), reads)  # zero more Notion reads
        self.assertEqual(again, {"status": "ok", "rows": first["rows"], "cached": True})
        self.assertNotIn("names", again)
        self.assertNotIn("tokens", again)

    def test_a_different_bootstrap_id_or_token_set_reads_again_and_without_once_every_call_reads(self):
        client, calls = self.counting()
        rc.shadow(dict(ENV), client, once=True)
        before = len(calls)
        rc.shadow(dict(ENV), client)  # no once: always reads
        self.assertGreater(len(calls), before)
        after = len(calls)
        rc.shadow({**ENV, "NOTION_JIRA_TOKEN": "t"}, client, once=True, make_client=lambda token: Source(NEW_ROWS))
        self.assertGreater(len(calls), after)  # a different token set is a different key

    def test_off_and_unreadable_are_not_cached_so_a_later_good_read_still_happens(self):
        self.assertEqual(rc.shadow({}, once=True), {"status": "off"})
        broken = Source([], broken=True)
        self.assertEqual(rc.shadow(dict(ENV), broken, once=True), {"status": "unreadable:RUNTIME_CONFIG_UNREADABLE"})
        client, calls = self.counting()
        self.assertEqual(
            rc.shadow(dict(ENV), client, once=True)["status"], "ok"
        )  # the failure did not poison the process
        self.assertGreater(len(calls), 0)

    def test_concurrent_callers_make_one_read(self):
        import threading

        client, calls = self.counting()
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(rc.shadow(dict(ENV), client, once=True))) for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if not r.get("cached")), 1)
        self.assertEqual(len(results), 8)


if __name__ == "__main__":
    unittest.main()
