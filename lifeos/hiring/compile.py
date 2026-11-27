"""The deterministic Hiring Pipeline compiler. Pure: inputs in, rows out; no clock reads, no I/O, no model.

Inputs (already read and checked complete by the source adapter):
  applied  [{"company", "role", "applied_on": iso date or None, "id"?, "decision"?}]  Job Ledger Applied rows; a
           confirmed Rejected or Passed decision closes the pursuit
  parents  [{"id", "company", "role", "rounds", "retired"?}]  opportunity pages; a retired one closes the pursuit
  events   [{"id", "title", "start": iso with offset}]  accepted Calendar events in the window
  prior    the stored snapshot (or None)
  ok       {"ledger": bool, "parents": bool, "calendar": bool}  which sources were read completely this run

Output: {"rows": [...] | None, "reasons": [fixed codes], "unsupported": n, "unmatched_events": n}. rows None means
UNAVAILABLE; any reason means DEGRADED. A row stores structure, never display text, so a past event can never keep an
"upcoming" label."""
from datetime import datetime

from lifeos.platform import names
from . import models as M

def event_kind(title):
    words = " " + names.norm(title) + " "
    if " assessment " in words or " take-home " in words or " coding test " in words:
        return "assessment"
    if " hiring manager " in words or " hm interview " in words:
        return "hiring_manager"
    if " recruiter " in words and (" screen " in words or " call " in words or " chat " in words or " intro " in words):
        return "recruiter_screen"
    if any(f" {w} " in words for w in ("interview", "onsite", "on-site", "panel", "loop")):
        return "interview"
    return None


def match_event(title, opps):
    """-> (index of the opportunity the title names, None) | (None, "ambiguous") | (None, "none"). Never a guess."""
    words = names.tokens(title)
    hits = [i for i, o in enumerate(opps) if M.contains(words, names.tokens(o["company"]))]
    if len(hits) > 1:
        hits = [i for i in hits if opps[i]["role"] and set(names.tokens(opps[i]["role"])) <= set(words)]
        if len(hits) != 1:
            return None, "ambiguous"
    return (hits[0], None) if hits else (None, "none")


def _stage(kind, start, now):
    past = datetime.fromisoformat(start) <= now
    if kind == "assessment":
        return M.ASSESSMENT
    if kind == "recruiter_screen":
        return M.RECRUITER_SCREEN
    if kind == "hiring_manager":
        return M.INTERVIEWING if past else M.HM_SCHEDULED
    return M.INTERVIEWING


def _pick(events, now):
    future = sorted((e for e in events if datetime.fromisoformat(e["start"]) > now), key=lambda e: e["start"])
    past = sorted((e for e in events if datetime.fromisoformat(e["start"]) <= now), key=lambda e: e["start"])
    return future[0] if future else past[-1] if past else None


def _opportunities(applied, parents, reasons, out):
    opps = []
    for row in applied:
        for opp in opps:
            if M.same(opp["company"], opp["role"], row["company"], row["role"]):
                opp["applied_on"] = max(filter(None, (opp["applied_on"], row["applied_on"])), default=None)
                opp["ledger_id"], opp["dup"], opp["hand"] = None, True, ()
                opp["decision"] = opp["decision"] if opp["decision"] in M.CLOSED_DECISIONS else row.get("decision")
                break
        else:
            opps.append({"company": row["company"], "role": row["role"], "applied_on": row["applied_on"], "ledger": True, "ledger_id": row.get("id"), "decision": row.get("decision"), "parent": None, "parent_state": "none", "rounds": 0, "events": [],
                         "hand": tuple(row.get("hand") or ())})
    for opp in opps:
        if opp.get("dup") and opp["decision"] in M.CLOSED_DECISIONS:
            opp["decision"] = None
            reasons.add("DECISION_AMBIGUOUS")
    for parent in parents:
        near = [o for o in opps if M.same(o["company"], o["role"], parent["company"], parent["role"])]
        if not near:
            if parent["company"] and parent["role"]:
                opps.append({"company": parent["company"], "role": parent["role"], "applied_on": None, "ledger": False, "parent": parent["id"], "parent_state": "one", "rounds": parent["rounds"], "events": [], "lead": parent.get("lead", False), "replied": parent.get("replied", ""),
                             "hand": (), "page_holds": tuple(parent.get("holds") or ())})
            else:
                out["untitled_pages"] += 1
        elif len(near) > 1 or near[0]["parent_state"] != "none":
            for o in near:
                o["parent"], o["parent_state"], o["page_holds"] = None, "ambiguous", ()
            reasons.add("IDENTITY_AMBIGUOUS")
        else:
            near[0]["parent"], near[0]["parent_state"], near[0]["rounds"], near[0]["lead"] = parent["id"], "one", parent["rounds"], parent.get("lead", False)
            near[0]["page_holds"] = tuple(parent.get("holds") or ())
    return opps


def _key(company, role):
    return "|".join(names.tokens(company)) + "|" + "|".join(sorted(names.tokens(role)))


def _hold(row, opp, hit, before):
    """A hand-set hold is row["hold"] until mail or a Calendar invite for the opportunity changes after it was set.

    Then it is cleared and row["hold_cleared"] says by what and when; the prior state keeps it cleared."""
    hold = M.hold_of(opp.get("hand", ()) + opp.get("page_holds", ()))
    if not hold:
        return
    set_at = M.moment(hold["at"])
    cleared = before.get("hold_cleared") if (before.get("hold_cleared") or {}).get("op") == hold["op"] else None
    newer = sorted((M.moment(when), by) for by, when in [("Calendar", e.get("updated")) for e in opp["events"]] + [("mail", (hit or {}).get("received")), ("mail", row.get("contact"))]
                   if when and M.moment(when) > set_at)
    if not cleared and newer:
        cleared = {"op": hold["op"], "state": hold["state"], "by": newer[0][1], "on": newer[0][0].isoformat()}
    if cleared:
        row["hold_cleared"] = cleared
    else:
        row["hold"] = {"state": hold["state"], "at": hold["at"]}


def compile_rows(applied, parents, events, prior, ok, now, outcomes=None, contacts=None):
    reasons, out = set(), {"unsupported": 0, "unmatched_events": 0, "untitled_pages": 0, "closed": 0}
    if not ok["ledger"] or not ok["parents"]:
        reasons.add("LEDGER_UNAVAILABLE" if not ok["ledger"] else "OPPORTUNITIES_UNAVAILABLE")
        rows = [dict(r, carried=True) for r in prior["rows"]] if prior and prior.get("rows") else None
        return {**out, "rows": rows, "reasons": sorted(reasons)}
    prior_rows = {r["key"]: r for r in (prior or {}).get("rows", [])}
    opps = _opportunities(applied, [p for p in parents if not p.get("retired")], reasons, out)
    for page in (p for p in parents if p.get("retired")):         # a Retired page closes the pursuit unless an Active page for the same role says it is live again
        for opp in opps:
            if opp["parent_state"] == "none" and M.same(opp["company"], opp["role"], page["company"], page["role"]):
                opp["closed"] = True
    if ok["calendar"]:
        for event in events:
            kind = event_kind(event["title"])
            if kind is None:
                continue
            at, why = match_event(event["title"], opps)
            if at is not None:
                opps[at]["events"].append({"kind": kind, "start": event["start"], **({"updated": event["updated"]} if event.get("updated") else {})})
            elif why == "ambiguous":
                reasons.add("CALENDAR_AMBIGUOUS")
            else:
                out["unmatched_events"] += 1
    else:
        reasons.add("CALENDAR_UNAVAILABLE")
    rows = []
    for opp in opps:
        if opp.get("closed") or opp.get("decision") in M.CLOSED_DECISIONS:
            out["closed"] += 1
            continue
        key = _key(opp["company"], opp["role"])
        before = prior_rows.get(key) or {}
        row = {"key": key, "company": opp["company"], "role": opp["role"], "applied_on": opp["applied_on"], "parent": opp["parent"], "parent_state": opp["parent_state"],
               "ledger_id": opp.get("ledger_id"), "stage": None, "event_kind": None, "event_start": None, "rounds": opp["rounds"], "carried": False}
        event = _pick(opp["events"], now)
        if event:
            row.update(stage=_stage(event["kind"], event["start"], now), event_kind=event["kind"], event_start=event["start"])
        elif before.get("event_start"):
            # No current event: the prior event stays as history. A vanished future one (or unreadable Calendar)
            # is kept as "last accepted" and the result is DEGRADED. The stage never rolls back.
            future = datetime.fromisoformat(before["event_start"]) > now
            row.update(stage=_stage(before["event_kind"], before["event_start"], now), event_kind=before["event_kind"], event_start=before["event_start"], carried=future or not ok["calendar"])
            if future and ok["calendar"]:
                reasons.add("EVENT_NOT_ON_CALENDAR")
        elif opp["rounds"]:
            row["stage"] = M.INTERVIEWING
        elif opp["ledger"]:
            row["stage"] = M.SUBMITTED
        elif opp.get("lead") and opp["parent_state"] == "one":
            row["stage"] = M.LEAD
            if opp.get("replied"):
                row["replied"] = opp["replied"]
        hit = (outcomes or {}).get(key)
        if hit and row["stage"] is not None:                      # outcome mail newer than the row's last evidence: a line to confirm, never a stage change
            last = row["event_start"] or row["applied_on"]
            if last is None or datetime.fromisoformat(hit["received"]) > M.moment(last):
                row["outcome"] = {"kind": hit["kind"], "received": hit["received"], "match": hit.get("match", "company"), "exact": hit.get("exact") is True}
        if (contacts or {}).get(key):
            row["contact"] = contacts[key]
        _hold(row, opp, hit, before)
        if row["stage"] is None:
            out["unsupported"] += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: (-M.RANK.get(r["stage"], 0), r["event_start"] or "9", r["company"].lower(), r["role"].lower()))
    if not rows and reasons:
        rows = [dict(r, carried=True) for r in prior["rows"]] if prior and prior.get("rows") else None
    return {**out, "rows": rows, "reasons": sorted(reasons)}
