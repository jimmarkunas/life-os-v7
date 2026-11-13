"""Immutable Request/Response handoff for the private Interview Advisor queue."""
from dataclasses import dataclass, field, replace
from enum import Enum
import json
import re
from urllib.parse import quote

from . import advisor, notion
from .advisor_store import AdvisorStoreError, VerifiedPreview
from .models import PrepEvidence
from lifeos.platform import notion_pages
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError

_QUEUE_TITLE = "Advisor Queue"
_QUEUE_MARKER = "v7-interview-advisor:1;kind=queue"
_REQUEST = re.compile(r"^Advisor Request ([0-9a-f]{32})$")
_RESPONSE = re.compile(r"^Advisor Response ([0-9a-f]{32})$")
_MARKER = re.compile(r"^v7-interview-advisor-request:1;([0-9a-f]{32});([0-9a-f]{64})$")
_RESPONSE_MARKER = re.compile(r"^v7-interview-advisor-response:1;([0-9a-f]{32});([0-9a-f]{64})$")
_REQUEST_MAX = 180000
_CALLS = 40
_BUNDLE_KEYS = ("company", "role", "interview_date", "interviewer", "ordinal", "source_job_id", "manifest", "sources", "evidence")
_DRAFT_KEYS = ("straight_line", "game_theory", "decision_criteria", "objections", "close_strategy", "focus",
               "strongest_evidence_refs", "pressure_points", "questions")
_ADVICE_KEYS = ("text", "source_refs")
_CODES = {"advisor_queue_invalid", "advisor_queue_incomplete", "advisor_queue_conflict", "advisor_queue_too_large",
          "advisor_queue_write_failed", "advisor_queue_readback_failed", "advisor_queue_response_invalid",
          "advisor_queue_ambiguous", "advisor_queue_preview_mismatch"}
_DEFINITE = {400, 401, 403, 404, 409, 429}


class AdvisorQueueError(ValueError):
    def __init__(self, code="advisor_queue_invalid"):
        self.code = code if code in _CODES else "advisor_queue_invalid"
        super().__init__(self.code)

    def __str__(self):
        return self.code


def _need(condition, code="advisor_queue_invalid"):
    if not condition:
        raise AdvisorQueueError(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_value(source):
    return {"source_id": source.source_id, "kind": source.kind.value, "text": source.text,
            "version": source.version, "source_round": source.source_round, "active": source.active}


def _bundle_value(bundle):
    _need(isinstance(bundle, advisor.AdvisorInputBundle))
    manifest = {name: getattr(bundle.manifest, name) for name in advisor.AdvisorManifest.__dataclass_fields__}
    return {"company": bundle.company, "role": bundle.role, "interview_date": bundle.interview_date,
            "interviewer": bundle.interviewer, "ordinal": bundle.ordinal, "source_job_id": bundle.source_job_id,
            "manifest": manifest, "sources": [_source_value(source) for source in bundle.sources],
            "evidence": [{"evidence_id": item.ref.evidence_id, "version": item.ref.version,
                          "canonical_text": item.canonical_text, "source_ref": item.source_ref,
                          "tags": list(item.tags), "active": item.active} for item in bundle.evidence]}


def serialize_bundle(bundle):
    with fixed(Exception, AdvisorQueueError, "advisor_queue_invalid", keep=AdvisorQueueError):
        payload = _canonical(_bundle_value(bundle))
    _need(len(payload) <= _REQUEST_MAX, "advisor_queue_too_large")
    return payload


def _object(value, keys):
    _need(type(value) is dict and set(value) == set(keys))
    return value


def parse_bundle(payload):
    _need(isinstance(payload, str))
    _need(len(payload) <= _REQUEST_MAX, "advisor_queue_too_large")
    with fixed(Exception, AdvisorQueueError, "advisor_queue_invalid", keep=AdvisorQueueError):
        value = _object(json.loads(payload), _BUNDLE_KEYS)
        md = _object(value["manifest"], tuple(advisor.AdvisorManifest.__dataclass_fields__))
        manifest = advisor.AdvisorManifest(**md)
        _need(isinstance(value["sources"], list) and isinstance(value["evidence"], list))
        sources = []
        for item in value["sources"]:
            item = _object(item, ("source_id", "kind", "text", "version", "source_round", "active"))
            sources.append(advisor.AdvisorSource(item["source_id"], advisor.SourceKind(item["kind"]), item["text"],
                                                   item["version"], item["source_round"], item["active"]))
        evidence = []
        for item in value["evidence"]:
            item = _object(item, ("evidence_id", "version", "canonical_text", "source_ref", "tags", "active"))
            _need(isinstance(item["tags"], list))
            evidence.append(advisor.CandidateEvidence(advisor.EvidenceRef(item["evidence_id"], item["version"]),
                item["canonical_text"], item["source_ref"], tuple(item["tags"]), item["active"]))
        result = advisor.AdvisorInputBundle(value["company"], value["role"], value["interview_date"],
            value["interviewer"], value["ordinal"], value["source_job_id"], manifest, tuple(sources), tuple(evidence))
        _need(serialize_bundle(result) == payload)
        advisor.bundle_digest(result)
        return result


@dataclass(frozen=True)
class QueueRequest:
    request_id: str
    bundle_hash: str
    bundle: advisor.AdvisorInputBundle = field(repr=False)
    page_id: str = field(repr=False)


@dataclass(frozen=True)
class QueueResponse:
    request_id: str
    bundle_hash: str
    draft: advisor.AdvisorDraft = field(repr=False)
    prep: PrepEvidence = field(repr=False)
    page_id: str = field(repr=False)


class QueueState(str, Enum):
    READY = "ready"
    RESPONDED = "responded"
    PREVIEWED = "previewed"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class QueueItem:
    request: QueueRequest = field(repr=False)
    response: QueueResponse | None = field(repr=False)
    state: QueueState


def _plain(block):
    with fixed((KeyError, TypeError), AdvisorQueueError, "advisor_queue_incomplete"):
        return "".join(x.get("plain_text", x.get("text", {}).get("content", ""))
                       for x in block[block["type"]]["rich_text"])


def _title(page):
    props = page.get("properties") or {}
    titles = [prop.get("title") for prop in props.values() if prop.get("type") == "title"]
    return "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in titles[0]) if len(titles) == 1 else ""


def _call(client, budget, method, path, body=None, once=False):
    _need(budget[0] > 0, "advisor_queue_incomplete")
    budget[0] -= 1
    with fixed(Exception, AdvisorQueueError, "advisor_queue_incomplete", keep=(AdvisorQueueError, NotionError) if once else AdvisorQueueError):
        return (client.call_once if once else client.call)(method, path, body)


def _page(client, budget, page_id, title, parent=None, unavailable="advisor_queue_incomplete"):
    page = _call(client, budget, "GET", f"/pages/{page_id}")
    _need(isinstance(page, dict) and not page.get("archived") and not page.get("in_trash"), unavailable)
    return page                                         # LIFE-383 (D250): no title or parent comparison; `title` and `parent` stay in the signature, unused; identity is the marker block


def _children(client, budget, page_id):
    def fetch(cursor):
        path = f"/blocks/{page_id}/children?page_size=100"
        return _call(client, budget, "GET", path + ("&start_cursor=" + quote(cursor, safe="") if cursor else ""))
    return [block for data in notion_pages.pages(fetch, AdvisorQueueError, "advisor_queue_incomplete") for block in data["results"]]


def _queue(client, budget, queue_page_id):
    _page(client, budget, queue_page_id, _QUEUE_TITLE)         # the queue page is held by ID (the store hands it over); its root is not derived from its parent (D250)
    first_data = _call(client, budget, "GET", f"/blocks/{queue_page_id}/children?page_size=1")
    _need(isinstance(first_data, dict) and isinstance(first_data.get("results"), list),
          "advisor_queue_incomplete")
    first = first_data["results"]
    _need(isinstance(first, list) and first and first[0].get("type") == "paragraph"
          and _plain(first[0]) == _QUEUE_MARKER, "advisor_queue_invalid")
    blocks = _children(client, budget, queue_page_id)
    _need(blocks and blocks[0].get("type") == "paragraph" and _plain(blocks[0]) == _QUEUE_MARKER,
          "advisor_queue_invalid")
    return blocks


def _marker_ids(client, budget, block_id, pattern):
    """The groups of a child page's first-block marker (`pattern`), else None: the page's own machine identity (D250), read as its first block only."""
    data = _call(client, budget, "GET", f"/blocks/{block_id}/children?page_size=1")
    first = data.get("results") if isinstance(data, dict) else None
    _need(isinstance(first, list), "advisor_queue_incomplete")
    match = pattern.fullmatch(_plain(first[0])) if first and first[0].get("type") == "paragraph" else None
    return match.groups() if match else None


def _payload_marker(bundle):
    payload = serialize_bundle(bundle)
    digest = advisor.bundle_digest(bundle)
    return payload, digest, digest[:32]


def _read_request(client, budget, page_id, expected_parent=None):
    page = _call(client, budget, "GET", f"/pages/{page_id}")
    _need(isinstance(page, dict) and not page.get("archived") and not page.get("in_trash"), "advisor_queue_invalid")
    blocks = _children(client, budget, page_id)
    _need(len(blocks) >= 2 and all(not b.get("has_children") for b in blocks[:2]), "advisor_queue_invalid")
    marker = _MARKER.fullmatch(_plain(blocks[0])) if blocks[0].get("type") == "paragraph" else None
    _need(marker is not None and blocks[1].get("type") == "paragraph", "advisor_queue_invalid")
    mid, bundle_hash = marker.groups()
    request_id = mid                                                 # LIFE-383 (D250): the request id is the marker's own id, never parsed from the title
    payload = _plain(blocks[1])
    _need(not _reserved(payload), "advisor_queue_invalid")
    bundle = parse_bundle(payload)
    _need(advisor.bundle_digest(bundle) == bundle_hash and bundle_hash[:32] == request_id, "advisor_queue_conflict")
    request = QueueRequest(request_id, bundle_hash, bundle, page_id)
    # A valid request body is immutable; child anomalies are classified by scan_queue as AMBIGUOUS.
    return request, blocks[2:]


def _reserved(text):
    normalized = text.strip().lower()
    return normalized.startswith(("v7-interview-advisor-request", "v7-interview-advisor-response"))


def _request_blocks(request):
    marker = f"v7-interview-advisor-request:1;{request.request_id};{request.bundle_hash}"
    return [_para(marker), _para(serialize_bundle(request.bundle))]


def _rich(text):
    return [{"type": "text", "text": {"content": text[i:i + 1800]}} for i in range(0, max(1, len(text)), 1800)]


def _para(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(text)}}


def ensure_request(client, queue_page_id, bundle):
    _need(callable(getattr(client, "call_once", None)))
    payload, bundle_hash, request_id = _payload_marker(bundle)
    _need(len(payload) <= _REQUEST_MAX, "advisor_queue_too_large")
    title = f"Advisor Request {request_id}"
    budget = [_CALLS]
    queue_blocks = _queue(client, budget, queue_page_id)
    candidates = []
    for block in queue_blocks[1:]:
        _need(block.get("type") == "child_page", "advisor_queue_invalid")
        pair = _marker_ids(client, budget, block.get("id"), _MARKER)         # LIFE-383 (D250): a request page is recognised by its own marker, not by its title
        _need(pair is not None, "advisor_queue_invalid")
        _need(pair[1][:32] == pair[0], "advisor_queue_conflict")             # a marker whose id is not its hash's prefix is tampered: fail closed
        if pair[0] == request_id:
            candidates.append(block.get("id"))
    if len(candidates) > 1:
        raise AdvisorQueueError("advisor_queue_conflict")
    if candidates:
        existing, trailing = _read_request(client, budget, candidates[0], queue_page_id)
        _need(len(trailing) <= 1 and all(b.get("type") == "child_page" for b in trailing), "advisor_queue_conflict")
        _need(existing.request_id == request_id and existing.bundle_hash == bundle_hash
              and serialize_bundle(existing.bundle) == payload, "advisor_queue_conflict")
        return existing
    request = QueueRequest(request_id, bundle_hash, bundle, "")
    try:
        page = _call(client, budget, "POST", "/pages", {"parent": {"type": "page_id", "page_id": queue_page_id},
            "properties": {"title": {"title": _rich(title)}}, "children": _request_blocks(request)}, once=True)
    except NotionError as error:
        status = re.fullmatch(r"NOTION_HTTP_(\d+)", str(error))
        raise AdvisorQueueError("advisor_queue_write_failed" if status and int(status.group(1)) in _DEFINITE
                                else "advisor_queue_readback_failed") from None
    except Exception:
        raise AdvisorQueueError("advisor_queue_readback_failed") from None
    with fixed(Exception, AdvisorQueueError, "advisor_queue_readback_failed"):
        page_id = page.get("id")
        _need(isinstance(page_id, str) and re.fullmatch(
              r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", page_id),
              "advisor_queue_readback_failed")
        result, trailing = _read_request(client, budget, page_id, queue_page_id)
        _need(not trailing and result.request_id == request_id and result.bundle_hash == bundle_hash
              and serialize_bundle(result.bundle) == payload, "advisor_queue_readback_failed")
        return result


def _draft_value(draft):
    return {key: ([{"text": item.text, "source_refs": list(item.source_refs)} for item in getattr(draft, key)]
                  if key != "strongest_evidence_refs" else list(draft.strongest_evidence_refs)) for key in _DRAFT_KEYS}


def _parse_draft(payload):
    with fixed(Exception, AdvisorQueueError, "advisor_queue_response_invalid", keep=AdvisorQueueError):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                _need(key not in result, "advisor_queue_response_invalid")
                result[key] = value
            return result
        value = json.loads(payload, object_pairs_hook=unique)
        _need(type(value) is dict and set(value) == set(_DRAFT_KEYS), "advisor_queue_response_invalid")
        sections = {}
        for key in _DRAFT_KEYS:
            if key == "strongest_evidence_refs":
                _need(isinstance(value[key], list) and all(isinstance(ref, str) for ref in value[key]), "advisor_queue_response_invalid")
                sections[key] = tuple(value[key])
            else:
                _need(isinstance(value[key], list), "advisor_queue_response_invalid")
                items = []
                for item in value[key]:
                    _need(type(item) is dict and set(item) == set(_ADVICE_KEYS)
                          and isinstance(item["text"], str) and isinstance(item["source_refs"], list)
                          and all(isinstance(ref, str) for ref in item["source_refs"]), "advisor_queue_response_invalid")
                    items.append(advisor.GroundedAdvice(item["text"], tuple(item["source_refs"])))
                sections[key] = tuple(items)
        return advisor.AdvisorDraft(**sections)


def read_response(client, request, response_page_id, budget=None):
    budget = budget if budget is not None else [_CALLS]
    with fixed(Exception, AdvisorQueueError, "advisor_queue_response_invalid", keep=AdvisorQueueError):
        _page(client, budget, response_page_id, None, None, "advisor_queue_response_invalid")
        blocks = _children(client, budget, response_page_id)
        _need(len(blocks) == 2 and all(not b.get("has_children") for b in blocks), "advisor_queue_response_invalid")
        marker = _RESPONSE_MARKER.fullmatch(_plain(blocks[0])) if blocks[0].get("type") == "paragraph" else None
        _need(marker is not None and blocks[1].get("type") == "paragraph", "advisor_queue_response_invalid")
        _need(marker.groups() == (request.request_id, request.bundle_hash), "advisor_queue_response_invalid")
        payload = _plain(blocks[1])
        _need(not _reserved(payload), "advisor_queue_response_invalid")
        draft = _parse_draft(payload)
        prep = advisor.compile_prep(request.bundle, draft)
        return QueueResponse(request.request_id, request.bundle_hash, draft, prep, response_page_id)


def _request_for_scan(client, budget, queue_page_id, block):
    request, extras = _read_request(client, budget, block.get("id"), queue_page_id)
    return request, extras


def scan_queue(client, queue_page_id):
    budget = [_CALLS]
    blocks = _queue(client, budget, queue_page_id)
    items = []
    seen_request_ids = set()
    for block in blocks[1:]:
        _need(block.get("type") == "child_page", "advisor_queue_invalid")
        request, extras = _request_for_scan(client, budget, queue_page_id, block)
        _need(request.request_id not in seen_request_ids, "advisor_queue_conflict")      # LIFE-383 (D250): the id comes from the request's own marker
        seen_request_ids.add(request.request_id)
        child_pages = [b for b in extras if b.get("type") == "child_page"]
        malformed = len(child_pages) != len(extras) or len(child_pages) > 1
        if not extras:
            items.append(QueueItem(request, None, QueueState.READY))
        elif malformed:
            items.append(QueueItem(request, None, QueueState.AMBIGUOUS))
        else:
            try:
                response = read_response(client, request, child_pages[0].get("id"), budget)
                items.append(QueueItem(request, response, QueueState.RESPONDED))
            except AdvisorQueueError as error:
                if error.code == "advisor_queue_incomplete":
                    raise
                items.append(QueueItem(request, None, QueueState.AMBIGUOUS))
    return tuple(items)


def with_verified_preview(item, preview):
    _need(isinstance(item, QueueItem) and item.state == QueueState.RESPONDED and item.response is not None,
          "advisor_queue_preview_mismatch")
    _need(isinstance(preview, VerifiedPreview) and preview.generation_id == item.request.request_id
          and preview.bundle_hash == item.request.bundle_hash and preview.prep == item.response.prep,
          "advisor_queue_preview_mismatch")
    return replace(item, state=QueueState.PREVIEWED)
