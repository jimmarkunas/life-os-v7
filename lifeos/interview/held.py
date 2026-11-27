"""A Held round never asks to verify scheduling.

The round snapshot's Next action is written once as the Verify-scheduling sentence when no Calendar event is linked.
Once the page reads Held, this module replaces exactly that sentence, and nothing else, with the page's own
follow-up: the Recommended Follow-Up action, else a real `Follow-up owed / due` value, else `None`. Any other Next
action is never touched, so a second run writes nothing."""

from lifeos.platform.notion_client import NotionError, rich_text
from . import notion
from .template import VERIFY_SCHEDULING

NEXT = "Next action: "
NONE = "None"
LIMIT = 300
FOLLOW_UP_HEADING = "recommended follow-up"
OWED = "Follow-up owed / due: "
UNSET = ("Unverified", "Not verified", "None", "Unknown")


def _walk(client, page_id, budget, context, depth=0):
    for block in notion.children(client, page_id, budget, context):
        yield block
        if block.get("has_children") and block.get("type") not in ("child_page", "child_database") and depth < 4:
            yield from _walk(client, block["id"], budget, context, depth + 1)


def follow_up(client, page_id, context=None):
    """-> the page's own next action: the Recommended Follow-Up action, else a `Follow-up owed / due` value, else `None`."""
    budget = [notion.MAX_CALLS]
    blocks = list(_walk(client, page_id, budget, context))
    texts = [notion.text(b).strip() for b in blocks]
    for index, line in enumerate(texts):
        if line.lower().strip("# *:") == FOLLOW_UP_HEADING:
            for after in texts[index + 1 : index + 4]:
                if after.lower().startswith("action:"):
                    action = after[len("action:") :].strip()
                    if action:
                        return action[:LIMIT]
    for line in texts:
        if line.startswith(OWED):
            value = line[len(OWED) :].strip()
            if value and not value.startswith(UNSET) and "no task or deadline is created" not in value:
                return value[:LIMIT]
    return NONE


def repair(client, page_id, live, counts, context=None):
    """-> a fixed code. Replaces the machine's Verify sentence on a Held round; other Next actions are kept."""
    try:
        tops = notion.children(client, page_id, [notion.MAX_CALLS], context)
        callouts = [b for b in tops[1:] if b.get("type") == "callout"]
        if not tops or not notion.is_fold(tops[0]) or not callouts:
            return "held_unreadable"
        line = next(
            (
                b
                for b in notion.children(client, callouts[0]["id"], [notion.MAX_CALLS], context)
                if notion.text(b) == NEXT + VERIFY_SCHEDULING
            ),
            None,
        )
        if line is None:
            return "held_unchanged"
        value = follow_up(client, page_id, context)
        counts["held_planned"] = counts.get("held_planned", 0) + 1
        if not live:
            return "held_allowed"
        before = notion.protected_snapshot(client, page_id, "round")
        if before is None:
            return "held_unreadable"
        kind = line["type"]
        label = notion.writable(line[kind]["rich_text"])[:1]
        notion.set_text(client, line["id"], kind, label + rich_text(value))
        if notion.protected_snapshot(client, page_id, "round") != before:
            return "held_protected_changed"
        counts["held_repaired"] = counts.get("held_repaired", 0) + 1
        return "held_repaired"
    except (NotionError, KeyError, TypeError, ValueError, AttributeError):
        counts["held_failed"] = counts.get("held_failed", 0) + 1
        return "held_failed"
