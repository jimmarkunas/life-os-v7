"""Followup source for the Action Inbox: reads the `followup` stage's payload, plus a bounded SYNTHETIC projection.

`FollowupSource` reads the payload through an injected loader and refuses while LIVE_READS_ENABLED is False. A
missing, stale, mismatched or degraded-and-empty payload is `unavailable`, never a trustworthy zero. `project()` is
pure: it turns evidence into native dicts for `adapters.followup` (provenance links only), with Review Needed apart
and never counted.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lifeos.inbox.model import InboxError
from lifeos.inbox import sources
from lifeos.inbox.sources import _Source, refuse_live
from lifeos.platform import ct_stamp

FOLLOWUP_PROJECTION_OWNER = "lifeos.inbox.followup_stage"
STALE_AFTER = timedelta(hours=2)
CLASSES = ("waiting_on_someone", "deliverable_owed", "post_interaction_value_add", "temporary_outcome_thread")
CREATES_LOOP = frozenset({"promise", "waiting_on", "deliverable", "value_add", "outcome_thread", "jim_capture"})
IMPLIED = frozenset({"implied_promise"})
NEVER_ALONE = frozenset({"unread_mail", "meeting_ended", "open_jira_task", "open_todoist_task"})
OUTCOME_ENDED = frozenset({"outcome_ended", "jim_resolved", "jim_abandoned"})
INTERMEDIATE = frozenset({"jira_done", "todoist_done", "chase_sent", "calendar_ended"})
ACTIVE = ("needs_action_today", "waiting_aging", "upcoming")


def refuse_followup(credential_ref):
    refuse_live(credential_ref)


def payload_reader(load):
    def open_reader(credential_ref):
        if not sources.LIVE_READS_ENABLED:
            refuse_live(credential_ref)
        return lambda: load(credential_ref)

    return open_reader


class FollowupSource(_Source):
    """Reads the stage's payload; `now` is an aware datetime (UTC when omitted). Returns the active threads only."""

    source = "followup"

    def __init__(self, workspace, account, identity, open_reader=refuse_followup, now=None, **kwargs):
        super().__init__(workspace, account, identity, open_reader=open_reader, **kwargs)
        self._now = now

    def _read(self, read):
        payload = read()
        if payload is None:
            raise InboxError("followup_projection_missing")
        threads = payload.get("threads") if isinstance(payload, dict) else None
        if (
            not isinstance(threads, list)
            or payload.get("component") != "followup"
            or payload.get("state") not in ("PASS", "DEGRADED")
        ):
            raise InboxError("listing_incomplete")
        if payload.get("workspace") != self.workspace or payload.get("account") != self.account:
            raise InboxError("payload_scope_mismatch")
        try:
            generated = _dt(payload.get("generated_at"))
        except (TypeError, ValueError):
            generated = None
        now = self._now or datetime.now(timezone.utc)
        if generated is None or generated > now + timedelta(minutes=5) or now - generated > STALE_AFTER:
            raise InboxError("evidence_stale")
        if payload["state"] == "DEGRADED":
            if not threads:
                raise InboxError("evidence_degraded")
            try:
                accepted = ct_stamp.when(_dt(payload.get("last_accepted")))
            except (TypeError, ValueError, AttributeError):
                accepted = "an unknown time"
            note = "; DEGRADED, last accepted " + accepted
            return [{**t, "notes": f"{t.get('notes', '')}{note}"} for t in threads if isinstance(t, dict)]
        return threads

    def _native(self, loop):
        return {k: v for k, v in loop.items() if k != "state"}


class SyntheticFollowupSource(_Source):
    """Test and design aid ONLY: projects evidence handed in by the caller. Never registered as a live feed."""

    source = "followup"

    def __init__(self, workspace, account, identity, evidence, now, **kwargs):
        super().__init__(workspace, account, identity, open_reader=refuse_live, **kwargs)
        self._evidence, self._now = evidence, now

    def fetch(self, credential_ref):
        result = project(self._evidence, self._now, self.workspace)
        if result.degraded and not result.native:
            raise InboxError("evidence_degraded")
        return result.native


@dataclass(frozen=True)
class Projection:
    native: list
    review: list
    degraded: bool
    counts: dict
    states: dict = field(default_factory=dict)  # thread id -> state


def _dt(text):
    if not isinstance(text, str):
        return None
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("America/Chicago"))


def project(evidence, now, workspace="personal"):
    """`evidence`: dicts {kind, outcome_key, workspace, owner (jim|external|ambiguous), who, title, link (https),
    checkpoint, checkpoint_source (source|suggested), override, observed_at, stale, denied}. Returns a Projection;
    evidence for another workspace or TGP is dropped.
    """
    degraded, groups = False, {}
    for item in evidence:
        if item.get("workspace") != workspace:
            continue
        if item.get("denied") or item.get("stale") or not item.get("link"):
            degraded = True
            continue
        groups.setdefault(item["outcome_key"], []).append(item)
    native, review = [], []
    for key in sorted(groups):
        items = groups[key]
        kinds = {i["kind"] for i in items}
        if kinds & OUTCOME_ENDED:
            continue
        explicit = [i for i in items if i["kind"] in CREATES_LOOP]
        implied = [i for i in items if i["kind"] in IMPLIED]
        if not explicit and not implied:
            continue
        owners = {i["owner"] for i in explicit if i.get("owner") in ("jim", "external")}
        if len(owners) > 1:  # contradictory observations never auto-merge: each stays its own review-only entry
            for n, item in enumerate(explicit):
                review.append(
                    {
                        "id": f"{key}#{n}",
                        "url": item["link"],
                        "title": item.get("title", ""),
                        "notes": "contradictory evidence",
                        "owner": item.get("who", ""),
                        "status": "open",
                        "next_checkpoint": item.get("checkpoint"),
                        "tz": "America/Chicago",
                        "updated": item["observed_at"],
                        "correlation_id": None,
                        "state": "review_needed",
                    }
                )
            continue
        lead = (explicit or implied)[0]
        links = sorted({i["link"] for i in items})
        override = next((i["override"] for i in items if i.get("override")), None)
        dated = next((i for i in explicit if i.get("checkpoint") and i.get("checkpoint_source") == "source"), None)
        checkpoint = override or (dated or lead).get("checkpoint")
        indicator = "jim_override" if override else "source-backed" if dated else "suggested"
        loop = {
            "id": key,
            "url": links[0],
            "title": lead.get("title", ""),
            "notes": f"checkpoint {indicator}; evidence {len(links)}",
            "owner": lead.get("who", ""),
            "status": "waiting" if (lead.get("owner") == "external") else "open",
            "next_checkpoint": checkpoint,
            "tz": "America/Chicago",
            "updated": max(i["observed_at"] for i in items),
            "correlation_id": None,
            "state": "",
        }
        if implied and not explicit or lead.get("owner") == "ambiguous" or (indicator == "suggested" and not override):
            loop["state"] = "review_needed"
            review.append(loop)
            continue
        due = _dt(checkpoint)
        loop["state"] = (
            "waiting_aging"
            if loop["status"] == "waiting"
            else "needs_action_today"
            if due and due <= now
            else "upcoming"
        )
        native.append(loop)
    counts = {s: sum(1 for loop in native if loop["state"] == s) for s in ACTIVE}
    states = {**{loop["id"]: loop["state"] for loop in native}, **{loop["id"]: "review_needed" for loop in review}}
    return Projection(
        [{k: v for k, v in loop.items() if k != "state"} for loop in native], review, degraded, counts, states
    )
