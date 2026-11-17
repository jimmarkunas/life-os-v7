"""LIFE-716: the bounded Followup classifier. Turns an item Jim wrote himself into explicit follow-up evidence ONLY when its title carries an explicit marker; everything else is observation."""
# Evidence: the delta readers' minimal items (source, id, link, observed time, a title of at most 120 characters, and a due date only when the SOURCE holds one). No body, snippet, recipient or
# attendee is read, so mail and calendar items are observation only: an unread message, a finished meeting, an open Jira ticket or a plain Todoist task never creates a loop. A loop exists only for
# an item in Jim's own task or notes systems (Todoist, Jira, Notion, Interview OS) whose title STARTS with one of the markers below. The marker is Jim's explicit capture; the text after it is the outcome.
# The due date the source holds is the source-backed checkpoint. No due date means a suggested checkpoint, which `followup.project` keeps in Review Needed and never counts.
# Same outcome in several sources merges on the outcome text; the same person with a different outcome is a different key. Closing proof is not read from titles: a loop closes only through the
# store's strong-proof path (`outcome_ended`, Jim's resolve), never because a task was completed, so completing the task never closes the outcome.
import re

ELIGIBLE = frozenset({"todoist", "jira", "notion", "interview_os"})          # Jim's own capture systems: mail and calendar need body or attendee access to mean anything
MAX_KEY = 120
MARKERS = (                                                                    # (pattern at the start of the title, evidence kind, owner)
    (re.compile(r"^\s*(?:waiting\s+(?:on|for)\b|wfr\s*:)[\s:.\-]*", re.I), "waiting_on", "external"),
    (re.compile(r"^\s*(?:follow[\s\-]?up\b|fu\s*:)[\s:.\-]*", re.I), "jim_capture", "jim"),
    (re.compile(r"^\s*(?:i\s+owe\b|owe\b|promised\b|promise\s*:)[\s:.\-]*", re.I), "promise", "jim"),
    (re.compile(r"^\s*deliverable\s*:\s*", re.I), "deliverable", "jim"),
    (re.compile(r"^\s*value[\s\-]?add\s*:\s*", re.I), "value_add", "jim"),
)
_SPACE = re.compile(r"\s+")


def outcome_of(title):
    """The outcome text after the marker, whitespace collapsed and trailing punctuation dropped; None when it is too short or too long to be a store identity."""
    text = _SPACE.sub(" ", str(title or "")).strip().rstrip(".,;:!").strip()
    return text if 3 <= len(text) <= MAX_KEY else None


def classify(item):
    """-> an evidence dict for `followup.project` (workspace is added by the stage), or None (observation only)."""
    if not isinstance(item, dict) or item.get("source") not in ELIGIBLE:
        return None
    title = str(item.get("title") or "")
    for pattern, kind, owner in MARKERS:
        found = pattern.match(title)
        if not found:
            continue
        outcome = outcome_of(title[found.end():])
        if outcome is None:
            return None
        due = item.get("due")
        return {"kind": kind, "outcome_key": outcome, "owner": owner, "who": "", "title": outcome, "link": item["link"], "checkpoint": due or None,
                "checkpoint_source": "source" if due else "suggested", "observed_at": item["observed_at"]}
    return None


__all__ = ["ELIGIBLE", "MARKERS", "classify", "outcome_of"]
