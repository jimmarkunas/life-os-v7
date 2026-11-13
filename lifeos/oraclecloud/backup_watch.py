"""Pages the phone when the off-site backup on Oracle fails or is missed (LIFE-129), from the `oracle1` reading the Oracle Cloud card already makes.

The backup job on Oracle (lifeos/edge/backup) sends nothing itself: the platform alerts gateway (D53) is the one sender. Input: the backup's last result {"ok", "code", "age_h"} in Oracle's `oracle1` answer (stats.mjs).
Output: pushes `offsite_backup_failed` (INFO: phone only, reminded every 24 h while it stays broken). A reading that cannot be had (Oracle unreachable) is unknown and changes nothing. State: one private row.
Counts and fixed codes only; it never raises, so the card's own result never depends on it. `make` is also the machinery of worker_watch (LIFE-531): a watch says whether a reading is `healthy` (True, False, or None) and what its one Alert is."""
# Detail moved out of the docstring (LIFE-595):
# LIFE-129 (Jim, Oct 28 2026): a failed or missed off-site backup night must reach the phone, quietly.
# `make` holds the rest of what a watch needs: reconcile, push through the gateway, keep the one private row (alert_open, last_notified). A `healthy` of None (a reading that cannot be had) changes nothing.
import json
import os
from datetime import timezone

from lifeos.platform import alerts, db

KEY = "offsite_backup_failed"
STALE_HOURS = 36                   # the same line as `offsite_backup.py check`: a night and a half without a good run
ACTION = "on Oracle: sudo bash /opt/lifeos-offsite/install-offsite.sh verify"


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


def make(label, table, row, key, healthy, alert_for):
    """-> (decide, record) for one watch. `label` prefixes the log line, `table` and `row` name its one private row, `key` is its alert key."""
    schema = (f"CREATE TABLE IF NOT EXISTS {table} (k VARCHAR(16) NOT NULL PRIMARY KEY, alert_open TINYINT NOT NULL DEFAULT 0, last_notified DATETIME NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")

    def decide(state, reading, now):
        """state: None or {"alert_open", "last_notified"}; reading: the card's value; now: naive UTC. -> (new state, alerts.Plan) or None when the reading is unknown."""
        ok = healthy(reading)
        if ok is None:
            return None
        state = dict(state or {"alert_open": False, "last_notified": None})
        known = {key: {"severity": alerts.INFO, "last_notified": state["last_notified"]}} if state["alert_open"] else {}
        active = [] if ok else [alert_for(reading)]
        plan = alerts.reconcile(active, known, now)
        state["alert_open"] = bool(active)
        if not active:
            state["last_notified"] = None
        return state, plan

    def record(reading, environ=os.environ, now=None, send=None, connect=None):
        """Never raises: a store that cannot be read must not turn the card's result into a different failure."""
        if healthy(reading) is None:
            print(f"{label}:", json.dumps({"reading": "unknown"}))
            return
        clock = now.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            with (connect or db.connect)() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(schema)
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT alert_open, last_notified FROM {table} WHERE k=%s", (row,))
                    found = cursor.fetchone()
                state, plan = decide({"alert_open": bool(found[0]), "last_notified": found[1]} if found else None, reading, clock)
                sent = alerts.deliver(plan, environ.get("NTFY_TOPIC", ""), send)
                if plan.notify and sent["failed"] == 0 and state["alert_open"]:
                    state["last_notified"] = clock                  # a push the phone service did not accept is retried next tick
                with connection.cursor() as cursor:
                    cursor.execute(f"INSERT INTO {table} (k, alert_open, last_notified) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE alert_open=VALUES(alert_open), last_notified=VALUES(last_notified)",
                                   (row, 1 if state["alert_open"] else 0, state["last_notified"]))
            print(f"{label}:", json.dumps({"alert_open": state["alert_open"], **sent}))
        except Exception:                                           # noqa: BLE001 - alerting must never change the card's result
            print(f"{label}: state_unavailable")

    return decide, record


decide, record = make("backup-watch", "v7_offsite_backup", "backup", KEY, healthy, alert_for)
