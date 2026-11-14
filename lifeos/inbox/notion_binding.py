"""How an action started in Notion is tied to the right person, record revision and grant: two signed events and an out-of-band code (REQ-7, LIFE-697); synthetic.

Nothing in a Notion page is a credential and no caller-supplied field names a person: who acted comes only from the HMAC-signed event's single person author, mapped through a server-side
allowlist. Step 1: that person sets "Requested action" on their row; Oracle reads the row and record fresh, checks policy, and stores a pending confirmation in the durable ledger (person,
workspace, account, record revision, action); a random code goes to that person out of band, never onto the row, valid 5 minutes, 3 tries. Step 2: the same person writes the code into
"Confirmation code" (a second signed event); only then is an ExactAuthorization minted. Notion proves no intent beyond the signed author id, so the verdict is PARTIAL: real execution stays off
(policy.LIVE_WRITES_ENABLED) and a live webhook needs a public HTTPS endpoint Jim has not approved. The event shape follows Notion's webhook docs and is UNVERIFIED live; the button's webhook is never used."""
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from lifeos.inbox import policy
from lifeos.inbox.ledger import digest, stamp
from lifeos.inbox.model import InboxError

EVENT_TYPE = "page.properties_updated"
REQUESTABLE = frozenset(a for actions in policy.MATRIX.values() for a in actions) - policy.READ_ACTIONS
PROP_ACTION, PROP_CODE = "requested_action", "confirmation_code"
CODE_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 3
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"                                           # no 0/O/1/I: read aloud or typed without doubt
CODE_LENGTH = 10                                                                           # 32^10 = 50 bits, with 3 tries and 5 minutes


@dataclass(frozen=True)
class Event:
    event_id: str
    at: datetime
    principal_id: str
    row_id: str
    updated: frozenset


@dataclass(frozen=True)
class Pending:
    intent_id: str
    principal_id: str
    action: str
    expires_at: datetime


def new_code(rng=secrets):
    return "".join(rng.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def verify_signature(verification_token, raw_body, header):
    """Constant-time check of `sha256=<hex>` over the exact raw bytes; anything else is False."""
    expected = "sha256=" + hmac.new(verification_token, raw_body, hashlib.sha256).hexdigest()
    return isinstance(header, str) and hmac.compare_digest(expected, header)


def parse_event(token, raw_body, header, *, notion_workspace_id, principals):
    """-> Event, or InboxError(reason). `principals`: {notion person id: principal id}, a server-side allowlist. Verifies the signature before anything is read from the body."""
    if not verify_signature(token, raw_body, header):
        raise InboxError("signature_invalid")
    try:
        event = json.loads(raw_body)
        at = datetime.fromisoformat(event["timestamp"])
    except (ValueError, KeyError, TypeError):
        raise InboxError("event_malformed") from None
    event_id = event.get("id")
    updated = (event.get("data") or {}).get("updated_properties")
    if not isinstance(event_id, str) or not event_id or at.tzinfo is None or not isinstance(updated, list) or not all(isinstance(p, str) for p in updated):
        raise InboxError("event_malformed")
    if event.get("type") != EVENT_TYPE:
        raise InboxError("event_type_unsupported")
    if event.get("workspace_id") != notion_workspace_id:
        raise InboxError("notion_workspace_mismatch")
    authors = event.get("authors") or []
    if len(authors) != 1 or not isinstance(authors[0], dict) or authors[0].get("type") != "person":
        raise InboxError("author_not_a_person")                                              # a bot, an agent or several authors never start or confirm an action
    principal_id = principals.get(authors[0].get("id"))
    if principal_id is None:
        raise InboxError("author_not_allowlisted")
    entity = event.get("entity") or {}
    if entity.get("type") != "page" or not isinstance(entity.get("id"), str):
        raise InboxError("row_unknown")
    return Event(event_id, at, principal_id, entity["id"], frozenset(updated))


def code_mac(key, intent_id, code):
    return hmac.new(key, "\x1f".join((intent_id, code)).encode(), hashlib.sha256).hexdigest()


def _fresh_row(read_row, row_map, row_id, principal_id):
    """The row as Notion has it now, or the reason it can no longer carry an action: deleted, unmapped, edited id column, or owned by somebody else now."""
    if row_id not in row_map:
        raise InboxError("row_unknown")
    try:
        row = read_row(row_id)
    except Exception:                                                                        # noqa: BLE001 - never quoted
        raise InboxError("row_unavailable") from None
    if not isinstance(row, dict):
        raise InboxError("row_deleted")
    if row.get("record_id") != row_map[row_id]:
        raise InboxError("row_tampered")                                                     # somebody edited the id column
    if row.get("principal_id") != principal_id:
        raise InboxError("row_reassigned")
    return row


def _fresh_record(read_record, record_id, principal, action, now):
    record = read_record(record_id)
    if record is None:
        raise InboxError("record_unavailable")
    allowed, reason = policy.decide(principal, record, action, now)                            # grants are read again: access revoked since the request refuses here
    if not allowed:
        raise InboxError(reason)
    if record.freshness(now) != "fresh":
        raise InboxError("record_stale")
    return record


def open_intent(ledger, event, *, key, row_map, read_row, read_record, principal_of, deliver, now, rng=secrets):
    """Step 1 -> Pending, or InboxError(reason). Nothing is authorized yet. `deliver(principal_id, intent_id, code)` sends the code to that person out of band; it is not stored anywhere readable."""
    if PROP_ACTION not in event.updated or PROP_CODE in event.updated:
        raise InboxError("event_coalesced" if PROP_ACTION in event.updated else "event_not_a_request")      # one event that changes both cannot be ordered: ask again
    if not ledger.claim_event(event.event_id, "request", now):
        raise InboxError("event_replayed")
    row = _fresh_row(read_row, row_map, event.row_id, event.principal_id)
    action = row.get("requested_action")
    if action not in REQUESTABLE:
        raise InboxError("no_valid_request")
    principal = principal_of(event.principal_id)
    record = _fresh_record(read_record, row_map[event.row_id], principal, action, now)
    intent_id, code = digest(event.event_id), new_code(rng)
    expires = now + CODE_TTL
    ledger.open_intent({"intent_id": intent_id, "event_id": event.event_id, "principal_id": event.principal_id, "source": record.source, "workspace": record.workspace,
                        "account": record.account, "record_id": record.record_id, "row_id": event.row_id, "action": action, "revision": record.revision, "event_at": stamp(event.at),
                        "nonce": digest("nonce:" + intent_id)[:32], "code_mac": code_mac(key, intent_id, code), "expires_at": stamp(expires)})
    deliver(event.principal_id, intent_id, code)
    return Pending(intent_id, event.principal_id, action, expires)


def confirm(ledger, event, *, key, row_map, read_row, read_record, principal_of, now):
    """Step 2 -> an ExactAuthorization, or InboxError(reason). The person is the signed event's author, never a parameter. Any change since step 1 invalidates the confirmation; a wrong code
    spends one of 3 tries; a right one is used once."""
    if PROP_CODE not in event.updated or PROP_ACTION in event.updated:
        raise InboxError("event_coalesced" if PROP_CODE in event.updated else "event_not_a_confirmation")
    if not ledger.claim_event(event.event_id, "confirm", now):
        raise InboxError("event_replayed")
    intent = ledger.pending_intent(event.row_id)
    if intent is None:
        raise InboxError("no_pending_request")
    intent_id = intent["intent_id"]

    def refuse(reason):
        ledger.close_intent(intent_id, "invalidated")
        raise InboxError(reason)
    if stamp(event.at) <= intent["event_at"]:
        raise InboxError("event_out_of_order")                                               # older than the request it answers: a late or reordered delivery changes nothing
    if event.principal_id != intent["principal_id"]:
        refuse("confirmer_mismatch")                                                         # somebody else typed in the row: the pending request is void
    if stamp(now) >= intent["expires_at"]:
        ledger.close_intent(intent_id, "expired")
        raise InboxError("challenge_expired")
    try:
        row = _fresh_row(read_row, row_map, event.row_id, event.principal_id)
    except InboxError as error:
        refuse(error.reason)
    if row.get("requested_action") != intent["action"] or row_map[event.row_id] != intent["record_id"]:
        refuse("intent_changed")                                                             # the row now asks for something else: never bind the old code to the new action
    principal = principal_of(event.principal_id)
    try:
        record = _fresh_record(read_record, intent["record_id"], principal, intent["action"], now)
    except InboxError as error:
        refuse(error.reason)
    if (record.revision, record.source, record.workspace, record.account) != (intent["revision"], intent["source"], intent["workspace"], intent["account"]):
        refuse("record_changed")
    if not ledger.spend_attempt(intent_id, MAX_ATTEMPTS):
        raise InboxError("challenge_locked")
    typed = row.get(PROP_CODE)
    if not isinstance(typed, str) or not hmac.compare_digest(code_mac(key, intent_id, typed.strip().upper()), intent["code_mac"]):
        raise InboxError("challenge_wrong")
    if not ledger.close_intent(intent_id, "confirmed"):
        raise InboxError("challenge_used")                                                   # a concurrent confirmation already consumed it
    return policy.mint(key, principal, record, intent["action"], intent["nonce"], now)
