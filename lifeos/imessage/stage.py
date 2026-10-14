"""COMM-1.4b: urgent iMessage alerts in V7's hourly domain run (LIFE-31; Jim's approval of Oct 13, 2026).

hourly tick -> domains.yml `imessage` job (joins the tailnet as an ephemeral tag:v7-runner node) -> Oracle's private alert-METADATA endpoint -> this stage ->
the existing ntfy sink, with the existing private v7_alerts table as the only state. The endpoint reads and classifies the Mac Mini's messages privately on Oracle
and returns only keyed event ids, severity, reason codes and completeness; nothing here ever sees message text, names, numbers or chat ids, and the push says only
how many texts need Jim.

Policy (Communications, not the platform's reminder cadence):
- INFORMATIONAL and ATTENTION never push. ACTION_REQUIRED pushes once per event; during quiet hours (10 PM-8 AM America/Chicago) it waits and goes out at the
  first run after 8 AM. TIME_CRITICAL (an emergency signal from a known contact in a direct conversation, never a `review` verdict) pushes at any hour.
- One push per event version: the row key is the event id plus its severity, so the same message escalating to TIME_CRITICAL is a new version; an unchanged
  event is never pushed again. A push the service did not accept stays queued and is retried next run (up to 24 h), never marked sent.
- Coverage: the lookback runs from the last COMPLETE scan (the `imsg_scan_ok` marker row) plus an hour of overlap, 2-72 h. A gap over 72 h, a scan that hit the
  reader's limit, an unreachable, stale or off-contract endpoint are all reported as a failure (counts `failed`), never passed as complete.
- Delivery is claim-then-send, so a database failure can never cause a duplicate push: events are recorded first; the ones about to go out are marked IMSG_P
  (pending) and committed BEFORE the push; the receipt then sets IMSG_S (accepted) or back to IMSG_Q (rejected: retried next run). A row still IMSG_P at the next
  run means the outcome is unknown (the receipt was lost): it is closed as sent WITHOUT a second push and reported as `ambiguous` and `failed`.
Rows use their own statuses (IMSG_Q queued, IMSG_P pending, IMSG_S sent, IMSG_X expired, IMSG_M marker), never 'OPEN', which the Jobs alert stage owns."""
import datetime as dt
import json
import math
import os
import re
import socket
from zoneinfo import ZoneInfo

from lifeos.platform import alerts, db

PORT = 47341
SCHEMA = "lifeos.imessage.alerts.v1"
TZ = ZoneInfo("America/Chicago")
QUIET_FROM, QUIET_UNTIL = 22, 8
PREFIX, MARK = "imsg:", "imsg_scan_ok"
QUEUED, PENDING, SENT, EXPIRED, MARKER = "IMSG_Q", "IMSG_P", "IMSG_S", "IMSG_X", "IMSG_M"
FIRST, MIN_LOOKBACK, MAX_LOOKBACK = 2, 2, 72
DELIVER_FOR, KEEP = dt.timedelta(hours=24), dt.timedelta(days=14)
CODE = {"TIME_CRITICAL": "tc", "ACTION_REQUIRED": "ar"}
SEVERITIES = ("ATTENTION", "ACTION_REQUIRED", "TIME_CRITICAL")
REASONS = {"emergency_language", "unknown_sender", "group_conversation", "urgent_same_day_deadline", "interview_scheduling", "deadline_request", "urgent_language",
           "direct_request", "mentions_jim", "no_signal", "no_text", "automated_or_code"}
MAX_REPLY = 32768


def window(now, mark):
    """(lookback hours, coverage gap): from the last complete scan with an hour of overlap; the first run looks back FIRST hours only (no backlog flood)."""
    if mark is None:
        return FIRST, False
    hours = math.ceil((now - mark).total_seconds() / 3600) + 1
    return min(max(hours, MIN_LOOKBACK), MAX_LOOKBACK), hours > MAX_LOOKBACK


def fetch(host, hours, port=PORT):
    """One request over the tailnet. No half-close: Tailscale Serve drops the whole connection on the client's FIN. Empty bytes = refused or unreachable."""
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
    """The endpoint's one JSON line -> payload, or None. Every field is checked; a fresh observation time is required (stale or replayed answers fail)."""
    if not raw or len(raw) > MAX_REPLY or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        return None
    try:
        p = json.loads(raw)
        ok = (isinstance(p, dict) and set(p) == {"schema", "observed_at", "lookback_hours", "limit", "sync", "events", "health", "complete", "scanned", "limit_reached"}
              and p["schema"] == SCHEMA and p["lookback_hours"] == hours and p["sync"] == "UNVERIFIED" and isinstance(p["complete"], bool)
              and isinstance(p["limit_reached"], bool) and p["health"] in ("CURRENT", "DEGRADED", "DISCONNECTED", "STALE")
              and isinstance(p["scanned"], int) and 0 <= p["scanned"] <= p["limit"] == 50 and isinstance(p["events"], list) and len(p["events"]) <= 50
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
    """Pure: payload (or None when the scan failed) + stored imsg rows -> what to write and push. rows: {key: {"status", "severity", "first_seen", "last_seen"}}."""
    events = [] if payload is None else payload["events"]
    found = {}
    for e in events:
        if e["severity"] == "TIME_CRITICAL" and e["review"]:
            continue                                                # a TIME_CRITICAL verdict is never a review one; refuse rather than wake Jim on doubt
        if e["severity"] in CODE:
            found[PREFIX + e["key"][:24] + ":" + CODE[e["severity"]]] = CODE[e["severity"]]
    new = {k: c for k, c in found.items() if k not in rows}
    seen = [k for k in found if k in rows]
    queued = {k: r["severity"] for k, r in rows.items() if r["status"] == QUEUED and now - r["first_seen"] <= DELIVER_FOR}
    queued.update(new)
    deliver = sorted(k for k, c in queued.items() if c == "tc" or not quiet(now))
    expire = sorted(k for k, r in rows.items() if r["status"] == QUEUED and now - r["first_seen"] > DELIVER_FOR)
    purge = sorted(k for k, r in rows.items() if r["status"] in (SENT, EXPIRED) and now - r["last_seen"] > KEEP)
    tc = sum(1 for k in deliver if queued[k] == "tc")
    push = None
    if deliver:
        n = len(deliver)
        push = (("Urgent text message" if tc else "Text needs a reply"),
                ("%d text%s may need you now" % (tc, "" if tc == 1 else "s") if tc else "%d text%s need%s a reply" % (n, "" if n == 1 else "s", "s" if n == 1 else ""))
                + ". Open Messages.", alerts.PAGE if tc else alerts.INFO)
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


def run(limit, live, environ=os.environ, now=None, fetcher=fetch, connect=db.connect, send=alerts.ntfy):
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
            with connection.cursor() as cursor:                     # 1. record everything this scan learned, before any push
                for key, code in p["new"].items():
                    cursor.execute("INSERT INTO v7_alerts (alert_key, severity, status, first_seen, last_seen, last_notified) VALUES (%s,%s,%s,%s,%s,NULL)",
                                   (key, code, QUEUED, now, now))
                for key in p["seen"]:
                    cursor.execute("UPDATE v7_alerts SET last_seen=%s WHERE alert_key=%s", (now, key))
                for key in p["ambiguous"]:                          # a lost receipt from an earlier run: never pushed twice
                    cursor.execute("UPDATE v7_alerts SET status=%s WHERE alert_key=%s AND status=%s", (SENT, key, PENDING))
                for key in p["expire"]:
                    cursor.execute("UPDATE v7_alerts SET status=%s WHERE alert_key=%s", (EXPIRED, key))
                for key in p["purge"]:
                    cursor.execute("DELETE FROM v7_alerts WHERE alert_key=%s AND status IN (%s, %s)", (key, SENT, EXPIRED))
                if payload is not None and payload["complete"] and not gap:
                    observed = _when(payload["observed_at"])
                    cursor.execute("INSERT INTO v7_alerts (alert_key, severity, status, first_seen, last_seen, last_notified) VALUES (%s,'info',%s,%s,%s,NULL) "
                                   "ON DUPLICATE KEY UPDATE last_seen=VALUES(last_seen)", (MARK, MARKER, observed, observed))
                for key in p["deliver"]:                            # 2. claim what is about to go out
                    cursor.execute("UPDATE v7_alerts SET status=%s, last_notified=%s WHERE alert_key=%s AND status=%s", (PENDING, now, key, QUEUED))
            connection.commit()
            if p["push"]:                                           # 3. push, then 4. the receipt
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
