"""Pages the phone when duplicate Codex workers pile up on Oracle: more app-server workers than agents is the leak.

Input: {"count", "agents"} in Oracle's `oracle1` answer. Output: pushes `openclaw_workers_leaking` (INFO: phone
only, reminded every 24 h while true). An unobtainable reading changes nothing. Never raises."""
from lifeos.oraclecloud.backup_watch import make
from lifeos.platform import alerts

KEY = "openclaw_workers_leaking"
CODE = "OPENCLAW_WORKERS_LEAKING"
ACTION = "on Oracle: restart the OpenClaw gateway when no agent run is in progress (LIFE-531), then check the worker count"


def healthy(reading):
    """-> True | False | None (unknown). Broken: more workers than agents."""
    if reading is None:
        return None
    return reading["count"] <= reading["agents"]


def alert_for(reading):
    return alerts.Alert(KEY, alerts.INFO, f"OpenClaw has {reading['count']} Codex workers for {reading['agents']} agents",
                        f"{reading['count'] - reading['agents']} extra worker(s) are running ({CODE}).", ACTION)


decide, record = make("worker-watch", "v7_openclaw_workers", "workers", KEY, healthy, alert_for)
