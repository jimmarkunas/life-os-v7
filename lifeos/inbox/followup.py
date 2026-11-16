"""Followup source for the Action Inbox (LIFE-710): UNAVAILABLE live, plus a bounded SYNTHETIC projection.

VERDICT (Nov 16 2026). No active canonical Followup source exists. Followup OS 8.1 is an approved future roadmap, not runtime; LIFE-180 is Cancelled; the repo holds only the synthetic
normalizer in adapters.py (LIFE-685); docs/platform/inbox-access-contract.md lists Followup as "no live source". Followup is also not a third-party API: it is a derived interpretation of Gmail,
Calendar, Jira, Notion, Todoist and Jim's instructions. So `FollowupSource` refuses every live read with a fixed word until a verified Followup-owned projection is named in
FOLLOWUP_PROJECTION_OWNER. Named human prerequisite: Jim decides which existing system owns the Followup projection (or approves promoting Followup OS 8.1 into the Daily Runs Production Contract).
No ledger, scheduler or store is created here.

`project()` is the bounded synthetic projection of the ticket's inherited MVP rules, over evidence items handed to it. It is a pure function: no storage, no provider, no network. Its output is native
dicts for `adapters.followup`, with provenance links only (never source payloads). Review-needed suggestions are returned apart and are excluded from active counts."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.inbox.model import InboxError
from lifeos.inbox.sources import _Source, refuse_live

FOLLOWUP_PROJECTION_OWNER = None            # set only when Jim names the existing system that owns a verified Followup projection
CLASSES = ("waiting_on_someone", "deliverable_owed", "post_interaction_value_add", "temporary_outcome_thread")
CREATES_LOOP = frozenset({"promise", "waiting_on", "deliverable", "value_add", "outcome_thread", "jim_capture"})       # explicit evidence kinds
IMPLIED = frozenset({"implied_promise"})                                                                               # strongly implied: needs Jim's confirmation, review only
NEVER_ALONE = frozenset({"unread_mail", "meeting_ended", "open_jira_task", "open_todoist_task"})                       # never produce a follow-up on their own
OUTCOME_ENDED = frozenset({"outcome_ended", "jim_resolved", "jim_abandoned"})                                          # the only strong closing proof
INTERMEDIATE = frozenset({"jira_done", "todoist_done", "chase_sent", "calendar_ended"})                                # never close the loop
ACTIVE = ("needs_action_today", "waiting_aging", "upcoming")


def refuse_followup(credential_ref):
    raise InboxError("followup_projection_missing" if FOLLOWUP_PROJECTION_OWNER is None else "live_reads_not_authorized")


class FollowupSource(_Source):
    """Live Followup reader: unavailable by design (see the module docstring). `open_reader` only matters once an owner is named and live reads are approved."""
    source = "followup"

    def __init__(self, workspace, account, identity, open_reader=refuse_followup, **kwargs):
        super().__init__(workspace, account, identity, open_reader=open_reader, **kwargs)

    def _read(self, read):
        loops = read()
        if not isinstance(loops, list):
            raise InboxError("listing_incomplete")
        return loops

    def _native(self, loop):
        return loop


class SyntheticFollowupSource(_Source):
    """Test and design aid ONLY: projects evidence handed in by the caller. Never registered anywhere, so it can never stand in for a live feed."""
    source = "followup"

    def __init__(self, workspace, account, identity, evidence, now, **kwargs):
        super().__init__(workspace, account, identity, open_reader=refuse_live, **kwargs)
        self._evidence, self._now = evidence, now

    def fetch(self, credential_ref):
        result = project(self._evidence, self._now, self.workspace)
        if result.degraded and not result.native:
            raise InboxError("evidence_degraded")                                               # never a trustworthy zero from missing, stale or denied evidence
        return result.native


@dataclass(frozen=True)
class Projection:
    native: list            # active loops as native followup dicts (needs action, waiting, upcoming)
    review: list            # Review Needed suggestions: separate, never counted
    degraded: bool          # missing, stale or denied evidence: last trustworthy state is kept, a zero is never trustworthy
    counts: dict            # active counts by state


def _dt(text):
    if not isinstance(text, str):
        return None
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("America/Chicago"))   # the loop's own zone, named in the native dict


def project(evidence, now, workspace="personal"):
    """`evidence`: a list of dicts {kind, outcome_key, workspace, owner (jim|external|ambiguous), who, title, link (https), checkpoint, checkpoint_source (source|suggested), override, observed_at,
    stale (bool), denied (bool)}. Returns a Projection. Evidence for another workspace or TGP is dropped; TGP never reaches here."""
    degraded, groups = False, {}
    for item in evidence:
        if item.get("workspace") != workspace:
            continue
        if item.get("denied") or item.get("stale") or not item.get("link"):
            degraded = True                                                                   # untrusted evidence never yields a trustworthy empty or closed state
            continue
        groups.setdefault(item["outcome_key"], []).append(item)
    native, review = [], []
    for key in sorted(groups):
        items = groups[key]
        kinds = {i["kind"] for i in items}
        if kinds & OUTCOME_ENDED:
            continue                                                                          # strong proof the OUTCOME ended: closed (intermediate kinds are ignored below)
        explicit = [i for i in items if i["kind"] in CREATES_LOOP]
        implied = [i for i in items if i["kind"] in IMPLIED]
        if not explicit and not implied:
            continue                                                                          # unread mail, a finished meeting, an open Jira/Todoist task or an intermediate completion alone
        owners = {i["owner"] for i in explicit if i.get("owner") in ("jim", "external")}
        if len(owners) > 1:                                                                   # contradictory observations never auto-merge: each stays its own review-only entry
            for n, item in enumerate(explicit):
                review.append({"id": f"{key}#{n}", "url": item["link"], "title": item.get("title", ""), "notes": "contradictory evidence", "owner": item.get("who", ""),
                               "status": "open", "next_checkpoint": item.get("checkpoint"), "tz": "America/Chicago", "updated": item["observed_at"], "correlation_id": None, "state": "review_needed"})
            continue
        lead = (explicit or implied)[0]
        links = sorted({i["link"] for i in items})
        override = next((i["override"] for i in items if i.get("override")), None)           # Jim's checkpoint override: its own field, source dates are never touched
        dated = next((i for i in explicit if i.get("checkpoint") and i.get("checkpoint_source") == "source"), None)
        checkpoint = override or (dated or lead).get("checkpoint")
        indicator = "jim_override" if override else "source-backed" if dated else "suggested"
        loop = {"id": key, "url": links[0], "title": lead.get("title", ""), "notes": f"checkpoint {indicator}; evidence {len(links)}", "owner": lead.get("who", ""),
                "status": "waiting" if (lead.get("owner") == "external") else "open", "next_checkpoint": checkpoint, "tz": "America/Chicago", "updated": max(i["observed_at"] for i in items),
                "correlation_id": None, "state": ""}
        if implied and not explicit or lead.get("owner") == "ambiguous" or (indicator == "suggested" and not override):
            loop["state"] = "review_needed"                                                   # suggestions, implied, ambiguous or contradictory observations stay review-only
            review.append(loop)
            continue
        due = _dt(checkpoint)
        loop["state"] = "waiting_aging" if loop["status"] == "waiting" else "needs_action_today" if due and due <= now else "upcoming"
        native.append(loop)
    counts = {s: sum(1 for loop in native if loop["state"] == s) for s in ACTIVE}
    return Projection([{k: v for k, v in loop.items() if k != "state"} for loop in native], review, degraded, counts)
