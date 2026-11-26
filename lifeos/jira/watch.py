"""Pages the phone when a scheduled sprint rollover keeps failing.

Told the rollover step's outcome: success records the time and closes any open alert; failure pages once it has not
worked for STUCK_HOURS, then reminds every 6 hours and says when it recovers. Skipped steps and dry runs record
nothing.
"""
import json
import os
from datetime import datetime, timezone

from lifeos.platform import alerts, db

KEY = "jira_rollover_stuck"
STUCK_HOURS = 3
SCHEMA = ("""CREATE TABLE IF NOT EXISTS v7_jira_rollover (
        k VARCHAR(16) NOT NULL PRIMARY KEY, last_ok DATETIME NOT NULL, alert_open TINYINT NOT NULL DEFAULT 0, last_notified DATETIME NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",)


def decide(state, ok, now):
    """state: None or {"last_ok", "alert_open", "last_notified"}; now: naive UTC."""
    state = dict(state or {"last_ok": now, "alert_open": False, "last_notified": None})
    was_open = bool(state["alert_open"])
    known = {KEY: {"severity": alerts.PAGE, "last_notified": state["last_notified"]}} if was_open else {}
    if ok:
        state.update(last_ok=now, alert_open=False, last_notified=None)
        return state, alerts.reconcile([], known, now)
    hours = int((now - state["last_ok"]).total_seconds() // 3600)
    active = [alerts.Alert(KEY, alerts.PAGE, f"Jira sprint rollover has not worked for {hours} hours", "A board kept its old sprint.",
                           "open the domains run (jira job, rollover step) and look for the JIRA_ code")] if hours >= STUCK_HOURS else []
    state["alert_open"] = bool(active)
    return state, alerts.reconcile(active, known, now)


def _load(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT last_ok, alert_open, last_notified FROM v7_jira_rollover WHERE k=%s", ("roll",))
        row = cursor.fetchone()
    return {"last_ok": row[0], "alert_open": bool(row[1]), "last_notified": row[2]} if row else None


def _save(connection, state):
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO v7_jira_rollover (k, last_ok, alert_open, last_notified) VALUES (%s,%s,%s,%s) "
                       "ON DUPLICATE KEY UPDATE last_ok=VALUES(last_ok), alert_open=VALUES(alert_open), last_notified=VALUES(last_notified)",
                       ("roll", state["last_ok"], 1 if state["alert_open"] else 0, state["last_notified"]))


def run(limit, live, environ=os.environ, now=None, send=None, connect=None):
    """JIRA_ROLLOVER_OUTCOME is the rollover step's outcome (success | failure); anything else records nothing."""
    outcome = environ.get("JIRA_ROLLOVER_OUTCOME", "")
    if not live or outcome not in ("success", "failure"):
        return {"recorded": False}
    connect = connect or db.connect
    clock = (now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)).replace(tzinfo=None)
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                for statement in SCHEMA:
                    cursor.execute(statement)
            state, plan = decide(_load(connection), outcome == "success", clock)
            sent = alerts.deliver(plan, environ.get("NTFY_TOPIC", ""), send)
            if plan.notify and sent["failed"] == 0 and state["alert_open"]:
                state["last_notified"] = clock
            _save(connection, state)
        result = {"recorded": True, "alert_open": state["alert_open"], **sent}
    except Exception:  # noqa: BLE001
        result = {"recorded": False, "state": "unavailable"}
    print("jira-rollover-watch:", json.dumps(result))
    return result
