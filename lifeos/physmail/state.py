"""The Physical Mail derivative state (MAIL-1.2): one private snapshot row, rebuilt by replay from the anchor, merged idempotently on every run.

Why a snapshot and not pure replay: the aggregate count is exact only if the window holds every still-pending arrival, and replay cannot prove that (items age out of
the window, notices get filed as spam, vendors dispose of mail without an email). So the row keeps the arrival notices, the per-mail-number events and the bound count;
the overlap re-read changes nothing because every event is keyed by message id.

Binding is by count, not identity (order-independent): the starting pile (Jim's October 4 anchor) and each arrival notice are units; each first-seen mail number
consumes one unit. Pending-unidentified = units - bound. A first-seen mail number with no unit left is tolerated only up to GRACE_DAYS after the anchor and is otherwise DEGRADED."""
import copy
from datetime import datetime, timedelta, timezone

from . import chains

SCHEMA = 1
REPLAY = 2                                                               # the notice parser that built the row: a row from an older parser is rebuilt once from the anchor (LIFE-95)
ANCHOR_DATE = "2026-10-04"
ANCHOR_AT = datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc)            # midnight Oct 4, 2026 in Chicago (CDT)
GRACE_DAYS = 14
AGED_DAYS = 30
KEEP_DAYS = 30                                      # a disposed or delivered piece is counted on the card this long, then it is not mentioned (the stored history stays)
MAX_REVIEW = 100


class StateError(RuntimeError):
    """Fixed codes only."""


def new_state(anchor_count, now):
    if not isinstance(anchor_count, int) or not 0 <= anchor_count <= 999:
        raise StateError("PHYSMAIL_ANCHOR_INVALID")
    return {"schema": SCHEMA, "taken_at": now.isoformat(), "anchor_date": ANCHOR_DATE, "anchor_count": anchor_count, "through": ANCHOR_AT.isoformat(),
            "accepted_at": None, "arrivals": {}, "chains": {}, "review": [], "portal": None, "ignored": 0, "replay": REPLAY}


def _at(value):
    return datetime.fromisoformat(value)


def apply(state, extracted, now):
    """-> (new state, counts). Pure: merges the extracted events keyed by message id; events dated before the anchor are ignored."""
    st, counts = copy.deepcopy(state), {"arrivals_new": 0, "actions_new": 0, "review_new": 0, "review_resolved": 0, "ignored": 0, "before_anchor": 0}
    seen_review = {item["id"] for item in st["review"]}
    for event in extracted:
        kind = event.get("kind")
        if kind == "IGNORED":
            counts["ignored"] += 1
            continue
        if event.get("at") and _at(event["at"]) < ANCHOR_AT:
            counts["before_anchor"] += 1
            continue
        if kind in ("ARRIVAL", "ACTIONS") and event.get("id") in seen_review:        # the same message now parses cleanly: its earlier REVIEW (an older, flatter parse) is resolved, nothing else is touched
            st["review"] = [item for item in st["review"] if item["id"] != event["id"]]
            seen_review.discard(event["id"])
            counts["review_resolved"] += 1
        if kind == "ARRIVAL":
            if event["id"] not in st["arrivals"]:
                st["arrivals"][event["id"]] = {"at": event["at"], "count": event["count"]}
                counts["arrivals_new"] += 1
            if event.get("portal") and (st["portal"] is None or _at(event["at"]) >= _at(st.get("portal_at") or ANCHOR_AT.isoformat())):
                st["portal"], st["portal_at"] = event["portal"], event["at"]
        elif kind == "ACTIONS":
            for number, verb in event["actions"]:
                chain = st["chains"].setdefault(number, {"events": {}})
                key = f"{event['id']}:{verb}"
                if key not in chain["events"]:
                    chain["events"][key] = [event["at"], verb]
                    counts["actions_new"] += 1
        elif kind == "REVIEW" and event.get("id") and event["id"] not in seen_review:
            st["review"] = (st["review"] + [{"id": event["id"], "at": event.get("at"), "reason": event["reason"]}])[-MAX_REVIEW:]
            seen_review.add(event["id"])
            counts["review_new"] += 1
    st["ignored"] += counts["ignored"]
    return st, counts


def _status_of(chain):
    return chains.fold([tuple(v) for v in chain["events"].values()])


def pieces(state):
    """The active pieces, one per vendor mail number (DONE pieces are history, not listed): [{number, status, verb, at, tracking}], needs-a-look first, then forwarded, then scanned (newest number first)."""
    out = []
    for number, chain in state["chains"].items():
        status = _status_of(chain)
        if status == chains.DONE:
            continue
        at, verb = max(chain["events"].values())
        out.append({"number": number, "status": status, "verb": verb, "at": at, "tracking": chain.get("tracking")})
    rank = {chains.REVIEW: 0, chains.WAITING: 1, chains.OPEN: 2}
    return sorted(out, key=lambda p: (rank[p["status"]], -int(p["number"]) if p["number"].isdigit() else 0, p["number"]))


def disposed(state, since=None):
    """{"recycled": n, "shredded": n} for the pieces whose chain is DONE (the verbs of one DONE chain are never both, the fold sends that to REVIEW). With `since`, only pieces whose last notice is that recent."""
    out = {"recycled": 0, "shredded": 0}
    for chain in state["chains"].values():
        if _status_of(chain) == chains.DONE:
            if since is not None and _at(max(v[0] for v in chain["events"].values())) < since:
                continue
            verbs = {v[1] for v in chain["events"].values()}
            out["recycled" if "RECYCLE" in verbs else "shredded"] += 1
    return out


def summary(state, now):
    """Counts and the degraded reason, if any: a pure function of the stored state."""
    chain_status = {number: chains.fold([tuple(v) for v in chain["events"].values()]) for number, chain in state["chains"].items()}
    units = state["anchor_count"] + sum(a["count"] for a in state["arrivals"].values())
    first_seen = sorted((min(v[0] for v in state["chains"][n]["events"].values()), n) for n in state["chains"])
    bound = min(units, len(first_seen))
    excess = first_seen[bound:]
    limit = ANCHOR_AT + timedelta(days=GRACE_DAYS)
    degraded = "UNBOUND_ITEM" if any(_at(t) > limit for t, _ in excess) else None
    waiting = {n: s for n, s in chain_status.items() if s == chains.WAITING}
    aged = sum(1 for n in waiting if _at(max(v[0] for v in state["chains"][n]["events"].values())) < now - timedelta(days=AGED_DAYS))
    return {"unidentified": units - bound, "opened": sum(1 for s in chain_status.values() if s == chains.OPEN), "waiting_tracking": len(waiting),
            "aged": aged, "done": sum(1 for s in chain_status.values() if s == chains.DONE), "identified": len(chain_status),
            "review": sum(1 for s in chain_status.values() if s == chains.REVIEW) + len(state["review"]), "degraded_reason": degraded}
