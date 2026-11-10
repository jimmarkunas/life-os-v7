"""LIFE-531: duplicate Codex workers piling up on Oracle must reach the phone, quietly. OpenClaw starts one Codex app-server worker per agent; upstream bug 196499 leaks extra ones
that never exit. Every tick the Oracle Cloud card already reads Oracle's `oracle1` answer, which carries {"count", "agents"} (from stats.mjs: the gateway's app-server children, and the
agent folders). More workers than agents is the leak. This pushes `openclaw_workers_leaking` (INFO: phone only, reminded every 24 h while it stays true) through the platform alerts
gateway (D53). A reading that cannot be had is unknown and changes nothing. State is one private row. Counts and fixed codes only; it never raises, so the card's result never depends on it."""
import json
import os
from datetime import timezone

from lifeos.platform import alerts, db

KEY = "openclaw_workers_leaking"
CODE = "OPENCLAW_WORKERS_LEAKING"
ACTION = "on Oracle: restart the OpenClaw gateway when no agent run is in progress (LIFE-531), then check the worker count"
SCHEMA = ("""CREATE TABLE IF NOT EXISTS v7_openclaw_workers (
        k VARCHAR(16) NOT NULL PRIMARY KEY, alert_open TINYINT NOT NULL DEFAULT 0, last_notified DATETIME NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",)


def healthy(reading):
    """-> True | False | None (unknown). Broken: more workers than agents."""
    if reading is None:
        return None
    return reading["count"] <= reading["agents"]


def alert_for(reading):
    return alerts.Alert(KEY, alerts.INFO, f"OpenClaw has {reading['count']} Codex workers for {reading['agents']} agents",
                        f"{reading['count'] - reading['agents']} extra worker(s) are running ({CODE}).", ACTION)


def decide(state, reading, now):
    """state: None or {"alert_open", "last_notified"}; reading: the card's `workers` value; now: naive UTC. -> (new state, alerts.Plan) or None when the reading is unknown."""
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
        cursor.execute("SELECT alert_open, last_notified FROM v7_openclaw_workers WHERE k=%s", ("workers",))
        row = cursor.fetchone()
    return {"alert_open": bool(row[0]), "last_notified": row[1]} if row else None


def _save(connection, state):
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO v7_openclaw_workers (k, alert_open, last_notified) VALUES (%s,%s,%s) "
                       "ON DUPLICATE KEY UPDATE alert_open=VALUES(alert_open), last_notified=VALUES(last_notified)",
                       ("workers", 1 if state["alert_open"] else 0, state["last_notified"]))


def record(reading, environ=os.environ, now=None, send=None, connect=None):
    """Never raises: a store that cannot be read must not turn the card's result into a different failure."""
    if healthy(reading) is None:
        print("worker-watch:", json.dumps({"reading": "unknown"}))
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
        print("worker-watch:", json.dumps({"alert_open": state["alert_open"], **sent}))
    except Exception:                                           # noqa: BLE001 - alerting must never change the card's result
        print("worker-watch: state_unavailable")
