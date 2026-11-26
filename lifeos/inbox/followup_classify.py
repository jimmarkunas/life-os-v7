"""The bounded Followup classifier: an item becomes follow-up evidence only when its title starts with an explicit
marker.

Input is the delta readers' minimal items (source, id, link, observed time, a title of at most 120 characters, a due
date only when the SOURCE holds one); no body or attendee is read. Only items in the user's own task or notes systems
create a loop; mail, calendar, open Jira tickets and plain Todoist tasks are observation only. The text after the
marker is the outcome and the source's due date is the checkpoint (none: a suggestion, never counted). A loop closes
only through the store's strong-proof path (`outcome_ended`), never because a task was completed.
"""
import re

ELIGIBLE = frozenset({"todoist", "jira", "notion", "interview_os"})
MAX_KEY = 120
MARKERS = (
    (re.compile(r"^\s*(?:waiting\s+(?:on|for)\b|wfr\s*:)[\s:.\-]*", re.I), "waiting_on", "external"),
    (re.compile(r"^\s*(?:follow[\s\-]?up\b|fu\s*:)[\s:.\-]*", re.I), "jim_capture", "jim"),
    (re.compile(r"^\s*(?:i\s+owe\b|owe\b|promised\b|promise\s*:)[\s:.\-]*", re.I), "promise", "jim"),
    (re.compile(r"^\s*deliverable\s*:\s*", re.I), "deliverable", "jim"),
    (re.compile(r"^\s*value[\s\-]?add\s*:\s*", re.I), "value_add", "jim"),
)
_SPACE = re.compile(r"\s+")


def outcome_of(title):
    """The outcome text after the marker, normalized; None when too short or too long to be a store identity."""
    text = _SPACE.sub(" ", str(title or "")).strip().rstrip(".,;:!").strip()
    return text if 3 <= len(text) <= MAX_KEY else None


def classify(item):
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
