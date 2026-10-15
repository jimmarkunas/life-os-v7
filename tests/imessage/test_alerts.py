import contextlib
import datetime as dt
import io
import ipaddress
import json
import os
import pathlib
import socket
import sqlite3
import struct
import tempfile
import unittest

from lifeos.imessage import alerts_endpoint as ep, stage
from tests.kit.mysqlite import MySQLite, Cursor

ROOT = pathlib.Path(__file__).resolve().parents[2]
WRAPPER = str(ROOT / "lifeos/imessage/openclaw_imessage.py")
RUNNER, OTHER = "100.64.0.20", "100.64.0.21"                                  # invented tailnet addresses
SIG = b"\r\n\r\n\x00\r\nQUIT\n"
SECRET_TEXT, SECRET_HANDLE, SECRET_NAME = "There was an accident call me now SECRET", "+15550100001", "Alice SECRET"


def v2(src, cmd=0x21):
    a = ipaddress.ip_address(src)
    fam, size, dst = (0x11, 12, ipaddress.IPv4Address("127.0.0.1").packed) if a.version == 4 else (0x21, 36, ipaddress.IPv6Address("::1").packed)
    return SIG + bytes([cmd, fam]) + struct.pack("!H", size) + a.packed + dst + struct.pack("!HH", 50000, 47342)


def whois(ip):
    return {"Node": {"Tags": ["tag:v7-runner"]}} if ip == RUNNER else {"Node": {"Tags": None}}


def message(i, text=SECRET_TEXT, name=SECRET_NAME, group=False, minutes=1):
    return {"ref": "m%d" % i, "chat": "c%d" % (i % 3 + 1), "sent_at": "2026-10-14T14:%02d:00Z" % (59 - minutes), "from_me": False,
            "sender": {"handle": SECRET_HANDLE, "name": name}, "group": group, "chat_name": None, "service": "iMessage", "unread": i % 2 == 0,
            "edited": False, "attachment": False, "text": text, "text_truncated": False}


class FakeWrapper:
    """The real classifier (verdicts) and limits from the installed wrapper, with the paged Mac read replaced by a fixed `scan` result."""

    def __init__(self, recent):
        self.real = ep.load_wrapper(WRAPPER)
        self.MAX_PAGES, self.PAGE_SIZE = self.real.MAX_PAGES, self.real.PAGE_SIZE
        self.recent, self.calls = recent, []

    def scan(self, hours, now=None):
        self.calls.append(hours)
        return self.recent

    def verdicts(self, msgs):
        return self.real.verdicts(msgs)


def recent(msgs, health="CURRENT", truncated=False, pages=1):
    """What the wrapper's paged scan returns: newest first, complete only when the reader proved the end."""
    return {"health": health, "observed_at": "2026-10-14T15:00:00Z", "messages": msgs, "pages": pages,
            "complete": health == "CURRENT" and not truncated, "truncated": truncated}


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.uid = 0                                                           # tailscaled
        d = tempfile.mkdtemp()
        self.key = os.path.join(d, "event-key")
        with open(self.key, "w") as f:
            f.write("ab" * 32)

    def call(self, payload, wrapper):
        out = io.BytesIO()
        original = ep.load_wrapper
        ep.load_wrapper = lambda path=None: wrapper
        try:
            code = ep.main(io.BytesIO(payload), out, whois, "unused", self.key, uid=lambda: self.uid)
        finally:
            ep.load_wrapper = original
        return code, out.getvalue()

    def test_authorized_runner_gets_metadata_only(self):
        w = FakeWrapper(recent([message(1), message(2, text="haha nice", minutes=5), message(3, text="Can you send the form by Friday?", minutes=9)]))
        code, raw = self.call(v2(RUNNER) + b"alerts 3\n", w)
        self.assertEqual(code, 0)
        self.assertEqual(w.calls, [3])
        p = json.loads(raw)
        self.assertEqual((p["health"], p["complete"], p["scanned"], p["limit_reached"], p["lookback_hours"]), ("CURRENT", True, 3, False, 3))
        self.assertEqual([e["severity"] for e in p["events"]], ["TIME_CRITICAL", "ACTION_REQUIRED"])
        self.assertTrue(all(set(e) == {"key", "severity", "reasons", "review"} and len(e["key"]) == 32 for e in p["events"]))
        for secret in (SECRET_TEXT, "SECRET", SECRET_HANDLE, "m1", "c2", "haha", "Friday"):
            self.assertNotIn(secret, raw.decode())
        self.assertEqual(stage.validate(raw, 3, dt.datetime(2026, 10, 14, 15, 0, 30))["events"], p["events"])     # the stage accepts exactly this shape

    def test_keys_are_stable_per_message_and_secret_dependent(self):
        a = ep.event_key(b"k" * 32, "m1")
        self.assertEqual(a, ep.event_key(b"k" * 32, "m1"))
        self.assertNotEqual(a, ep.event_key(b"k" * 32, "m2"))
        self.assertNotEqual(a, ep.event_key(b"j" * 32, "m1"))

    def test_unread_state_does_not_matter(self):
        for m in (dict(message(1), unread=False), dict(message(1), unread=True)):
            code, raw = self.call(v2(RUNNER) + b"alerts 2\n", FakeWrapper(recent([m])))
            self.assertEqual(json.loads(raw)["events"][0]["severity"], "TIME_CRITICAL")

    def test_a_truncated_scan_is_never_complete_but_what_was_read_still_counts(self):
        msgs = [message(i, text="ok", minutes=i % 50) for i in range(2000)]
        msgs[0] = message(0)                                                              # one urgent message among them
        p = json.loads(self.call(v2(RUNNER) + b"alerts 5\n", FakeWrapper(recent(msgs, "DEGRADED", truncated=True, pages=40)))[1])
        self.assertEqual((p["health"], p["complete"], p["limit_reached"], p["scanned"], p["pages"], p["limit"]), ("DEGRADED", False, True, 2000, 40, 2000))
        self.assertEqual([e["severity"] for e in p["events"]], ["TIME_CRITICAL"])

    def test_more_than_fifty_messages_over_several_pages_is_complete(self):
        msgs = [message(i, text="ok", minutes=i % 50) for i in range(137)]
        p = json.loads(self.call(v2(RUNNER) + b"alerts 10\n", FakeWrapper(recent(msgs, pages=3)))[1])
        self.assertEqual((p["health"], p["complete"], p["limit_reached"], p["scanned"], p["pages"]), ("CURRENT", True, False, 137, 3))

    def test_too_many_events_is_incomplete_and_bounded(self):
        msgs = [message(i, text="Can you call me?", minutes=i % 50) for i in range(600)]
        p = json.loads(self.call(v2(RUNNER) + b"alerts 10\n", FakeWrapper(recent(msgs, pages=12)))[1])
        self.assertEqual((p["health"], p["complete"], p["limit_reached"], len(p["events"])), ("DEGRADED", False, True, ep.MAX_EVENTS))

    def test_source_failures_are_incomplete(self):
        for health in ("DISCONNECTED", "DEGRADED", "STALE"):
            p = json.loads(self.call(v2(RUNNER) + b"alerts 2\n", FakeWrapper(recent([], health, pages=0)))[1])
            self.assertEqual((p["health"], p["complete"], p["events"]), (health, False, []))

    def test_refusals_return_nothing(self):
        w = FakeWrapper(recent([message(1)]))
        for payload in (v2(OTHER) + b"alerts 2\n",                     # an untagged tailnet node (Jim's devices, Oracle itself)
                        b"alerts 2\n",                                  # no PROXY header
                        v2(RUNNER, cmd=0x20) + b"alerts 2\n",           # LOCAL command
                        b"PROXY TCP4 %s 127.0.0.1 1 2\r\nalerts 2\n" % RUNNER.encode(),
                        v2(RUNNER) + v2(RUNNER) + b"alerts 2\n",        # a second, forged header is just a bad request
                        v2(RUNNER) + b"alerts 0\n", v2(RUNNER) + b"alerts 73\n", v2(RUNNER) + b"alerts 02\n", v2(RUNNER) + b"recent 2\n",
                        v2(RUNNER) + b"alerts 2; id\n", v2(RUNNER) + b"alerts " + b"9" * 40 + b"\n", v2(RUNNER) + b"GET / HTTP/1.1\r\n"):
            self.assertEqual(self.call(payload, w), (1, b""), payload[-24:])
        self.assertEqual(w.calls, [])                                   # nothing was ever read from the Mac

    def test_a_local_non_root_process_cannot_present_even_a_valid_looking_header(self):
        w = FakeWrapper(recent([message(1)]))
        for uid in (1001, 65534, None):
            self.uid = uid
            self.assertEqual(self.call(v2(RUNNER) + b"alerts 2\n", w), (1, b""), uid)
        self.assertEqual(w.calls, [])

    def test_peer_uid_reads_the_connecting_socket_from_proc_net_tcp(self):
        table = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
                 "   0: 0100007F:B846 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1001        0 1 1\n"     # our listener (47174)
                 "   1: 0100007F:D431 0100007F:B846 01 00000000:00000000 00:00000000 00000000     0        0 2 1\n"     # peer 54321 -> 47174, root
                 "   2: 0100007F:B846 0100007F:D431 01 00000000:00000000 00:00000000 00000000  1001        0 3 1\n")    # our accepted end
        self.assertEqual(ep.peer_uid(table, ("127.0.0.1", 54321), ("127.0.0.1", 47174)), 0)
        self.assertIsNone(ep.peer_uid(table, ("127.0.0.1", 54322), ("127.0.0.1", 47174)))
        self.assertEqual(ep._hex("127.0.0.1", 47342), "0100007F:B8EE")

    def test_peer_uid_on_a_real_loopback_connection_is_this_process_owner(self):
        server = socket.create_server(("127.0.0.1", 0))
        client = socket.create_connection(server.getsockname())
        conn, _ = server.accept()
        try:
            self.assertEqual(ep.loopback_peer_uid(conn.fileno()), os.getuid())
        finally:
            for s in (conn, client, server):
                s.close()

    def test_whois_failure_or_missing_key_fails_closed(self):
        self.assertFalse(ep.authorized(RUNNER, lambda ip: None))
        self.assertFalse(ep.authorized(RUNNER, lambda ip: (_ for _ in ()).throw(OSError())))
        with open(self.key, "w") as f:
            f.write("ab")
        self.assertEqual(self.call(v2(RUNNER) + b"alerts 2\n", FakeWrapper(recent([message(1)]))), (1, b""))


class StageDB(MySQLite):
    """v7_alerts as MySQL has it, with the two dialect bits the stage uses (ON DUPLICATE KEY, DATETIME round-trip)."""

    def __init__(self):
        self.raw = sqlite3.connect(":memory:", isolation_level=None, detect_types=sqlite3.PARSE_DECLTYPES)
        self.fail_on, self.statements = None, []
        self.raw.execute("CREATE TABLE v7_alerts (alert_key VARCHAR(40) PRIMARY KEY, severity VARCHAR(8) NOT NULL, status VARCHAR(8) NOT NULL,"
                         " first_seen TIMESTAMP NOT NULL, last_seen TIMESTAMP NOT NULL, last_notified TIMESTAMP NULL)")

    def cursor(self):
        db = self

        class C(Cursor):
            def execute(self, sql, args=()):
                sql = sql.replace("ON DUPLICATE KEY UPDATE last_seen=VALUES(last_seen)", "ON CONFLICT(alert_key) DO UPDATE SET last_seen=excluded.last_seen")
                return Cursor.execute(self, sql, [a.isoformat(sep=" ") if isinstance(a, dt.datetime) else a for a in args])
        return C(db)

    def commit(self):
        pass

    def rows(self):
        return {k: (s, st) for k, s, st in self.raw.execute("SELECT alert_key, severity, status FROM v7_alerts")}


sqlite3.register_converter("TIMESTAMP", lambda b: dt.datetime.fromisoformat(b.decode()))


def payload(events, observed="2026-10-14T15:00:00Z", hours=2, complete=True, health="CURRENT", scanned=None, reached=False):
    return {"schema": stage.SCHEMA, "observed_at": observed, "lookback_hours": hours, "limit": 2000, "pages": 1 if complete else 2, "sync": "UNVERIFIED",
            "events": events, "health": health, "complete": complete, "scanned": len(events) if scanned is None else scanned, "limit_reached": reached}


def ev(key, severity="ACTION_REQUIRED", review=False):
    return {"key": key * 32 if len(key) == 1 else key, "severity": severity, "reasons": ["deadline_request"], "review": review}


DAY = dt.datetime(2026, 10, 14, 15, 0)        # 10 AM Chicago
NIGHT = dt.datetime(2026, 10, 15, 8, 0)       # 3 AM Chicago


class Stage(unittest.TestCase):
    def runs(self, db, now, body, live=True, accept=True):
        pushes = []

        def send(topic, title, text, severity):
            pushes.append((topic, title, text, severity))
            return accept
        raw = (json.dumps(body) + "\n").encode() if isinstance(body, dict) else body
        counts = stage.run(0, live, {"IMESSAGE_ALERTS_HOST": "oracle.example", "NTFY_TOPIC": "topic"}, now,
                           lambda host, hours: raw if not isinstance(body, dict) or body["lookback_hours"] == hours else b"",
                           lambda: contextlib.nullcontext(db), send)
        return counts, pushes

    def test_one_push_per_event_and_none_for_unchanged_reruns(self):
        db = StageDB()
        counts, pushes = self.runs(db, DAY, payload([ev("a"), ev("b", "ATTENTION")]))
        self.assertEqual((counts["new"], counts["pushed"], counts["failed"], counts["attention"]), (1, 1, 0, 1))
        self.assertEqual(pushes, [("topic", "Text needs a reply", "1 text needs a reply. Open Messages.", "info")])
        again, pushes = self.runs(db, DAY + dt.timedelta(minutes=60), payload([ev("a")], observed="2026-10-14T16:00:00Z"))
        self.assertEqual((again["new"], again["deliver"], pushes), (0, 0, []))                  # same event, next hour: silent
        self.assertNotIn("OPEN", {st for _, st in db.rows().values()})                         # never in the Jobs alert stage's OPEN set

    def test_escalation_is_a_new_version(self):
        db = StageDB()
        self.runs(db, DAY, payload([ev("a")]))
        counts, pushes = self.runs(db, DAY + dt.timedelta(hours=1), payload([ev("a", "TIME_CRITICAL")], observed="2026-10-14T16:00:00Z"))
        self.assertEqual((counts["new"], pushes[0][1], pushes[0][3]), (1, "Urgent text message", "page"))

    def test_quiet_hours_queue_action_required_and_deliver_after_8am(self):
        db = StageDB()
        counts, pushes = self.runs(db, NIGHT, payload([ev("a")], observed="2026-10-15T08:00:00Z"))
        self.assertEqual((counts["deliver"], counts["queued_quiet"], pushes), (0, 1, []))
        later = dt.datetime(2026, 10, 15, 13, 5)                                                # 8:05 AM Chicago; the event is outside this scan's window
        counts, pushes = self.runs(db, later, payload([], observed="2026-10-15T13:05:00Z", hours=6))
        self.assertEqual((counts["deliver"], len(pushes)), (1, 1))

    def test_time_critical_bypasses_quiet_hours_but_never_on_review(self):
        db = StageDB()
        counts, pushes = self.runs(db, NIGHT, payload([ev("a", "TIME_CRITICAL")], observed="2026-10-15T08:00:00Z"))
        self.assertEqual((counts["deliver"], pushes[0][3]), (1, "page"))
        db = StageDB()
        counts, pushes = self.runs(db, NIGHT, payload([ev("b", "TIME_CRITICAL", review=True)], observed="2026-10-15T08:00:00Z"))
        self.assertEqual((counts["new"], pushes), (0, []))

    def test_rejected_push_stays_queued_and_is_retried(self):
        db = StageDB()
        counts, _ = self.runs(db, DAY, payload([ev("a")]), accept=False)
        self.assertEqual((counts["pushed"], counts["failed"]), (0, 1))
        counts, pushes = self.runs(db, DAY + dt.timedelta(hours=1), payload([ev("a")], observed="2026-10-14T16:00:00Z"))
        self.assertEqual((counts["deliver"], counts["pushed"], len(pushes)), (1, 1, 1))

    def test_a_lost_receipt_after_an_accepted_push_never_pushes_again(self):
        db = StageDB()
        db.fail_on = ("SET status=%s WHERE alert_key=%s AND status=%s", 1)      # the receipt after the accepted push fails (the claim also sets last_notified)
        with self.assertRaises(RuntimeError):
            self.runs(db, DAY, payload([ev("a")]))
        self.assertEqual(db.rows()["imsg:" + "a" * 24 + ":ar"], ("ar", stage.PENDING))
        counts, pushes = self.runs(db, DAY + dt.timedelta(hours=1), payload([ev("a")], observed="2026-10-14T16:00:00Z"))
        self.assertEqual((pushes, counts["ambiguous"], counts["failed"], counts["deliver"]), ([], 1, 1, 0))
        self.assertEqual(db.rows()["imsg:" + "a" * 24 + ":ar"], ("ar", stage.UNKNOWN))                 # never claimed as delivered
        counts, pushes = self.runs(db, DAY + dt.timedelta(hours=2), payload([ev("a")], observed="2026-10-14T17:00:00Z"))
        self.assertEqual((pushes, counts["ambiguous"], counts["failed"]), ([], 0, 0))

    def test_a_database_failure_before_the_push_sends_nothing_and_the_next_run_sends_once(self):
        db = StageDB()
        db.fail_on = ("INSERT INTO v7_alerts", 1)
        sent = []
        with self.assertRaises(RuntimeError):
            stage.run(0, True, {"IMESSAGE_ALERTS_HOST": "h", "NTFY_TOPIC": "t"}, DAY, lambda h, n: (json.dumps(payload([ev("a")])) + "\n").encode(),
                      lambda: contextlib.nullcontext(db), lambda *a: sent.append(a) or True)
        self.assertEqual(sent, [])
        counts, pushes = self.runs(db, DAY + dt.timedelta(minutes=5), payload([ev("a")], observed="2026-10-14T15:05:00Z"))
        self.assertEqual((len(pushes), counts["pushed"], counts["failed"]), (1, 1, 0))

    def test_undelivered_events_expire_after_a_day(self):
        db = StageDB()
        self.runs(db, DAY, payload([ev("a")]), accept=False)
        counts, pushes = self.runs(db, DAY + dt.timedelta(hours=25), payload([], observed="2026-10-15T16:00:00Z", hours=3))
        self.assertEqual((counts["expired"], pushes), (1, []))

    def test_incomplete_scans_fail_and_never_advance_the_watermark(self):
        for body in (payload([ev("a")], complete=False, health="DEGRADED", scanned=50, reached=True), payload([], complete=False, health="DISCONNECTED"),
                     b"", b"not json\n"):
            db = StageDB()
            counts, _ = self.runs(db, DAY, body)
            self.assertEqual(counts["failed"], 1)
            self.assertNotIn(stage.MARK, db.rows())
        db = StageDB()
        counts, pushes = self.runs(db, DAY, payload([ev("a")], complete=False, health="DEGRADED", scanned=50, reached=True))
        self.assertEqual((counts["new"], len(pushes)), (1, 1))                                  # what was seen is still delivered

    def test_watermark_window_and_coverage_gap(self):
        self.assertEqual(stage.window(DAY, None), (2, False))
        self.assertEqual(stage.window(DAY, DAY - dt.timedelta(minutes=50)), (2, False))
        self.assertEqual(stage.window(DAY, DAY - dt.timedelta(hours=5, minutes=10)), (7, False))
        self.assertEqual(stage.window(DAY, DAY - dt.timedelta(hours=100)), (72, True))
        db = StageDB()
        self.runs(db, DAY, payload([]))
        self.assertIn(stage.MARK, db.rows())
        counts, _ = self.runs(db, DAY + dt.timedelta(hours=3), payload([], observed="2026-10-14T18:00:00Z", hours=4))
        self.assertEqual((counts["lookback_hours"], counts["complete"]), (4, 1))

    def test_stale_replayed_or_off_contract_answers_are_refused(self):
        good = payload([ev("a")])
        self.assertIsNotNone(stage.validate((json.dumps(good) + "\n").encode(), 2, DAY))
        for bad in (dict(good, observed_at="2026-10-14T14:50:00Z"), dict(good, lookback_hours=3), dict(good, text="hi"), dict(good, sync="CURRENT"),
                    dict(good, events=[dict(ev("a"), text="hi")]), dict(good, events=[dict(ev("a"), key="m12")]),
                    dict(good, events=[dict(ev("a"), reasons=["sender:Alice"])]), dict(good, events=[ev("a", "CLEAR")]), dict(good, limit=50000),
                    dict(good, limit_reached=True), dict(good, pages=0), dict(good, health="DEGRADED"), dict(good, scanned=2001)):
            self.assertIsNone(stage.validate((json.dumps(bad) + "\n").encode(), 2, DAY), bad)

    def test_pushes_never_carry_keys_or_message_data(self):
        db = StageDB()
        _, pushes = self.runs(db, DAY, payload([ev("a"), ev("b", "TIME_CRITICAL")]))
        text = json.dumps(pushes)
        self.assertNotIn("a" * 24, text)
        self.assertNotIn("deadline_request", text)
        self.assertEqual(pushes[0][1:], ("Urgent text message", "1 text may need you now. Open Messages.", "page"))

    def test_dry_run_writes_and_pushes_nothing(self):
        db = StageDB()
        counts, pushes = self.runs(db, DAY, payload([ev("a")]), live=False)
        self.assertEqual((counts["new"], pushes, db.rows()), (1, [], {}))

    def test_no_endpoint_configured_is_a_failure(self):
        counts = stage.run(0, False, {}, DAY, lambda h, n: b"", lambda: contextlib.nullcontext(StageDB()), lambda *a: True)
        self.assertEqual((counts["health"], counts["failed"]), ("NO_ENDPOINT", 1))


if __name__ == "__main__":
    unittest.main()


class WrapperScan(unittest.TestCase):
    """openclaw_imessage.scan over a fake paged source: complete only on a proven end; anything uncertain is incomplete."""
    from lifeos.imessage import openclaw_imessage as w
    NOW = dt.datetime(2026, 10, 14, 15, 0, tzinfo=dt.timezone.utc)

    def page(self, ids, more, cursor=True, health="CURRENT"):
        msgs = [dict(message(i, text="ok", minutes=1), sent_at="2026-10-14T14:%02d:%02dZ" % (i // 60 % 60, i % 60)) for i in ids]
        state = {"channel": "imessage", "health": health, "sync": "UNVERIFIED", "observed_at": "2026-10-14T15:00:00Z", "source": "Apple Messages / Mac Mini",
                 "messages": msgs if health == "CURRENT" else None, "contacts": "CURRENT", "limit": 50, "more": more if health == "CURRENT" else None,
                 "cursor": ({"date": 1000 + ids[-1], "rowid": ids[-1]} if ids and cursor else None) if health == "CURRENT" else None}
        body = {"content": [{"type": "text", "text": json.dumps(state)}], "structuredContent": state, "isError": health != "CURRENT"}
        return (json.dumps({"jsonrpc": "2.0", "id": 1, "result": body}) + "\n").encode()

    def run_pages(self, pages, clock=None):
        endpoint = os.path.join(tempfile.mkdtemp(), "endpoint")
        with open(endpoint, "w") as f:
            f.write("mac.example 47331\n")
        requests = []

        def fetcher(host, port, request, limit):
            requests.append(request)
            if not pages:
                raise OSError("gone")
            return pages.pop(0)
        out = self.w.scan(10, self.NOW, endpoint, fetcher, clock or (lambda: 0), lambda: self.NOW)
        return out, requests

    def test_pages_until_the_reader_proves_the_end(self):
        out, req = self.run_pages([self.page(range(1, 51), True), self.page(range(51, 101), True), self.page(range(101, 138), False)])
        self.assertEqual((out["health"], out["complete"], out["pages"], len(out["messages"])), ("CURRENT", True, 3, 137))
        self.assertEqual(req, [b"page 10\n", b"next 1050 50\n", b"next 1100 100\n"])
        self.assertEqual(out["messages"][0]["ref"], "m137")                                     # newest first for classification

    def test_uncertain_coverage_is_incomplete_but_keeps_what_was_read(self):
        cases = {
            "short page claiming more": [self.page(range(1, 31), True)],
            "cursor went backwards": [self.page(range(51, 101), True), self.page(range(1, 51), True)],
            "message repeated across pages": [self.page(range(1, 51), True), self.page(range(50, 100), False)],
            "source failed mid-scan": [self.page(range(1, 51), True), self.page([], False, health="DISCONNECTED")],
            "connection lost mid-scan": [self.page(range(1, 51), True)],
            "malformed page": [self.page(range(1, 51), True), b"not json\n"],
            "cursor not the last message": [self.page(range(1, 51), True, cursor=False)],
        }
        for name, pages in cases.items():
            out, _ = self.run_pages(pages)
            self.assertFalse(out["complete"], name)
            self.assertNotEqual(out["health"], "CURRENT", name)
            self.assertTrue(len(out["messages"]) <= 50, name)

    def test_page_and_time_caps_are_incomplete(self):
        pages = [self.page(range(1 + 50 * k, 51 + 50 * k), True) for k in range(self.w.MAX_PAGES + 2)]
        out, req = self.run_pages(pages)
        self.assertEqual((out["complete"], out["truncated"], out["pages"], len(req)), (False, True, self.w.MAX_PAGES, self.w.MAX_PAGES))
        ticks = iter([0, 0, 10, 20, 40, 50])
        out, req = self.run_pages([self.page(range(1 + 50 * k, 51 + 50 * k), True) for k in range(5)], clock=lambda: next(ticks))
        self.assertEqual((out["complete"], out["truncated"]), (False, True))

    def test_an_empty_window_is_complete(self):
        out, _ = self.run_pages([self.page([], False)])
        self.assertEqual((out["health"], out["complete"], out["messages"], out["pages"]), ("CURRENT", True, [], 1))


class Recovery(unittest.TestCase):
    def test_events_already_recorded_by_a_degraded_run_are_not_pushed_again(self):
        db = StageDB()
        degraded = payload([ev("a")], complete=False, health="DEGRADED", scanned=50, reached=True)
        counts, pushes = Stage.runs(Stage(), db, DAY, degraded)
        self.assertEqual((counts["new"], len(pushes), counts["failed"]), (1, 1, 1))
        self.assertNotIn(stage.MARK, db.rows())                                                  # watermark NOT advanced by the incomplete scan
        later = DAY + dt.timedelta(hours=1)
        hours, _ = stage.window(later, None)
        counts, pushes = Stage.runs(Stage(), db, later, payload([ev("a"), ev("b")], observed="2026-10-14T16:00:00Z", hours=hours))
        self.assertEqual((counts["new"], counts["deliver"], counts["complete"], counts["failed"]), (1, 1, 1, 0))
        self.assertEqual(pushes[0][2], "1 text needs a reply. Open Messages.")                   # only the new event
        self.assertIn(stage.MARK, db.rows())
