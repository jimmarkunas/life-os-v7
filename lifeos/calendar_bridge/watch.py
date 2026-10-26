"""A calendar sync that keeps failing must reach the phone (D173). The sync step is a warning by design (a provider hiccup never fails the run), so it refused every run for five days
(CALENDAR_MASS_DELETE_GUARD, Oct 7 to 12) without a word. This wraps `sync.run` for the `calendar-sync` stage: a live success records the time and closes any open alert; a live failure
pages the phone only once the sync has not worked for STUCK_HOURS (one transient provider error is not an incident), then reminds every 6 hours (platform alerts, D53) and says when it
recovers. State is one private row. Counts and fixed codes only; alerting never changes the sync's own result or error."""
import json
import os
from datetime import datetime, timezone

from lifeos.calendar_bridge import sync
from lifeos.platform import alerts, db

KEY = "calendar_sync_stuck"
STUCK_HOURS = 3                    # three hourly runs without a success
SCHEMA = ("""CREATE TABLE IF NOT EXISTS v7_calendar_sync (
        k VARCHAR(16) NOT NULL PRIMARY KEY, last_ok DATETIME NOT NULL, alert_open TINYINT NOT NULL DEFAULT 0, last_notified DATETIME NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",)


def decide(state, ok, now, code=""):
    """state: None or {"last_ok", "alert_open", "last_notified"}; now: naive UTC. -> (new state, alerts.Plan). The first time anything is seen counts as the last good moment, so a sync that was
    already broken when this shipped pages after STUCK_HOURS, not instantly and not never."""
    state = dict(state or {"last_ok": now, "alert_open": False, "last_notified": None})
    was_open = bool(state["alert_open"])
    known = {KEY: {"severity": alerts.PAGE, "last_notified": state["last_notified"]}} if was_open else {}
    if ok:
        state.update(last_ok=now, alert_open=False, last_notified=None)
        return state, alerts.reconcile([], known, now)
    hours = int((now - state["last_ok"]).total_seconds() // 3600)
    active = [alerts.Alert(KEY, alerts.PAGE, f"Calendar sync has not worked for {hours} hours", f"Last error {code}.",
                           "open the domains run (outlook job, Calendar sync step)")] if hours >= STUCK_HOURS else []
    state["alert_open"] = bool(active)
    return state, alerts.reconcile(active, known, now)


def _load(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT last_ok, alert_open, last_notified FROM v7_calendar_sync WHERE k=%s", ("sync",))
        row = cursor.fetchone()
    return {"last_ok": row[0], "alert_open": bool(row[1]), "last_notified": row[2]} if row else None


def _save(connection, state):
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO v7_calendar_sync (k, last_ok, alert_open, last_notified) VALUES (%s,%s,%s,%s) "
                       "ON DUPLICATE KEY UPDATE last_ok=VALUES(last_ok), alert_open=VALUES(alert_open), last_notified=VALUES(last_notified)",
                       ("sync", state["last_ok"], 1 if state["alert_open"] else 0, state["last_notified"]))


def _record(connect, environ, now, ok, code, send):
    """Never raises: a store that cannot be read must not turn a sync result into a different failure."""
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                for statement in SCHEMA:
                    cursor.execute(statement)
            state, plan = decide(_load(connection), ok, now, code)
            sent = alerts.deliver(plan, environ.get("NTFY_TOPIC", ""), send)
            if plan.notify and sent["failed"] == 0 and state["alert_open"]:
                state["last_notified"] = now                    # a push the phone service did not accept is retried next run
            _save(connection, state)
        print("calendar-watch:", json.dumps({"alert_open": state["alert_open"], **sent}))
    except Exception:                                           # noqa: BLE001 - alerting must never change the sync's result
        print("calendar-watch: state_unavailable")


def run(limit, live, environ=os.environ, now=None, send=None, connect=None, **kwargs):
    connect = connect or db.connect
    clock = (now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)).replace(tzinfo=None)
    try:
        result = sync.run(limit, live, environ=environ, connect=connect, now=now, **kwargs)
    except Exception as error:
        if live:
            _record(connect, environ, clock, False, str(error) if isinstance(error, sync.BridgeError) else type(error).__name__, send)
        raise
    if live:
        _record(connect, environ, clock, True, "", send)
    return result
