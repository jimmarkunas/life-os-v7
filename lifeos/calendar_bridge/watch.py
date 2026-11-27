"""Wraps `sync.run` so a persistently failing sync reaches the phone.

A live failure pages only after STUCK_HOURS without success, then reminds every 6 hours and reports recovery. State is
one private row; alerting never changes the sync's result or error."""

import json
import os
from datetime import datetime, timezone

from lifeos.calendar_bridge import sync
from lifeos.platform import alerts, db

KEY = "calendar_sync_stuck"
STUCK_HOURS = 3
SCHEMA = """CREATE TABLE IF NOT EXISTS v7_calendar_sync (
        k VARCHAR(16) NOT NULL PRIMARY KEY, last_ok DATETIME NOT NULL, alert_open TINYINT NOT NULL DEFAULT 0, last_notified DATETIME NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def decide(state, ok, now, code=""):
    """Returns (new state, alerts.Plan) for the stored state (or None) and a naive-UTC `now`.
    The first observation counts as the last good moment."""
    state = dict(state or {"last_ok": now, "alert_open": False, "last_notified": None})
    was_open = bool(state["alert_open"])
    known = {KEY: {"severity": alerts.PAGE, "last_notified": state["last_notified"]}} if was_open else {}
    if ok:
        state.update(last_ok=now, alert_open=False, last_notified=None)
        return state, alerts.reconcile([], known, now)
    hours = int((now - state["last_ok"]).total_seconds() // 3600)
    active = (
        [
            alerts.Alert(
                KEY,
                alerts.PAGE,
                f"Calendar sync has not worked for {hours} hours",
                f"Last error {code}.",
                "open the domains run (outlook job, Calendar sync step)",
            )
        ]
        if hours >= STUCK_HOURS
        else []
    )
    state["alert_open"] = bool(active)
    return state, alerts.reconcile(active, known, now)


def _load(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT last_ok, alert_open, last_notified FROM v7_calendar_sync WHERE k=%s", ("sync",))
        row = cursor.fetchone()
    return {"last_ok": row[0], "alert_open": bool(row[1]), "last_notified": row[2]} if row else None


def _save(connection, state):
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO v7_calendar_sync (k, last_ok, alert_open, last_notified) VALUES (%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE last_ok=VALUES(last_ok), alert_open=VALUES(alert_open), last_notified=VALUES(last_notified)",
            ("sync", state["last_ok"], 1 if state["alert_open"] else 0, state["last_notified"]),
        )


def _record(connect, environ, now, ok, code, send):
    """Never raises: a store that cannot be read must not turn a sync result into a different failure."""
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
            state, plan = decide(_load(connection), ok, now, code)
            sent = alerts.deliver(plan, environ.get("NTFY_TOPIC", ""), send)
            if plan.notify and sent["failed"] == 0 and state["alert_open"]:
                state["last_notified"] = now  # an unaccepted push is retried next run
            _save(connection, state)
        print("calendar-watch:", json.dumps({"alert_open": state["alert_open"], **sent}))
    except Exception:  # noqa: BLE001 - alerting must never change the sync's result
        print("calendar-watch: state_unavailable")


def run(limit, live, environ=os.environ, now=None, send=None, connect=None, **kwargs):
    connect = connect or db.connect
    clock = (now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)).replace(tzinfo=None)
    try:
        result = sync.run(limit, live, environ=environ, connect=connect, now=now, **kwargs)
    except Exception as error:
        if live:
            _record(
                connect,
                environ,
                clock,
                False,
                str(error) if isinstance(error, sync.BridgeError) else type(error).__name__,
                send,
            )
        raise
    if live:
        _record(connect, environ, clock, True, "", send)
    return result
