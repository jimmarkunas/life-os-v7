import contextlib
import datetime as dt
import io
import json
import unittest
from unittest import mock

from lifeos.imessage import alerts_endpoint as ep
from lifeos.oraclecloud import card, system_table as st
from lifeos.platform import notion_client, report_region
from tests.imessage.support import RUNNER, OTHER, v2, whois
from tests.oraclecloud.support import SystemPage, plain_row

NOW = dt.datetime(2026, 10, 15, 13, 5, 0, tzinfo=dt.timezone.utc)                # 8:05 AM in Chicago
SVC = {"gateway": True, "telegram": True, "whatsapp": True, "alerts": True}


def stats(**over):
    return {"schema": card.SCHEMA, "observed_at": "2026-10-15T13:04:30Z", "cost": {"amount": 0, "currency": "USD"}, "cpu_24h_pct": 5.7,
            "memory_pct": 55.2, "disk_pct": 38.6, "a1_cores": {"used": 2, "available": 0}, "reclaim": "NONE", "services": dict(SVC),
            "chatgpt": {"left_5h": 100, "left_week": 64}, "backup": {"ok": True, "code": "ok", "age_h": 3}, **over}


class Validate(unittest.TestCase):
    """The Oracle contract is unchanged by LIFE-975: a reply is used whole or not at all."""

    def test_a_good_reply_is_used_and_the_optional_readings_are_kept(self):
        r = card.validate(json.dumps({**stats(), "workers": {"count": 6, "agents": 5}, "backups": {"used": 5, "available": 0}}), NOW)
        self.assertEqual((r["memory_pct"], r["workers"], r["backups"], r["honcho_reported"]), (55.2, {"count": 6, "agents": 5}, {"used": 5, "available": 0}, False))

    def test_the_backup_reading_is_optional_on_the_wire_and_checked(self):
        old = stats()
        del old["backup"]
        self.assertIsNone(card.validate(json.dumps(old), NOW)["backup"])
        for bad in ({"ok": True, "code": "a /path", "age_h": 1}, {"ok": 1, "code": "ok", "age_h": 1}, {"ok": True, "code": "ok", "age_h": -1}, {"ok": True, "code": "ok"}):
            with self.assertRaises(card.OracleCardError, msg=bad):
                card.validate(json.dumps(stats(backup=bad)), NOW)

    def test_the_worker_count_and_the_backup_slots_are_optional_and_checked(self):
        for key, bads in (("workers", ({"count": 6}, {"count": -1, "agents": 5}, {"count": 6, "agents": 0}, {"count": "6", "agents": 5}, {"count": 6, "agents": 5, "pid": 1}, [6, 5])),
                          ("backups", ({"used": 5}, {"used": -1, "available": 0}, {"used": "5", "available": 0}, {"used": 5, "available": 0.5}, {"used": 5, "available": 0, "id": "x"}, [5, 0]))):
            for bad in bads:
                with self.assertRaises(card.OracleCardError, msg=bad):
                    card.validate(json.dumps({**stats(), key: bad}), NOW)

    def test_an_unreachable_off_contract_or_stale_source_is_refused(self):
        for raw in (b"", b"{}", json.dumps(stats(extra=1)).encode(), json.dumps(stats(memory_pct=140)).encode(), json.dumps(stats(observed_at="2026-10-15T11:00:00Z")).encode()):
            with self.assertRaises(card.OracleCardError):
                card.validate(raw, NOW)

    def test_stats_of_answers_none_and_logs_a_fixed_code_instead_of_raising(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(card.stats_of("h", lambda host: (_ for _ in ()).throw(OSError()), NOW))
            self.assertIsNone(card.stats_of("h", lambda host: b"{}", NOW))
        self.assertEqual(out.getvalue().count("oracle-card: source refused"), 2)
        self.assertEqual(card.stats_of("h", lambda host: json.dumps(stats()).encode(), NOW)["disk_pct"], 38.6)


class Place(unittest.TestCase):
    """Finding the System callout and writing its heading and its cells in place."""

    def table(self, memory="9%"):
        return [[[(h, "bold")] for h in st.HEADER]] + [[[(label, None)], [("██", "green"), ("░░░░░░░░", "gray")], [(memory, None)], [("—", None)], [("OK", "green")]] for label in st.LABELS]

    def test_the_callout_is_found_at_the_top_level_through_the_jira_card_and_a_stamped_heading_still_matches(self):
        for heading in ("System", "System (11/24 · 11:01 PM CT)"):
            heading_block, table, rows = card.locate(SystemPage(heading), "jira")
            self.assertEqual((report_region.plain(heading_block), len(rows)), (heading, 17))

    def test_a_missing_two_or_reshaped_callout_is_not_owned_and_nothing_is_written(self):
        for page in (SystemPage("Oracle Cloud"), SystemPage(extra_callout=True), SystemPage(table_rows=16)):
            with self.assertRaises(card.OracleCardError) as error:
                card.locate(page, "jira")
            self.assertEqual(str(error.exception), "CARD_NOT_OWNED")
            self.assertEqual(page.patches, [])

    def test_only_the_differing_rows_and_the_heading_are_written_and_each_is_read_back(self):
        page = SystemPage()
        heading, _, rows = card.locate(page, "jira")
        want = self.table()
        result = card.write(page, heading, rows, want, "System (11/25 · 9:00 AM CT)", True)
        self.assertEqual((result["rows_written"], result["heading_written"]), (16, True))                  # the header and the rows that already match are not touched
        self.assertEqual(page.plain("sys-h"), "System (11/25 · 9:00 AM CT)")
        self.assertEqual(page.row_text()[1][2], "9%")
        again_heading, _, again_rows = card.locate(page, "jira")
        mark = len(page.patches)
        self.assertEqual(card.write(page, again_heading, again_rows, want, "System (11/25 · 9:00 AM CT)", True)["rows_written"], 0)
        self.assertEqual(len(page.patches), mark)                                                            # an unchanged table writes nothing
        changed = self.table(memory="10%")
        card.write(page, again_heading, again_rows, changed, "System (11/25 · 9:00 AM CT)", True)
        self.assertEqual([p[0] for p in page.patches[mark:]], [f"sys-r{i}" for i in range(1, 17)])

    def test_the_heading_keeps_jims_bold_and_a_dry_run_writes_nothing(self):
        page = SystemPage()
        heading, _, rows = card.locate(page, "jira")
        result = card.write(page, heading, rows, self.table(), "System (11/25 · 9:00 AM CT)", False)
        self.assertEqual((result["rows_changed"], result["rows_written"], page.patches), (16, 0, []))
        card.write(page, heading, rows, self.table(), "System (11/25 · 9:00 AM CT)", True)
        self.assertTrue(page.blocks["sys-h"]["heading_3"]["rich_text"][0]["annotations"]["bold"])

    def test_a_row_that_does_not_read_back_fails_with_a_fixed_code(self):
        page = SystemPage()
        heading, _, rows = card.locate(page, "jira")
        real = page.call_once
        page.call_once = lambda method, path, body=None, **kw: (real(method, path, body, **kw), page.blocks.__setitem__("sys-r1", {**page.blocks["sys-r1"], "table_row": {"cells": [[]] * 5}}))[0]
        with self.assertRaises(card.OracleCardError) as error:
            card.write(page, heading, rows, self.table(), "System (11/25 · 9:00 AM CT)", True)
        self.assertEqual(str(error.exception), "ORACLE_READBACK_MISMATCH")

    def test_a_write_outside_the_located_blocks_is_refused_before_it_is_sent(self):
        page = SystemPage()
        heading, _, rows = card.locate(page, "jira")
        scoped = report_region.ScopedClient(page, heading["id"], card.OracleCardError).adopt(*(r["id"] for r in rows))
        with self.assertRaises(card.OracleCardError) as error:
            scoped.call_once("PATCH", "/blocks/jira", {"callout": {"rich_text": []}})
        self.assertEqual(str(error.exception), "WRITE_OUT_OF_SCOPE")
        self.assertEqual(page.patches, [])
        card.write(scoped, heading, rows, self.table(), "System (11/25 · 9:00 AM CT)", True)                  # the heading and the rows themselves are allowed


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
