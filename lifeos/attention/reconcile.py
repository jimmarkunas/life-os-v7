"""Attention reconciler (D127): week math, natural key and the plan of writes. Pure: no clock, no network.

Natural key: Week Ending + Category + normalized Item. `Done` belongs to Jim: no plan ever sets it, and a Done row is never reactivated or recreated.
A row is created only when its absence in the (completely read) current week is proved. More than one row with a key is ambiguous: nothing is written for it."""
from datetime import date, timedelta

from lifeos.attention.policy import normalize


def week_ending(today):
    """The Sunday that ends the canonical week containing `today` (a Sunday is its own week ending)."""
    return today + timedelta(days=(6 - today.weekday()) % 7)


def key(week, category, item):
    return (str(week), category or "", normalize(item))


def plan(candidates, rows, today, proofs):
    """candidates: [{category, item, url, medium}] admitted from sources that were read completely.
    rows: [{id, item, category, done, active, medium, url, week}] of the current and the immediately prior week, read completely.
    proofs: {medium: "present" | "absent" | "unknown"} for sources V7 could check (label still on the message, or proved off it).
    -> {"create": [props], "deactivate": [row id], "reactivate": [row id], "ambiguous": n, "carried": n, "reused": n, "skipped_done": n}"""
    this_week, prior = week_ending(today), week_ending(today) - timedelta(days=7)
    out = {"create": [], "deactivate": [], "reactivate": [], "ambiguous": 0, "carried": 0, "reused": 0, "skipped_done": 0}
    by_key = {}
    for row in rows:
        by_key.setdefault(key(row["week"], row["category"], row["item"]), []).append(row)
    planned = set()

    def want(category, item, url, medium, carried=False):
        k = key(this_week, category, item)
        if k in planned:
            return
        existing = by_key.get(k, [])
        if len(existing) > 1:
            out["ambiguous"] += 1
            planned.add(k)
            return
        planned.add(k)
        if existing:
            row = existing[0]
            if row["done"]:
                out["skipped_done"] += 1
            elif not row["active"] and not carried and proofs.get(row["medium"]) == "present":
                out["reactivate"].append(row["id"])
            else:
                out["reused"] += 1
            return
        out["create"].append({"week": str(this_week), "category": category, "item": item, "url": url, "medium": medium})
        out["carried"] += carried

    for c in candidates:
        want(c["category"], c["item"], c["url"], c["medium"])
    for row in rows:                                                  # carry-forward: immediately prior week, still active and not done
        if str(row["week"]) == str(prior) and row["active"] and not row["done"] and row["category"] != "Physical Mail":
            want(row["category"], row["item"], row["url"], row["medium"], carried=True)
    for row in rows:                                                  # deactivate only on proof that the source condition is gone (label removed by Jim)
        if str(row["week"]) == str(this_week) and row["active"] and not row["done"] and proofs.get(row["medium"]) == "absent":
            out["deactivate"].append(row["id"])
    return out
