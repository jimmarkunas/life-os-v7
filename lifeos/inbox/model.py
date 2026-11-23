"""The one normalized Action Inbox record and its validation (LIFE-685).

A record is a pointer plus presentation facts about a task another system owns: it never replaces the source record and never infers completion.
Errors are InboxError with a fixed lower-case reason word; no message ever carries record content."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from lifeos.platform.inbox_error import InboxError  # noqa: F401 - LIFE-876: defined in platform (shared with the governed MCP); this name keeps working

SOURCES = ("jira", "todoist", "concord", "followup", "calendar", "asana")
WORKSPACES = ("personal", "bytalos")                    # released together (Jim, Nov 13 2026); each is authorized, credentialed and read on its own
UNRELEASED_WORKSPACES = ("tgp",)                        # excluded from this release: never loaded, never acted on
PRESENTATION = ("needs_me", "waiting", "approvals", "upcoming")
HEALTH = ("ok", "degraded", "unavailable", "no_grant", "refused")
FRESH_FOR = timedelta(minutes=15)                       # a record older than this since its retrieval is stale and can support no action or completion claim
_PART = re.compile(r"^[A-Za-z0-9_.@:-]{1,80}$")


def record_id(source, workspace, account, native_id):
    """The canonical source-qualified id: the same title in two sources, accounts or workspaces is always a different record."""
    for part in (source, workspace, account, native_id):
        if not isinstance(part, str) or not _PART.match(part):
            raise InboxError("id_part_invalid")
    return f"{source}:{workspace}:{account}:{native_id}"


@dataclass(frozen=True)
class Record:
    record_id: str
    source: str
    workspace: str
    account: str
    native_id: str
    url: str                                  # the original record in its own system
    title: str
    description: str
    assignee: str                             # the human or party responsible, as the source names them
    source_status: str                        # exactly the source's own status word
    presentation: str                         # one of PRESENTATION
    due: object                               # tz-aware datetime or None (due date or next checkpoint)
    due_tz: object                            # the IANA zone name the source gave, or None
    source_updated_at: object
    retrieved_at: object
    health: str                               # the connector's state when this was read: ok | degraded
    kind: str                                 # task | human_uat | approval_request | agent_work | checkpoint | event
    flags: frozenset = frozenset()            # eligibility facts the adapter proved from the source record (human_uat, personal_task, resumable)
    approval_required: bool = False
    approval_evidence: tuple = ()             # references (urls or ids) to the evidence, never its content
    correlation_id: object = None
    revision: str = ""                        # changes whenever the source status or update time changes; an action is tied to one revision

    @property
    def label(self):
        return self.workspace.title()

    def freshness(self, now):
        return "fresh" if now - self.retrieved_at <= FRESH_FOR else "stale"


def _aware(value, name):
    if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
        raise InboxError(f"{name}_not_timezone_aware")


def validate(record):
    """-> the record, or InboxError(reason). TGP and any unknown workspace are refused before anything else is looked at."""
    if record.workspace in UNRELEASED_WORKSPACES:
        raise InboxError("workspace_not_released")
    if record.workspace not in WORKSPACES:
        raise InboxError("workspace_unknown")
    if record.source not in SOURCES:
        raise InboxError("source_unknown")
    if record.record_id != record_id(record.source, record.workspace, record.account, record.native_id):
        raise InboxError("record_id_mismatch")
    if record.presentation not in PRESENTATION:
        raise InboxError("presentation_unknown")
    if record.health not in ("ok", "degraded"):
        raise InboxError("health_unknown")
    if not isinstance(record.url, str) or not record.url.startswith("https://"):
        raise InboxError("source_url_invalid")
    if not record.source_status or not isinstance(record.title, str):
        raise InboxError("record_incomplete")
    for name in ("due", "source_updated_at", "retrieved_at"):
        _aware(getattr(record, name), name)
    if record.source_updated_at is None or record.retrieved_at is None:
        raise InboxError("timestamps_missing")
    if record.approval_required and not record.approval_evidence:
        raise InboxError("approval_without_evidence")
    return record
