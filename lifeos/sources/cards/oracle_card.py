"""The Oracle Cloud card stage with its Codex lines taken from the AI Capacity card's own reading (Jim, Oct 24 2026: the two cards must show the same numbers).

lifeos.jobs.fit.edge.limits is the one read of Oracle's Codex windows that AI Capacity shows; this module asks it too and hands the card `left = 100 - used` for the 5h and
Week windows. It lives in sources because only sources may import both the jobs layer (the reader) and the oraclecloud layer (the card). A reading that cannot be had is
None: the card shows Codex unavailable and says DEGRADED, the same way AI Capacity does. Counts and fixed codes only in the log."""
import json
import os

from lifeos.jobs.fit import edge
from lifeos.oraclecloud import card

WINDOWS = (("5h", "left_5h"), ("Week", "left_week"))                     # the labels capacity_card shows as 5-hour and weekly


def plan_from(reading):
    """edge.limits() -> {"left_5h", "left_week"} (percent left), or None when Oracle reports no Codex pool or either window."""
    pool = next((p for p in reading["pools"] if p["provider"] == "openai"), None)
    used = {w["label"]: w["used"] for w in (pool or {}).get("windows", [])}
    if not all(label in used for label, _ in WINDOWS):
        return None
    return {key: max(0, min(100, 100 - used[label])) for label, key in WINDOWS}


def run(limit, live, environ=os.environ, **kw):
    host = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()

    def plan():
        try:
            return plan_from(edge.limits(host))
        except edge.EdgeError as error:
            print("oracle-card: codex limits unavailable", json.dumps({"code": str(error)}))
            return None
    return card.run(limit, live, environ, plan=plan, **kw)
