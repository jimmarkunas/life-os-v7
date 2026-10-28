"""LIFE-129 (Jim, Oct 28 2026): a failed or missed off-site backup night must reach the phone, quietly. The backup job on Oracle (lifeos/edge/backup) sends nothing itself: the
platform alerts gateway is the one sender. Every tick the Oracle Cloud card stage already reads Oracle's `oracle1` answer, which carries the backup's last result
({"ok", "code", "age_h"}, from stats.mjs); this decides from that reading and pushes `offsite_backup_failed` (INFO: phone only, reminded every 24 h while it stays broken,
platform alerts D53). A reading that cannot be had (Oracle unreachable) is unknown and changes nothing: it neither opens nor resolves the alert. State is one private row.
Counts and fixed codes only; it never raises, so the card's own result never depends on it."""
import json
import os
from datetime import timezone

from lifeos.platform import alerts, db

KEY = "offsite_backup_failed"
STALE_HOURS = 36                   # the same line as `offsite_backup.py check`: a night and a half without a good run
ACTION = "on Oracle: sudo bash /opt/lifeos-offsite/install-offsite.sh verify"
SCHEMA = ("""CREATE TABLE IF NOT EXISTS v7_offsite_backup (
        k VARCHAR(16) NOT NULL PRIMARY KEY, alert_open TINYINT NOT NULL DEFAULT 0, last_notified DATETIME NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",)


def healthy(reading):
    """-> True | False | None (unknown). Broken: the last run failed, or the last good run is STALE_HOURS old or more."""
    if reading is None:
        return None
    return bool(reading["ok"]) and reading["age_h"] is not None and reading["age_h"] < STALE_HOURS


def alert_for(reading):
    """A broken reading -> the one Alert, its title saying exactly what failed (Jim: a specific label, never a generic DEGRADED)."""
    if reading["code"] == "backup_never_ran":
        title = "Off-site backup has never run"
    elif not reading["ok"]:
        title = f"Off-site backup failed ({reading['code']})"
    else:
        title = f"Off-site backup missed: last good run {reading['age_h']} hours ago"
    detail = "" if reading["age_h"] is None else f"Last run {reading['age_h']} hours ago."
    return alerts.Alert(KEY, alerts.INFO, title, detail, ACTION)


def decide(state, reading, now):
    """state: None or {"alert_open", "last_notified"}; reading: the card's `backup` value; now: naive UTC. -> (new state, alerts.Plan) or None when the reading is unknown."""
    ok = healthy(reading)
    if ok is None:
        return None
    state = dict(state or {"alert_open": False, "last_notified": None})
    known = {KEY: {"severity": alerts.INFO, "last_notified": state["last_notified"]}} if state["alert_open"] else {}
    active = [] if ok else [alert_for(reading)]
    plan = alerts.reconcile(active, known, now)
    state["alert_open"] = bool(active)
    if not active:
        state["last_notified"] = None
    return state, plan


def _load(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT alert_open, last_notified FROM v7_offsite_backup WHERE k=%s", ("backup",))
        row = cursor.fetchone()
    return {"alert_open": bool(row[0]), "last_notified": row[1]} if row else None


def _save(connection, state):
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO v7_offsite_backup (k, alert_open, last_notified) VALUES (%s,%s,%s) "
                       "ON DUPLICATE KEY UPDATE alert_open=VALUES(alert_open), last_notified=VALUES(last_notified)",
                       ("backup", 1 if state["alert_open"] else 0, state["last_notified"]))


def record(reading, environ=os.environ, now=None, send=None, connect=None):
    """Never raises: a store that cannot be read must not turn the card's result into a different failure."""
    if healthy(reading) is None:
        print("backup-watch:", json.dumps({"reading": "unknown"}))
        return
    clock = now.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        with (connect or db.connect)() as connection:
            with connection.cursor() as cursor:
                for statement in SCHEMA:
                    cursor.execute(statement)
            state, plan = decide(_load(connection), reading, clock)
            sent = alerts.deliver(plan, environ.get("NTFY_TOPIC", ""), send)
            if plan.notify and sent["failed"] == 0 and state["alert_open"]:
                state["last_notified"] = clock                  # a push the phone service did not accept is retried next tick
            _save(connection, state)
        print("backup-watch:", json.dumps({"alert_open": state["alert_open"], **sent}))
    except Exception:                                           # noqa: BLE001 - alerting must never change the card's result
        print("backup-watch: state_unavailable")
