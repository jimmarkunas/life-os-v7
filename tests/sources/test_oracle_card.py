"""step 4d: the System card stage: Oracle's stats, Codex read once, the Connections row from the System health database,
one write in place.
"""

import contextlib
import datetime as dt
import io
import json
import unittest
from unittest import mock

from lifeos.jobs.fit import edge
from lifeos.oraclecloud import card, system_table as st
from lifeos.platform import connections, report_region
from lifeos.sources.cards import oracle_card
from tests.oraclecloud.support import SystemPage, default_rows
from tests.platform.test_connections import GOOD, database
from tests.oraclecloud.test_system_table import limits as good_limits

NOW = dt.datetime(2026, 11, 25, 15, 0, tzinfo=dt.timezone.utc)  # 9:00 AM in Chicago
ENV = {
    "IMESSAGE_ALERTS_HOST": " 100.64.0.9 ",
    "JIRA_CARD_BLOCK_ID": "jira",
    "NOTION_JIRA_TOKEN": "unused",
    "LIFEOS_NOTION_SYSTEM_HEALTH_SOURCE": "src-id",
}


def setUpModule():  # no test ever reaches the endpoint
    global _patch
    _patch = mock.patch.object(card, "fetch", lambda host, port=card.PORT, request=b"": b"")
    _patch.start()


def tearDownModule():
    _patch.stop()


SVC = {"gateway": True, "telegram": True, "whatsapp": True, "alerts": True}


def stats_raw(**over):
    base = {
        "schema": card.SCHEMA,
        "observed_at": "2026-11-25T14:59:30Z",
        "cost": {"amount": 0, "currency": "USD"},
        "cpu_24h_pct": 5.4,
        "memory_pct": 23.0,
        "disk_pct": 66.0,
        "a1_cores": {"used": 4, "available": 0},
        "reclaim": "NONE",
        "services": dict(SVC),
        "chatgpt": "unreported",
        "backup": {"ok": True, "code": "ok", "age_h": 3},
        "honcho": {
            "health": True,
            "failed": False,
            "exhausted": False,
            "stopped": False,
            "backup": {"at": "2026-11-24T09:30:00Z", "ok": True},
            "meter": {"cap": 25, "reserved": 0.7},
        },
        "anthropic": {"billed_usd": 0.02, "honcho_usd": 0.1, "as_of": "2026-11-25T14:30:00Z"},
    }
    return json.dumps({**base, **over}).encode()


def reading():
    return good_limits(read_at="2026-11-25T14:59:00Z"), dt.datetime(2026, 11, 25, 14, 59, tzinfo=dt.timezone.utc)


class Run(unittest.TestCase):
    def run_card(self, page, live=True, raw=None, lim=None, seen=GOOD, env=None):
        calls = {"limits": 0}

        def limits(host):
            calls["limits"] += 1
            return lim() if callable(lim) else (lim or reading())

        out = io.StringIO()
        noop = lambda *a, **k: None
        with (
            mock.patch.object(report_region, "require_configured", lambda ids, fail: None),
            contextlib.redirect_stdout(out),
        ):
            counts = oracle_card.run(
                0, live, env or ENV, NOW, lambda host: raw or stats_raw(), page, limits, seen, noop, noop, noop
            )
        return counts, calls, out.getvalue()

    def test_a_live_run_writes_the_sixteen_rows_and_the_stamped_heading_in_place(self):
        page = database()
        counts, calls, _ = self.run_card(page)
        self.assertEqual(
            (counts["rows"], counts["stale"], counts["oracle"], counts["limits"], counts["connections"]),
            (16, 0, 1, 1, "5/6"),
        )
        self.assertEqual(page.plain("sys-h"), "System (11/25 · 9:00 AM CT)")
        table = page.row_text()
        self.assertEqual([r[0] for r in table[1:]], list(st.LABELS))
        rows = {r[0]: r for r in table[1:]}
        self.assertEqual(
            (rows["Oracle · Memory"][2], rows["Codex · 5-hour"][2], rows["Connections"][2], rows["Connections"][4]),
            ("23%", "7% used", "5 of 6 connected · Concord not connected · 3 off by design", "CONCORD DOWN"),
        )
        self.assertEqual({r[4] for k, r in rows.items() if k != "Connections"}, {"OK"})
        self.assertEqual(calls["limits"], 1)  # Codex is read ONCE for the whole table

    def test_only_the_heading_and_the_table_rows_are_ever_written(self):
        page = database()
        self.run_card(page)
        self.assertTrue(set(p[0] for p in page.patches) <= {"sys-h"} | {f"sys-r{i}" for i in range(17)})
        self.assertEqual(page.plain("sys-h").split(" (")[0], "System")
        self.assertNotIn("jira", {p[0] for p in page.patches})

    def test_a_dry_run_resolves_the_block_and_writes_nothing_anywhere(self):
        page = database()
        counts, _, _ = self.run_card(page, live=False)
        self.assertEqual(
            (counts["rows_changed"], counts["rows_written"], counts["written"], page.patches, page.log),
            (16, 0, 0, [], []),
        )

    def test_the_second_run_writes_nothing_new(self):
        page = database()
        self.run_card(page)
        mark, pages = len(page.patches), len(page.log)
        counts, _, _ = self.run_card(page)
        self.assertEqual(
            (counts["rows_changed"], counts["connections_written"], len(page.patches) - mark, len(page.log) - pages),
            (0, 0, 0, 0),
        )

    def test_the_health_database_rows_are_refreshed_and_the_todoist_row_is_retired(self):
        page = database()
        counts, _, _ = self.run_card(page)
        self.assertEqual((counts["connections_written"], counts["todoist_row_retired"]), (5, 1))
        self.assertEqual(
            page.pages["pg-health:personal:jira"]["properties"]["Last OK"]["rich_text"][0]["plain_text"],
            "11/25 · 8:40 AM CT",
        )
        self.assertTrue(page.pages["pg-health:personal:todoist"].get("in_trash"))
        self.assertEqual(
            page.pages["pg-health:all:concord"]["properties"]["Health"]["select"]["name"], connections.NOT_CONNECTED
        )

    def test_oracle_unreachable_goes_stale_row_by_row_and_the_run_still_writes(self):
        page = database()
        self.run_card(page)

        def refuse(host):
            raise OSError()

        noop = lambda *a, **k: None
        later = NOW + dt.timedelta(minutes=58)
        with (
            mock.patch.object(report_region, "require_configured", lambda ids, fail: None),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            counts = oracle_card.run(0, True, ENV, later, refuse, page, lambda host: reading(), GOOD, noop, noop, noop)
        rows = {r[0]: r for r in page.row_text()[1:]}
        self.assertEqual((counts["oracle"], counts["stale"]), (0, 9))
        self.assertEqual(
            rows["Oracle · Memory"][2:], ["23%", "—", "STALE since 9:00 AM"]
        )  # the last reading is kept, never zero
        self.assertEqual(rows["Codex · Week"][4], "OK")

    def test_limits_that_cannot_be_read_make_only_the_limits_rows_stale_with_a_fixed_code_in_the_log(self):
        page = database()
        counts, _, _ = self.run_card(page, lim=lambda: (None, None))
        self.assertEqual((counts["limits"], counts["stale"]), (0, 6))

        def boom(host):
            raise edge.EdgeError("FIT_EDGE_NO_REPLY")

        out = io.StringIO()
        with mock.patch.object(edge, "limits", boom), contextlib.redirect_stdout(out):
            self.assertEqual(oracle_card.limits_of("h"), (None, None))
        self.assertIn('oracle-card: limits unavailable {"code": "FIT_EDGE_NO_REPLY"}', out.getvalue())

    def test_an_unreadable_health_database_goes_stale_on_the_connections_row_only(self):
        page = database()
        real = page.call

        def call(method, path, body=None):
            if method == "POST":
                raise card.NotionError("NOTION_HTTP_404")
            return real(method, path, body)

        page.call = call
        counts, _, _ = self.run_card(page)
        self.assertEqual((counts["connections"], counts["stale"]), ("unread", 1))

    def test_without_the_health_source_setting_the_connections_row_goes_stale_and_nothing_else_changes(self):
        page = database()
        counts, _, _ = self.run_card(
            page, env={k: v for k, v in ENV.items() if k != "LIFEOS_NOTION_SYSTEM_HEALTH_SOURCE"}
        )
        self.assertEqual((counts["connections"], counts["stale"], counts["written"] > 0), ("unread", 1, True))
        self.assertEqual(page.log, [])  # no database row is touched

    def test_not_configured_without_a_host_or_the_jira_anchor(self):
        for env in ({"JIRA_CARD_BLOCK_ID": "jira"}, {"IMESSAGE_ALERTS_HOST": "h"}):
            with self.assertRaises(card.OracleCardError) as error:
                self.run_card(database(), env=env)
            self.assertEqual(str(error.exception), "ORACLE_NOT_CONFIGURED")

    def test_a_page_with_no_system_callout_writes_nothing(self):
        page = SystemPage("Oracle Cloud")
        with self.assertRaises(card.OracleCardError) as error:
            self.run_card(page)
        self.assertEqual((str(error.exception), page.patches), ("CARD_NOT_OWNED", []))


def concord_reply(ok=True, code="", messages=3, at=None):
    at = at if at is not None else int(NOW.timestamp()) - 60
    return json.dumps({"ok": ok, "code": code, "messages": messages, "at": at}, sort_keys=True).encode() + b"\n"


class ConcordRow(unittest.TestCase):
    def run_card(self, page, reply, now=NOW, seen=GOOD, allowed=None):
        out = io.StringIO()
        noop = lambda *a, **k: None
        with (
            mock.patch.object(report_region, "require_configured", lambda ids, fail: None),
            contextlib.redirect_stdout(out),
        ):
            counts = oracle_card.run(
                0,
                True,
                ENV,
                now,
                lambda host: stats_raw(),
                page,
                lambda host: reading(),
                seen,
                noop,
                noop,
                noop,
                concord_fetch=lambda host, request: reply,
            )
        return counts, out.getvalue()

    def health(self, page):
        props = page.pages["pg-health:all:concord"]["properties"]
        return props["Health"]["select"]["name"], props["Last OK"]["rich_text"][0]["plain_text"]

    def test_a_good_read_makes_the_concord_row_ok_with_its_read_time_and_the_line_reads_twelve_of_twelve(self):
        page = database()
        counts, log = self.run_card(page, concord_reply())
        self.assertEqual(self.health(page), (connections.OK, "11/25 · 8:59 AM CT"))
        self.assertEqual((counts["concord"], counts["connections"]), ("ok", "6/6"))
        rows = {r[0]: r for r in page.row_text()[1:]}
        self.assertEqual((rows["Connections"][2], rows["Connections"][4]), ("6 of 6 connected · 3 off by design", "OK"))
        self.assertIn('"messages": 3', log)
        self.assertNotIn("sender", log)

    def test_cli_missing_gives_the_fixed_code_and_a_row_that_was_never_good_stays_not_connected(self):
        page = database()
        counts, log = self.run_card(page, concord_reply(False, "CONCORD_CLI_MISSING", 0))
        self.assertEqual(counts["concord"], "CONCORD_CLI_MISSING")
        self.assertEqual(self.health(page)[0], connections.NOT_CONNECTED)
        self.assertIn("CONCORD_CLI_MISSING", log)
        rows = {r[0]: r for r in page.row_text()[1:]}
        self.assertEqual(rows["Connections"][4], "CONCORD DOWN")

    def test_a_failed_read_keeps_the_last_good_time_and_the_row_stays_connected_for_two_and_a_half_hours(self):
        page = database()
        self.run_card(page, concord_reply())  # good read at 8:59 AM CT
        later = NOW + dt.timedelta(hours=2, minutes=20)
        fresh = lambda at: {
            job: {"last_ok": at, "failed": False} for job in ("jira", "agenda", "recruiters", "followup")
        }  # the job-backed rows stay fresh
        counts, _ = self.run_card(
            page, concord_reply(False, "CONCORD_READ_FAILED", 0), later, fresh(later - dt.timedelta(minutes=5))
        )
        self.assertEqual(self.health(page), (connections.OK, "11/25 · 8:59 AM CT"))  # unchanged
        self.assertEqual(counts["connections"], "6/6")  # 2h20m: still inside 2.5 hours
        counts, _ = self.run_card(
            page,
            concord_reply(False, "CONCORD_READ_FAILED", 0),
            NOW + dt.timedelta(hours=2, minutes=40),
            fresh(NOW + dt.timedelta(hours=2, minutes=35)),
        )
        self.assertEqual(counts["connections"], "5/6")  # 2h40m: DOWN
        rows = {r[0]: r for r in page.row_text()[1:]}
        self.assertEqual(rows["Connections"][4], "CONCORD DOWN · STALE since 8:59 AM")

    def test_the_reader_is_refused_when_concord_is_not_on_the_allow_list(self):
        from lifeos.inbox import concord_reader
        from lifeos.inbox.model import InboxError

        called = []
        with self.assertRaises(InboxError) as caught:
            concord_reader.health("host", lambda h, r: called.append(r), allowed=frozenset())
        self.assertEqual((str(caught.exception), called), ("live_reads_not_authorized", []))
        with mock.patch("lifeos.inbox.sources.LIVE_READ_SOURCES", frozenset()):
            counts, _ = self.run_card(database(), concord_reply())
        self.assertEqual(counts["concord"], "live_reads_not_authorized")  # the stage still writes


if __name__ == "__main__":
    unittest.main()
