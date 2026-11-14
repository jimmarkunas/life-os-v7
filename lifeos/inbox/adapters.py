"""One normalizer per source: a native record in the source's own field names becomes one Record (LIFE-685).

A normalizer only reads the native dict it is given and never calls a provider. The presentation status and the eligibility flags come from the source's own state, never from agent-written text."""
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.inbox.model import InboxError, Record, record_id, validate


def _when(text, tz=None):
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise InboxError("timestamp_invalid") from None
    if parsed.tzinfo is None:
        if not tz:
            raise InboxError("timestamp_not_timezone_aware")
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed


def _revision(status, updated):
    return hashlib.sha256(f"{status}\x1f{updated}".encode()).hexdigest()[:16]


def _build(source, workspace, account, raw, retrieved_at, health, *, native_id, url, title, description, assignee, status, presentation, kind, due=None, flags=(),
           approval_required=False, evidence=(), correlation=None):
    updated = _when(raw.get("updated"), raw.get("tz"))
    return validate(Record(
        record_id=record_id(source, workspace, account, native_id), source=source, workspace=workspace, account=account, native_id=native_id, url=url, title=title or "",
        description=description or "", assignee=assignee or "", source_status=status, presentation=presentation, due=due, due_tz=raw.get("tz") if due else None,
        source_updated_at=updated, retrieved_at=retrieved_at, health=health, kind=kind, flags=frozenset(flags), approval_required=approval_required,
        approval_evidence=tuple(evidence), correlation_id=correlation, revision=_revision(status, raw.get("updated"))))


def jira(workspace, account, raw, identity, now, retrieved_at, health):
    status = raw["status"]
    uat = status == "Human UAT"
    presentation = "approvals" if uat else "waiting" if status.lower() == "blocked" else "needs_me"
    return _build("jira", workspace, account, raw, retrieved_at, health, native_id=raw["key"], url=raw["url"], title=raw.get("summary"), description=raw.get("description"),
                  assignee=raw.get("assignee"), status=status, presentation=presentation, kind="human_uat" if uat else "task", due=_when(raw.get("due"), raw.get("tz")),
                  flags=("human_uat",) if uat else (), approval_required=uat, evidence=raw.get("evidence", ()) if uat else ())


def todoist(workspace, account, raw, identity, now, retrieved_at, health):
    due = _when(raw.get("due"), raw.get("tz"))
    presentation = "needs_me" if due and due <= now else "upcoming"
    personal = raw.get("project_scope") == "personal" and workspace == "personal"
    return _build("todoist", workspace, account, raw, retrieved_at, health, native_id=raw["id"], url=raw["url"], title=raw.get("content"), description=raw.get("description"),
                  assignee=raw.get("assignee"), status=raw["status"], presentation=presentation, kind="task", due=due, flags=("personal_task",) if personal else ())


def concord(workspace, account, raw, identity, now, retrieved_at, health):
    status = raw["status"]
    pending = status == "pending_approval"
    resumable = status == "approved" and raw.get("resume_eligible") is True
    presentation = "approvals" if pending else "needs_me" if resumable else "waiting"
    return _build("concord", workspace, account, raw, retrieved_at, health, native_id=raw["id"], url=raw["url"], title=raw.get("title"), description=raw.get("body"),
                  assignee=raw.get("agent"), status=status, presentation=presentation, kind="approval_request" if pending else "agent_work",
                  flags=("resumable",) if resumable else (), approval_required=pending, evidence=raw.get("evidence", ()) if pending else (), correlation=raw.get("correlation_id"))


def followup(workspace, account, raw, identity, now, retrieved_at, health):
    due = _when(raw.get("next_checkpoint"), raw.get("tz"))
    status = raw["status"]
    presentation = "waiting" if status == "waiting" else "needs_me" if due and due <= now else "upcoming"
    return _build("followup", workspace, account, raw, retrieved_at, health, native_id=raw["id"], url=raw["url"], title=raw.get("title"), description=raw.get("notes"),
                  assignee=raw.get("owner"), status=status, presentation=presentation, kind="checkpoint", due=due, correlation=raw.get("correlation_id"))


def calendar(workspace, account, raw, identity, now, retrieved_at, health):
    return _build("calendar", workspace, account, raw, retrieved_at, health, native_id=raw["id"], url=raw["url"], title=raw.get("summary"), description=raw.get("description"),
                  assignee=identity, status=raw["status"], presentation="upcoming", kind="event", due=_when(raw.get("start"), raw.get("tz")))


def asana(workspace, account, raw, identity, now, retrieved_at, health):
    blocked = raw.get("blocked") is True
    mine = raw.get("assignee") == identity
    return _build("asana", workspace, account, raw, retrieved_at, health, native_id=raw["gid"], url=raw["url"], title=raw.get("name"), description=raw.get("notes"),
                  assignee=raw.get("assignee"), status=raw["status"], presentation="waiting" if blocked else "needs_me" if mine else "upcoming", kind="task",
                  due=_when(raw.get("due"), raw.get("tz")))


NORMALIZERS = {"jira": jira, "todoist": todoist, "concord": concord, "followup": followup, "calendar": calendar, "asana": asana}


def normalize(source, workspace, account, raw, identity, now, retrieved_at, health="ok"):
    """-> one validated Record. A native record missing a field the source always has is InboxError(record_incomplete), never a guess."""
    try:
        return NORMALIZERS[source](workspace, account, raw, identity, now, retrieved_at, health)
    except KeyError:
        raise InboxError("record_incomplete") from None
