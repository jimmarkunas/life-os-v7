import contextlib
import datetime as dt
import io
import json
import unittest
from unittest import mock

from lifeos.imessage import alerts_endpoint as ep
from lifeos.oraclecloud import card
from lifeos.platform import report_region
from tests.imessage.test_alerts import RUNNER, OTHER, v2, whois
from tests.kit.comms import Report, text

NOW = dt.datetime(2026, 10, 15, 13, 5, 0, tzinfo=dt.timezone.utc)                # 8:05 AM in Chicago
SVC = {"gateway": True, "telegram": True, "whatsapp": True, "alerts": True}


def stats(**over):
    return {"schema": card.SCHEMA, "observed_at": "2026-10-15T13:04:30Z", "cost": {"amount": 0, "currency": "USD"}, "cpu_24h_pct": 5.7,
            "memory_pct": 55.2, "disk_pct": 38.6, "a1_cores": {"used": 2, "available": 0}, "reclaim": "NONE", "services": dict(SVC),
            "chatgpt": {"left_5h": 100, "left_week": 64}, **over}


def report(heading="Oracle Cloud"):
    r = Report()
    r.add("oracle", "callout", "col", [])
    bold = {**text(heading), "annotations": {"bold": True, "color": "default"}}
    r.add("ohead", "heading_3", "oracle", [bold])
    r.add("obody", "paragraph", "oracle", [])
    return r


class Card(unittest.TestCase):
    ENV = {"IMESSAGE_ALERTS_HOST": "100.64.0.9", "ATTENTION_CARD_BLOCK_ID": "attn", "NOTION_JIRA_TOKEN": "unused"}

    def run_card(self, rep, live=True, answer=None, env=None, now=NOW):
        answer = answer or (lambda host: json.dumps(stats()).encode())
        out = io.StringIO()
        with mock.patch.object(report_region, "protected", lambda client, ids, fail: {"Attention": "same"}), contextlib.redirect_stdout(out):
            counts = card.run(0, live, env or self.ENV, now, answer, rep)
        return counts, out.getvalue()

    def test_live_writes_the_lines_with_bars_and_the_stamp_keeping_jims_bold(self):
        rep = report()
        others = {b: rep.plain(b) for b in ("head", "l0", "r0", "attn", "bills")}
        counts, _ = self.run_card(rep)
        self.assertEqual((counts["missing"], counts["reclaim"], counts["written"]), (0, "none", 2))
        self.assertEqual([p[0] for p in rep.patches], ["obody", "ohead"])
        self.assertEqual(rep.plain("obody").split("\n"), [                           # Jim's layout (Oct 15)
            "Cost this month: $0.00 (Oracle billing) · CPU, last 24 h: avg 5.7%",
            "",
            "Memory ██████░░░░ 55% · Disk ████░░░░░░ 39%",
            "",
            "Always Free A1 cores: 2 of 2 in use · no headroom · Reclaim risk: none",
            "Services: Gateway ✓ · Telegram ✓ · WhatsApp ✓ · Alerts ✓",
            "",
            "Codex 5h ██████████ 100% · Codex week ██████░░░░ 64%"])
        colors = [(i["plain_text"], i["annotations"].get("color")) for i in rep.blocks["obody"]["paragraph"]["rich_text"] if "█" in i["plain_text"] or "░" in i["plain_text"]]
        self.assertEqual(colors, [("██████", "green"), ("░░░░", "gray"), ("████", "green"), ("░░░░░░", "gray"),
                                  ("██████████", "green"), ("██████", "green"), ("░░░░", "gray")])          # the bars only; a full bar has no gray part
        self.assertEqual(rep.plain("ohead"), "Oracle Cloud (Updated Oct 15 · 8:05 AM CT)")
        self.assertTrue(rep.blocks["ohead"]["heading_3"]["rich_text"][0]["annotations"]["bold"])
        self.assertEqual({b: rep.plain(b) for b in others}, others)                  # the Communications callout and the rest are untouched

    def test_once_a_day_from_6_am_and_again_while_degraded(self):
        calls = []
        answer = lambda host: calls.append(host) or json.dumps(stats()).encode()
        early = dt.datetime(2026, 10, 15, 10, 30, tzinfo=dt.timezone.utc)            # 5:30 AM in Chicago
        self.assertEqual(self.run_card(report(), answer=answer, now=early)[0], {"due": False, "written": 0})
        done = report("Oracle Cloud (Updated Oct 15 · 6:07 AM CT)")
        self.assertFalse(self.run_card(done, answer=answer)[0]["due"])
        self.assertEqual((calls, done.patches), ([], []))                            # not due: Oracle is not even asked
        for heading in ("Oracle Cloud (Updated Oct 14 · 6:07 AM CT)", "Oracle Cloud (DEGRADED · Updated Oct 15 · 6:07 AM CT)"):
            self.assertEqual(self.run_card(report(heading), answer=answer)[0]["written"], 2)
        self.assertEqual(self.run_card(done, answer=answer, env={**self.ENV, "ORACLE_CARD_FORCE": "true"})[0]["written"], 2)

    def test_a_missing_reading_says_unavailable_and_marks_the_card_degraded(self):
        rep = report()
        counts, _ = self.run_card(rep, answer=lambda host: json.dumps(stats(cost=None, chatgpt=None, services={**SVC, "telegram": None, "alerts": False})).encode())
        body = rep.plain("obody")
        self.assertIn("Cost this month: unavailable", body)
        self.assertIn("Codex plan: unavailable", body)
        self.assertIn("Services: Gateway ✓ · Telegram ? · WhatsApp ✓ · Alerts ✗", body)
        self.assertEqual(rep.plain("ohead"), "Oracle Cloud (DEGRADED · Updated Oct 15 · 8:05 AM CT)")
        self.assertEqual((counts["missing"], counts["services_down"]), (3, 1))

    def test_an_unreported_plan_under_sign_in_with_chatgpt_is_said_plainly_and_is_not_degraded(self):
        rep = report()
        counts, _ = self.run_card(rep, answer=lambda host: json.dumps(stats(chatgpt="unreported")).encode())
        self.assertEqual(rep.plain("obody").split("\n")[-1], "ChatGPT plan: not reported by Sign in with ChatGPT")
        self.assertEqual((rep.plain("ohead"), counts["missing"]), ("Oracle Cloud (Updated Oct 15 · 8:05 AM CT)", 0))
        rep = report()
        self.run_card(rep, answer=lambda host: json.dumps(stats(chatgpt="anything else")).encode())     # any other string is off contract
        self.assertIn("DEGRADED", rep.plain("ohead"))

    def test_reclaim_risk_is_shown_plainly(self):
        rep = report()
        self.run_card(rep, answer=lambda host: json.dumps(stats(reclaim="AT_RISK")).encode())
        self.assertIn("Reclaim risk: AT RISK", rep.plain("obody"))

    def test_an_unreachable_off_contract_or_stale_source_is_all_unavailable(self):
        for answer in (lambda host: (_ for _ in ()).throw(OSError()), lambda host: b"{}",
                       lambda host: json.dumps(stats(extra=1)).encode(), lambda host: json.dumps(stats(memory_pct=140)).encode(),
                       lambda host: json.dumps(stats(observed_at="2026-10-15T11:00:00Z")).encode()):
            rep = report()
            counts, log = self.run_card(rep, answer=answer)
            self.assertIn("source refused", log)
            self.assertEqual(counts["missing"], 11)
            self.assertTrue(rep.plain("ohead").startswith("Oracle Cloud (DEGRADED · "))
            self.assertIn("Memory unavailable · Disk unavailable", rep.plain("obody"))

    def test_dry_run_writes_nothing(self):
        rep = report()
        self.assertEqual(self.run_card(rep, live=False)[0]["written"], 0)
        self.assertEqual(rep.patches, [])

    def test_a_missing_ambiguous_or_reshaped_callout_is_not_owned(self):
        no_card = Report()
        twice = report()
        twice.add("oracle2", "callout", "col", [])
        twice.add("ohead2", "heading_3", "oracle2", [text("Oracle Cloud")])
        twice.add("obody2", "paragraph", "oracle2", [])
        extra = report()
        extra.add("obody3", "paragraph", "oracle", [])
        for rep in (no_card, twice, extra):
            with self.assertRaises(card.OracleCardError):
                self.run_card(rep)
            self.assertEqual(rep.patches, [])
        with self.assertRaises(card.OracleCardError):
            self.run_card(report(), env={**self.ENV, "ATTENTION_CARD_BLOCK_ID": ""})

    def test_the_card_is_found_under_the_jira_card_where_jim_moved_it(self):
        rep = Report()
        rep.add("col2", "column", "root")
        rep.add("jira", "callout", "col2", [text("JIRA Execution")])
        rep.add("oracle", "callout", "col2", [])
        rep.add("ohead", "heading_3", "oracle", [text("Oracle Cloud (Updated Oct 14 · 6:07 AM CT)")])
        rep.add("obody", "paragraph", "oracle", [])
        env = {**self.ENV, "JIRA_CARD_BLOCK_ID": "jira"}
        counts, _ = self.run_card(rep, env=env)
        self.assertEqual(counts["written"], 2)
        self.assertEqual(rep.plain("ohead"), "Oracle Cloud (Updated Oct 15 · 8:05 AM CT)")
        with self.assertRaises(card.OracleCardError):                                  # without the JIRA anchor that column is not searched
            self.run_card(rep)
        both = report()                                                                 # a second card beside Attention makes it ambiguous
        both.add("col2", "column", "root")
        both.add("jira", "callout", "col2", [text("JIRA Execution")])
        both.add("oracle2", "callout", "col2", [])
        both.add("ohead2", "heading_3", "oracle2", [text("Oracle Cloud")])
        both.add("obody2", "paragraph", "oracle2", [])
        with self.assertRaises(card.OracleCardError):
            self.run_card(both, env=env)
        self.assertEqual(both.patches, [])

    def test_a_changed_protected_region_fails_after_the_write(self):
        states = iter([{"Attention": "a"}, {"Attention": "b"}])
        with mock.patch.object(report_region, "protected", lambda client, ids, fail: next(states)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(card.OracleCardError):
                card.run(0, True, self.ENV, NOW, lambda host: json.dumps(stats()).encode(), report())


class Endpoint(unittest.TestCase):
    def call(self, payload, stats_fn):
        out = io.BytesIO()
        code = ep.main(io.BytesIO(payload), out, whois, "unused", "/nonexistent/event-key", uid=lambda: 0, stats=stats_fn)
        return code, out.getvalue()

    def test_only_the_tagged_runner_gets_the_numbers(self):
        code, raw = self.call(v2(RUNNER) + b"oracle1\n", lambda: stats())
        self.assertEqual((code, json.loads(raw)["schema"]), (0, card.SCHEMA))
        for payload in (v2(OTHER) + b"oracle1\n", v2(RUNNER) + b"oracle1 x\n", v2(RUNNER) + b"oracle2\n"):
            self.assertEqual(self.call(payload, lambda: stats()), (1, b""))

    def test_a_failing_or_off_contract_script_answers_nothing(self):
        self.assertEqual(self.call(v2(RUNNER) + b"oracle1\n", lambda: (_ for _ in ()).throw(ValueError())), (1, b""))
        done = mock.Mock(returncode=0, stdout=json.dumps({"schema": "other"}))
        with self.assertRaises(ValueError):
            ep.oracle_stats(run=lambda *a, **k: done)


if __name__ == "__main__":
    unittest.main()
