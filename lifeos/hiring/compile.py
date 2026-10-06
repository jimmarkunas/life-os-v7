"""The deterministic Hiring Pipeline compiler. Pure: inputs in, rows out; no clock reads, no I/O, no model.

Inputs (all already read and checked complete by the source adapter):
  applied  [{"company", "role", "applied_on": iso date or None}]          canonical Job Ledger Applied rows
  parents  [{"id", "company", "role", "rounds"}]                          ACTIVE Hiring Pipeline opportunity pages (read only)
  events   [{"id", "title", "start": iso with offset}]                    accepted Calendar events in the window
  prior    the stored snapshot (or None)
  ok       {"ledger": bool, "parents": bool, "calendar": bool}            which sources were read completely this run

Output: {"rows": [...] | None, "reasons": [fixed codes], "unsupported": n, "unmatched_events": n}. rows None means UNAVAILABLE (nothing safe to show); any reason means the presentation is DEGRADED.
A row stores structure (stage, event kind and start, parent state), never display text, so a past event can never keep an "upcoming" label."""
from datetime import date, datetime

from lifeos.platform import names
from . import models as M

def _tokens(text):
    return names.tokens(text)


def _contains(haystack, needle):
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def event_kind(title):
    """The supported evidence kind a title states, else None. Unsupported titles establish nothing."""
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
    """-> (index of the one opportunity the title names, None) | (None, "ambiguous") | (None, "none"). Company tokens must appear in order in the title; if
    several opportunities share the company, the role tokens must narrow it to exactly one. Never a guess."""
    words = _tokens(title)
    hits = [i for i, o in enumerate(opps) if _contains(words, _tokens(o["company"]))]
    if len(hits) > 1:
        hits = [i for i in hits if opps[i]["role"] and set(_tokens(opps[i]["role"])) <= set(words)]
        if len(hits) != 1:
            return None, "ambiguous"
    return (hits[0], None) if hits else (None, "none")


def _stage(kind, start, now):
    """The stage a calendar event establishes. A past event never keeps a scheduled label."""
    past = datetime.fromisoformat(start) <= now
    if kind == "assessment":
        return M.ASSESSMENT
    if kind == "recruiter_screen":
        return M.RECRUITER_SCREEN
    if kind == "hiring_manager":
        return M.INTERVIEWING if past else M.HM_SCHEDULED
    return M.INTERVIEWING


def _pick(events, now):
    """The nearest upcoming event, else the latest past one."""
    future = sorted((e for e in events if datetime.fromisoformat(e["start"]) > now), key=lambda e: e["start"])
    past = sorted((e for e in events if datetime.fromisoformat(e["start"]) <= now), key=lambda e: e["start"])
    return future[0] if future else past[-1] if past else None


def _opportunities(applied, parents, reasons, out):
    opps = []
    for row in applied:                                           # one opportunity per company+role, whatever the number of ledger rows
        for opp in opps:
            if M.same(opp["company"], opp["role"], row["company"], row["role"]):
                opp["applied_on"] = max(filter(None, (opp["applied_on"], row["applied_on"])), default=None)
                break
        else:
            opps.append({"company": row["company"], "role": row["role"], "applied_on": row["applied_on"], "ledger": True, "parent": None, "parent_state": "none", "rounds": 0, "events": []})
    for parent in parents:
        near = [o for o in opps if M.same(o["company"], o["role"], parent["company"], parent["role"])]
        if not near:
            if parent["company"] and parent["role"]:
                opps.append({"company": parent["company"], "role": parent["role"], "applied_on": None, "ledger": False, "parent": parent["id"], "parent_state": "one", "rounds": parent["rounds"], "events": []})
            else:
                out["untitled_pages"] += 1                            # a page whose title is not "Company — Role" has no identity to match: counted, ignored, never a degraded state
        elif len(near) > 1 or near[0]["parent_state"] != "none":
            for o in near:
                o["parent"], o["parent_state"] = None, "ambiguous"
            reasons.add("IDENTITY_AMBIGUOUS")
        else:
            near[0]["parent"], near[0]["parent_state"], near[0]["rounds"] = parent["id"], "one", parent["rounds"]
    return opps


def _key(company, role):
    return "|".join(_tokens(company)) + "|" + "|".join(sorted(_tokens(role)))


def compile_rows(applied, parents, events, prior, ok, now):
    reasons, out = set(), {"unsupported": 0, "unmatched_events": 0, "untitled_pages": 0}
    if not ok["ledger"] or not ok["parents"]:                     # the two sources that decide WHO is in the pipeline: without both, only safe prior state may show
        reasons.add("LEDGER_UNAVAILABLE" if not ok["ledger"] else "OPPORTUNITIES_UNAVAILABLE")
        rows = [dict(r, carried=True) for r in prior["rows"]] if prior and prior.get("rows") else None
        return {**out, "rows": rows, "reasons": sorted(reasons)}
    prior_rows = {r["key"]: r for r in (prior or {}).get("rows", [])}
    opps = _opportunities(applied, parents, reasons, out)
    if ok["calendar"]:
        for event in events:
            kind = event_kind(event["title"])
            if kind is None:
                continue
            at, why = match_event(event["title"], opps)
            if at is not None:
                opps[at]["events"].append({"kind": kind, "start": event["start"]})
            elif why == "ambiguous":
                reasons.add("CALENDAR_AMBIGUOUS")
            else:
                out["unmatched_events"] += 1
    else:
        reasons.add("CALENDAR_UNAVAILABLE")
    rows = []
    for opp in opps:
        key = _key(opp["company"], opp["role"])
        before = prior_rows.get(key) or {}
        row = {"key": key, "company": opp["company"], "role": opp["role"], "applied_on": opp["applied_on"], "parent": opp["parent"], "parent_state": opp["parent_state"],
               "stage": None, "event_kind": None, "event_start": None, "rounds": opp["rounds"], "carried": False}
        event = _pick(opp["events"], now)
        if event:
            row.update(stage=_stage(event["kind"], event["start"], now), event_kind=event["kind"], event_start=event["start"])
        elif before.get("event_start"):
            # No current event: the accepted prior event stays as history and is never refreshed. A past one is simply past; a future one that has
            # vanished (or whose Calendar could not be read) is kept only as "last accepted" and the presentation is DEGRADED. A missing event never rolls the stage back.
            future = datetime.fromisoformat(before["event_start"]) > now
            row.update(stage=_stage(before["event_kind"], before["event_start"], now), event_kind=before["event_kind"], event_start=before["event_start"], carried=future or not ok["calendar"])
            if future and ok["calendar"]:
                reasons.add("EVENT_NOT_ON_CALENDAR")
        elif opp["rounds"]:
            row["stage"] = M.INTERVIEWING
        elif opp["ledger"]:
            row["stage"] = M.SUBMITTED
        if row["stage"] is None:                                  # a notes page with no round and no event: nothing supports a stage, so nothing is claimed
            out["unsupported"] += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: (-M.RANK.get(r["stage"], 0), r["event_start"] or "9", r["company"].lower(), r["role"].lower()))
    if not rows and reasons:                                      # incomplete evidence and nothing computed: carry the safe prior rows, else UNAVAILABLE, never an empty table
        rows = [dict(r, carried=True) for r in prior["rows"]] if prior and prior.get("rows") else None
    return {**out, "rows": rows, "reasons": sorted(reasons)}
