"""How an action started in Notion is tied to the right person, workspace, record and grant (REQ-7, synthetic); nothing in a Notion page is a credential.

Path: Jim edits the "Requested action" property on an Inbox row; Notion's integration webhook sends a signed page.properties_updated event (HMAC-SHA256 over the raw body, header
X-Notion-Signature sha256=hex). Oracle verifies it and takes only who (authors[0], a person id), where (workspace_id) and which row (entity.id) from it; the record and the action come from
Oracle's own row map and a fresh API read. A protected action then needs a challenge code, typed back by the same person, before an ExactAuthorization is minted.
The button "Send webhook" action is never used: it has no authentication or documented signature and cannot say who pressed it (Notion help, Webhook actions)."""
import hashlib
import hmac
import json
from dataclasses import dataclass

from lifeos.inbox import policy
from lifeos.inbox.model import InboxError

EVENT_TYPE = "page.properties_updated"
REQUESTABLE = frozenset(a for actions in policy.MATRIX.values() for a in actions) - policy.READ_ACTIONS


@dataclass(frozen=True)
class Intent:
    principal_id: str
    record_id: str
    action: str
    row_page_id: str
    event_id: str


def verify_signature(verification_token, raw_body, header):
    """Constant-time check of `sha256=<hex>` over the exact raw bytes; anything else is False."""
    expected = "sha256=" + hmac.new(verification_token, raw_body, hashlib.sha256).hexdigest()
    return isinstance(header, str) and hmac.compare_digest(expected, header)


def intent_from_event(token, raw_body, header, *, notion_workspace_id, principals, row_map, read_row, seen_events):
    """-> Intent, or InboxError(reason). `principals`: {notion person id: principal id} (server-side allowlist). `row_map`: {row page id: record id} (written by Oracle when it created the row).
    `read_row(page_id)` -> the row's properties fetched from the Notion API now. `seen_events`: a set of event ids already handled (Notion may retry a delivery up to 8 times)."""
    if not verify_signature(token, raw_body, header):
        raise InboxError("signature_invalid")
    try:
        event = json.loads(raw_body)
    except ValueError:
        raise InboxError("event_malformed") from None
    event_id = event.get("id")
    if not isinstance(event_id, str):
        raise InboxError("event_malformed")
    if event_id in seen_events:
        raise InboxError("event_replayed")
    if event.get("type") != EVENT_TYPE:
        raise InboxError("event_type_unsupported")
    if event.get("workspace_id") != notion_workspace_id:
        raise InboxError("notion_workspace_mismatch")
    authors = event.get("authors") or []
    if len(authors) != 1 or authors[0].get("type") != "person":
        raise InboxError("author_not_a_person")                                              # a bot, an agent or several authors never start an action
    principal_id = principals.get(authors[0].get("id"))
    if principal_id is None:
        raise InboxError("author_not_allowlisted")
    entity = event.get("entity") or {}
    row_id = entity.get("id")
    if entity.get("type") != "page" or row_id not in row_map:
        raise InboxError("row_unknown")
    row = read_row(row_id)                                                                   # fresh from the API: the event only ever names the row
    if row.get("record_id") != row_map[row_id]:
        raise InboxError("row_tampered")                                                     # somebody edited the id column
    action = row.get("requested_action")
    if action not in REQUESTABLE:
        raise InboxError("no_valid_request")
    seen_events.add(event_id)
    return Intent(principal_id, row_map[row_id], action, row_id, event_id)


def challenge_code(key, intent, revision, nonce):
    """The short code Oracle writes beside the row for a protected action. Only a person who can see the row can type it back; it is bound to this principal, record revision, action and nonce."""
    digest = hmac.new(key, "\x1f".join((intent.principal_id, intent.record_id, intent.action, revision, nonce)).encode(), hashlib.sha256).hexdigest()
    return digest[:8].upper()


def authorization_from_confirmation(key, principal, record, intent, nonce, typed_code, confirmer_principal_id, now):
    """-> ExactAuthorization when the SAME principal typed the right code for this exact record revision and action; else InboxError."""
    if confirmer_principal_id != principal.principal_id or intent.principal_id != principal.principal_id:
        raise InboxError("confirmer_mismatch")
    if intent.record_id != record.record_id:
        raise InboxError("record_mismatch")
    if not isinstance(typed_code, str) or not hmac.compare_digest(challenge_code(key, intent, record.revision, nonce), typed_code.strip().upper()):
        raise InboxError("challenge_wrong")
    return policy.mint(key, principal, record, intent.action, nonce, now)
