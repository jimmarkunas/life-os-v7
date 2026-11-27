"""Urgent iMessage alerts in the hourly run: classify Oracle's alert metadata and push the phone through ntfy.

Input is keyed event ids, severity, reason codes and completeness only (no text, names or chat ids); the private
v7_alerts table is the only state. INFORMATIONAL and ATTENTION never push; ACTION_REQUIRED pushes once per event and
waits out quiet hours (10 PM-8 AM America/Chicago); TIME_CRITICAL (never a `review` verdict) pushes at any hour. One
push per event version (key = event id + severity); a rejected push stays queued for up to 24 h.
Coverage: the lookback runs from the last COMPLETE scan (`imsg_scan_ok` marker) plus an hour, 2-72 h; a longer gap, a
scan that hit the reader's limit, or a bad endpoint is `failed`, never complete.
Delivery is claim-then-send: rows about to go out are marked IMSG_P and committed BEFORE the push; the receipt sets
IMSG_S or back to IMSG_Q. A row still IMSG_P next run has an unknown outcome: it becomes IMSG_U WITHOUT a second push
and is reported `ambiguous` and `failed`. The IMSG_* statuses are this stage's own; 'OPEN' belongs to the Jobs alert
stage."""
import datetime as dt
import json
import math
import os
import re
import socket
from zoneinfo import ZoneInfo

from lifeos.platform import alerts, db

PORT = 47341
SCHEMA = "lifeos.imessage.alerts.v2"
TZ = ZoneInfo("America/Chicago")
QUIET_FROM, QUIET_UNTIL = 22, 8
PREFIX, MARK = "imsg:", "imsg_scan_ok"
QUEUED, PENDING, SENT, UNKNOWN, EXPIRED, MARKER = "IMSG_Q", "IMSG_P", "IMSG_S", "IMSG_U", "IMSG_X", "IMSG_M"
FIRST, MIN_LOOKBACK, MAX_LOOKBACK = 2, 2, 72
DELIVER_FOR, KEEP = dt.timedelta(hours=24), dt.timedelta(days=14)
CODE = {"TIME_CRITICAL": "tc", "ACTION_REQUIRED": "ar"}
SEVERITIES = ("ATTENTION", "ACTION_REQUIRED", "TIME_CRITICAL")
REASONS = {"emergency_language", "unknown_sender", "group_conversation", "urgent_same_day_deadline", "interview_scheduling", "deadline_request", "urgent_language",
           "direct_request", "mentions_jim", "no_signal", "no_text", "automated_or_code"}
MAX_REPLY, MAX_EVENTS = 131072, 500


def window(now, mark):
    if mark is None:
        return FIRST, False
    hours = math.ceil((now - mark).total_seconds() / 3600) + 1
    return min(max(hours, MIN_LOOKBACK), MAX_LOOKBACK), hours > MAX_LOOKBACK


def fetch(host, hours, port=PORT):
    """One request over the tailnet. No half-close: Tailscale Serve drops the connection on the client's FIN. Empty
    bytes = refused or unreachable."""
    try:
        with socket.create_connection((host, port), timeout=15) as s:
            s.settimeout(40)
            s.sendall(b"alerts %d\n" % hours)
            data = b""
            while len(data) <= MAX_REPLY:
                chunk = s.recv(MAX_REPLY + 1 - len(data))
                if not chunk:
                    break
                data += chunk
            return data
    except OSError:
        return b""


def _when(text):
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", text):
        raise ValueError
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


def validate(raw, hours, now):
    if not raw or len(raw) > MAX_REPLY or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        return None
    try:
        p = json.loads(raw)
        ok = (isinstance(p, dict) and set(p) == {"schema", "observed_at", "lookback_hours", "limit", "pages", "sync", "events", "health", "complete", "scanned",
                                                 "limit_reached"}
              and p["schema"] == SCHEMA and p["lookback_hours"] == hours and p["sync"] == "UNVERIFIED" and isinstance(p["complete"], bool)
              and isinstance(p["limit_reached"], bool) and p["health"] in ("CURRENT", "DEGRADED", "DISCONNECTED", "STALE")
              and isinstance(p["limit"], int) and 50 <= p["limit"] <= 5000 and isinstance(p["pages"], int) and 0 <= p["pages"] <= 100
              and isinstance(p["scanned"], int) and 0 <= p["scanned"] <= p["limit"] and isinstance(p["events"], list) and len(p["events"]) <= MAX_EVENTS
              and (not p["complete"] or (p["health"] == "CURRENT" and not p["limit_reached"] and p["pages"] >= 1))
              and all(isinstance(e, dict) and set(e) == {"key", "severity", "reasons", "review"} and re.fullmatch(r"[0-9a-f]{32}", str(e["key"]))
                      and e["severity"] in SEVERITIES and isinstance(e["review"], bool) and isinstance(e["reasons"], list) and set(e["reasons"]) <= REASONS
                      for e in p["events"]))
        observed = _when(p["observed_at"]) if ok else None
    except (ValueError, KeyError, TypeError):
        return None
    if not ok or abs((now - observed).total_seconds()) > 120:
        return None
    return p


def quiet(now):
    hour = now.replace(tzinfo=dt.timezone.utc).astimezone(TZ).hour
    return hour >= QUIET_FROM or hour < QUIET_UNTIL


def plan(payload, rows, now):
    """Pure: payload (None when the scan failed) + stored imsg rows -> what to write and push.

    rows: {key: {"status", "severity", "first_seen", "last_seen"}}."""
    events = [] if payload is None else payload["events"]
    found = {}
    for e in events:
        if e["severity"] == "TIME_CRITICAL" and e["review"]:
            continue                                                # a TIME_CRITICAL verdict is never a review one; refuse rather than page on doubt
        if e["severity"] in CODE:
            found[PREFIX + e["key"][:24] + ":" + CODE[e["severity"]]] = CODE[e["severity"]]
    new = {k: c for k, c in found.items() if k not in rows}
    seen = [k for k in found if k in rows]
    queued = {k: r["severity"] for k, r in rows.items() if r["status"] == QUEUED and now - r["first_seen"] <= DELIVER_FOR}
    queued.update(new)
    deliver = sorted(k for k, c in queued.items() if c == "tc" or not quiet(now))
    expire = sorted(k for k, r in rows.items() if r["status"] == QUEUED and now - r["first_seen"] > DELIVER_FOR)
    purge = sorted(k for k, r in rows.items() if r["status"] in (SENT, UNKNOWN, EXPIRED) and now - r["last_seen"] > KEEP)
    tc = sum(1 for k in deliver if queued[k] == "tc")
    push = None
    if deliver:
        n = len(deliver)
        push = (("Urgent text message" if tc else "Text needs a reply"),
                ("%d text%s may need you now" % (tc, "" if tc == 1 else "s") if tc else "%d text%s need%s a reply" % (n, "" if n == 1 else "s", "s" if n == 1 else ""))
                + ". Open Messages.", alerts.PAGE)
    ambiguous = sorted(k for k, r in rows.items() if r["status"] == PENDING)
    return {"new": new, "seen": seen, "deliver": deliver, "expire": expire, "purge": purge, "push": push, "ambiguous": ambiguous,
            "attention": sum(1 for e in events if e["severity"] == "ATTENTION"), "queued_quiet": len(queued) - len(deliver)}


def _rows(cursor):
    cursor.execute("SELECT alert_key, severity, status, first_seen, last_seen FROM v7_alerts WHERE alert_key LIKE %s", (PREFIX + "%",))
    return {k: {"severity": s, "status": st, "first_seen": f, "last_seen": l} for k, s, st, f, l in cursor.fetchall()}


def _mark(cursor):
    cursor.execute("SELECT last_seen FROM v7_alerts WHERE alert_key = %s AND status = %s", (MARK, MARKER))
    row = cursor.fetchone()
    return row[0] if row else None


KEY = "imessage_action"                       # the registered alert every iMessage push is (PAGE; quiet hours still hold action-required ones)


def _gateway(topic, title, body, severity):
    return alerts.push(KEY, title, body, topic)


def run(limit, live, environ=os.environ, now=None, fetcher=fetch, connect=db.connect, send=None):
    send = send or _gateway
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    host = environ.get("IMESSAGE_ALERTS_HOST", "")
    with connect() as connection:
        with connection.cursor() as cursor:
            rows, mark = _rows(cursor), _mark(cursor)
        hours, gap = window(now, mark)
        payload = validate(fetcher(host, hours), hours, now) if host else None
        p = plan(payload, rows, now)
        accepted = False
        if live:
            with connection.cursor() as cursor:
                for key, code in p["new"].items():
                    cursor.execute("INSERT INTO v7_alerts (alert_key, severity, status, first_seen, last_seen, last_notified) VALUES (%s,%s,%s,%s,%s,NULL)",
                                   (key, code, QUEUED, now, now))
                for key in p["seen"]:
                    cursor.execute("UPDATE v7_alerts SET last_seen=%s WHERE alert_key=%s", (now, key))
                for key in p["ambiguous"]:                          # a lost receipt from an earlier run: never pushed twice, never recorded as sent
                    cursor.execute("UPDATE v7_alerts SET status=%s WHERE alert_key=%s AND status=%s", (UNKNOWN, key, PENDING))
                for key in p["expire"]:
                    cursor.execute("UPDATE v7_alerts SET status=%s WHERE alert_key=%s", (EXPIRED, key))
                for key in p["purge"]:
                    cursor.execute("DELETE FROM v7_alerts WHERE alert_key=%s AND status IN (%s, %s, %s)", (key, SENT, UNKNOWN, EXPIRED))
                if payload is not None and payload["complete"] and not gap:
                    observed = _when(payload["observed_at"])
                    cursor.execute("INSERT INTO v7_alerts (alert_key, severity, status, first_seen, last_seen, last_notified) VALUES (%s,'info',%s,%s,%s,NULL) "
                                   "ON DUPLICATE KEY UPDATE last_seen=VALUES(last_seen)", (MARK, MARKER, observed, observed))
                for key in p["deliver"]:
                    cursor.execute("UPDATE v7_alerts SET status=%s, last_notified=%s WHERE alert_key=%s AND status=%s", (PENDING, now, key, QUEUED))
            connection.commit()
            if p["push"]:
                accepted = send(environ.get("NTFY_TOPIC", ""), *p["push"])
                with connection.cursor() as cursor:
                    for key in p["deliver"]:
                        cursor.execute("UPDATE v7_alerts SET status=%s WHERE alert_key=%s AND status=%s", (SENT if accepted else QUEUED, key, PENDING))
                connection.commit()
    complete = payload is not None and payload["complete"] and not gap
    return {"lookback_hours": hours, "coverage_gap": int(gap), "health": payload["health"] if payload else ("NO_ENDPOINT" if not host else "UNAVAILABLE"),
            "scanned": payload["scanned"] if payload else 0, "complete": int(complete), "new": len(p["new"]), "deliver": len(p["deliver"]),
            "queued_quiet": p["queued_quiet"], "attention": p["attention"], "expired": len(p["expire"]), "pushed": int(accepted), "ambiguous": len(p["ambiguous"]),
            "failed": int(not complete or bool(p["ambiguous"]) or (live and bool(p["push"]) and not accepted))}
