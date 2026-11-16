"""Followup source for the Action Inbox (LIFE-710, LIFE-749): reads the `followup` stage's payload; plus a bounded SYNTHETIC projection."""
# LIFE-749 (Jim, D258): the owner of the Followup projection is now named: the `followup` stage (lifeos/inbox/followup_stage.py) derives thread state from the LIFE-747 store plus the LIFE-748
# reader deltas with `project()` below and publishes ONE component-state payload. `FollowupSource` reads that payload through an injected loader. It still refuses while LIVE_READS_ENABLED is
# False (the default reader), and a missing, stale, mismatched or degraded-and-empty payload is `unavailable`, never a trustworthy zero. Nothing is wired in production: no live read, store or schedule.
#
# `project()` is the bounded synthetic projection of the ticket's inherited MVP rules, over evidence items handed to it. It is a pure function: no storage, no provider, no network. Its output is native
# dicts for `adapters.followup`, with provenance links only (never source payloads). Review-needed suggestions are returned apart and are excluded from active counts.
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lifeos.inbox.model import InboxError
from lifeos.inbox import sources
from lifeos.inbox.sources import _Source, refuse_live
from lifeos.platform import ct_stamp

FOLLOWUP_PROJECTION_OWNER = "lifeos.inbox.followup_stage"       # D258: the `followup` stage owns the projection; it is unwired in production until the live-wiring step
STALE_AFTER = timedelta(hours=2)                                # a payload older than this is not evidence of anything current
CLASSES = ("waiting_on_someone", "deliverable_owed", "post_interaction_value_add", "temporary_outcome_thread")
CREATES_LOOP = frozenset({"promise", "waiting_on", "deliverable", "value_add", "outcome_thread", "jim_capture"})       # explicit evidence kinds
IMPLIED = frozenset({"implied_promise"})                                                                               # strongly implied: needs Jim's confirmation, review only
NEVER_ALONE = frozenset({"unread_mail", "meeting_ended", "open_jira_task", "open_todoist_task"})                       # never produce a follow-up on their own
OUTCOME_ENDED = frozenset({"outcome_ended", "jim_resolved", "jim_abandoned"})                                          # the only strong closing proof
INTERMEDIATE = frozenset({"jira_done", "todoist_done", "chase_sent", "calendar_ended"})                                # never close the loop
ACTIVE = ("needs_action_today", "waiting_aging", "upcoming")


def refuse_followup(credential_ref):
    """The default `open_reader`: no payload loader is wired, so a read is refused with the fixed live-read word and nothing is built."""
    refuse_live(credential_ref)


def payload_reader(load):
    """An `open_reader` that reads the stage's payload through `load(credential_ref) -> payload dict or None` (the host side; none exists in production yet). Still refuses while LIVE_READS_ENABLED is False."""
    def open_reader(credential_ref):
        if not sources.LIVE_READS_ENABLED:
            refuse_live(credential_ref)
        return lambda: load(credential_ref)
    return open_reader


class FollowupSource(_Source):
    """Reads the `followup` stage's component-state payload. `now` is an aware datetime (UTC when omitted). Output is native dicts for the active threads only: the Review Needed list stays out."""
    source = "followup"

    def __init__(self, workspace, account, identity, open_reader=refuse_followup, now=None, **kwargs):
        super().__init__(workspace, account, identity, open_reader=open_reader, **kwargs)
        self._now = now

    def _read(self, read):
        payload = read()
        if payload is None:
            raise InboxError("followup_projection_missing")
        threads = payload.get("threads") if isinstance(payload, dict) else None
        if not isinstance(threads, list) or payload.get("component") != "followup" or payload.get("state") not in ("PASS", "DEGRADED"):
            raise InboxError("listing_incomplete")
        if payload.get("workspace") != self.workspace or payload.get("account") != self.account:
            raise InboxError("payload_scope_mismatch")                                      # another workspace's or account's payload is never shown here
        try:
            generated = _dt(payload.get("generated_at"))
        except (TypeError, ValueError):
            generated = None
        now = self._now or datetime.now(timezone.utc)
        if generated is None or generated > now + timedelta(minutes=5) or now - generated > STALE_AFTER:
            raise InboxError("evidence_stale")                                              # old or undated: not a current read, never a trustworthy empty list
        if payload["state"] == "DEGRADED":
            if not threads:
                raise InboxError("evidence_degraded")
            try:
                accepted = ct_stamp.when(_dt(payload.get("last_accepted")))                    # Central Time, the one shared form
            except (TypeError, ValueError, AttributeError):
                accepted = "an unknown time"
            note = "; DEGRADED, last accepted " + accepted
            return [{**t, "notes": f"{t.get('notes', '')}{note}"} for t in threads if isinstance(t, dict)]      # the last accepted threads stay visible, visibly degraded
        return threads

    def _native(self, loop):
        return {k: v for k, v in loop.items() if k != "state"}


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
    states: dict = field(default_factory=dict)      # LIFE-749: thread id -> state (needs_action_today, waiting_aging, upcoming, review_needed) for the stage's payload


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
    states = {**{loop["id"]: loop["state"] for loop in native}, **{loop["id"]: "review_needed" for loop in review}}
    return Projection([{k: v for k, v in loop.items() if k != "state"} for loop in native], review, degraded, counts, states)
