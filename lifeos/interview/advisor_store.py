"""Bounded private Notion store and immutable Advisor preview pages."""
from dataclasses import dataclass, field
import hashlib
import json
import os
import re

from . import advisor, notion
from .models import PrepEvidence
from lifeos.platform.notion_client import Client

_ROOT = "LIFE OS — Interview Advisor"
_NAMES = ("Corpus Manifest", "Straight Line Doctrine", "Game Theory Doctrine",
          "Candidate Evidence Bank", "Candidate Profile", "Advisor Inputs", "Advisor Queue", "Advisor Previews")
_MARKERS = dict(zip(_NAMES, ("manifest", "straight_line_doctrine", "game_theory_doctrine",
                              "evidence_bank", "candidate_profile", "inputs", "queue", "previews")))
_CHILDREN = {"Advisor Inputs": ("Guidance", "Accepted Signals")}
_RX = re.compile(r"^[0-9a-f]{32}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CODE = "advisor_store_invalid"


class AdvisorStoreError(ValueError):
    def __init__(self, code=_CODE):
        self.code = code if code in {"advisor_store_invalid", "advisor_store_incomplete", "advisor_store_hash_mismatch",
                                    "advisor_store_unreadable", "advisor_preview_invalid", "advisor_preview_conflict",
                                    "advisor_preview_readback_failed"} else _CODE
        super().__init__(self.code)

    def __str__(self):
        return self.code


def _need(ok, code=_CODE):
    if not ok:
        raise AdvisorStoreError(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json(text, keys, code=_CODE):
    try:
        value = json.loads(text)
        _need(type(value) is dict and set(value) == set(keys), code)
        _need(_canonical(value) == text, code)
        return value
    except (ValueError, TypeError):
        raise AdvisorStoreError(code) from None


def _plain(block):
    try:
        parts = block[block["type"]]["rich_text"]
        return "".join(part.get("plain_text", part.get("text", {}).get("content", "")) for part in parts)
    except (KeyError, TypeError):
        raise AdvisorStoreError("advisor_store_unreadable") from None


def _title(page):
    props = page.get("properties") or {}
    found = [prop.get("title") for prop in props.values() if isinstance(prop, dict) and prop.get("type") == "title"]
    return "".join(p.get("plain_text", p.get("text", {}).get("content", "")) for p in found[0]) if len(found) == 1 else ""


def _page(client, page_id, expected_title=None, parent=None, code=_CODE):
    try:
        page = client.call("GET", f"/pages/{page_id}")
        _need(isinstance(page, dict) and not page.get("archived") and not page.get("in_trash"), code)
        if expected_title is not None:
            _need(_title(page) == expected_title, code)
        if parent is not None:
            _need(notion.same_notion_id((page.get("parent") or {}).get("page_id"), parent), code)
        return page
    except AdvisorStoreError:
        raise
    except Exception:
        raise AdvisorStoreError("advisor_store_unreadable") from None


def _blocks(client, page_id):
    try:
        return notion.children(client, page_id, [40])
    except Exception:
        raise AdvisorStoreError("advisor_store_unreadable") from None


def _marked(client, page_id, kind):
    blocks = _blocks(client, page_id)
    marker = f"v7-interview-advisor:1;kind={kind}"
    _need(bool(blocks) and blocks[0].get("type") == "paragraph" and _plain(blocks[0]) == marker,
          "advisor_store_incomplete")
    if any(b.get("type") == "paragraph" and _plain(b).startswith("v7-interview-advisor:1;kind=") for b in blocks[1:]):
        raise AdvisorStoreError("advisor_store_incomplete")
    return blocks


def _first_marker(client, page_id, kind):
    """Read only the marker block; Queue contents belong to the later queue contract."""
    try:
        data = client.call("GET", f"/blocks/{page_id}/children?page_size=1")
        blocks = data["results"]
        expected = f"v7-interview-advisor:1;kind={kind}"
        _need(isinstance(blocks, list) and blocks and blocks[0].get("type") == "paragraph"
              and _plain(blocks[0]) == expected, "advisor_store_incomplete")
    except AdvisorStoreError:
        raise
    except Exception:
        raise AdvisorStoreError("advisor_store_unreadable") from None


def _direct_pages(client, parent_id, allowed):
    blocks = _blocks(client, parent_id)
    pages = [b for b in blocks if b.get("type") == "child_page"]
    titles = [(b.get("child_page") or {}).get("title") for b in pages]
    _need(all(isinstance(t, str) and t in allowed for t in titles), "advisor_store_incomplete")
    _need(set(titles) == set(allowed) and len(titles) == len(set(titles)) == len(allowed),
          "advisor_store_incomplete")
    result = {}
    for block, title in zip(pages, titles):
        result[title] = _page(client, block.get("id"), title, parent_id)
    return {title: result[title]["id"] for title in allowed}


def _body_text(block):
    kind = block.get("type")
    _need(kind in ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item"),
          "advisor_store_invalid")
    _need(not block.get("has_children"), "advisor_store_invalid")
    prefix = {"paragraph": "", "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
              "bulleted_list_item": "- ", "numbered_list_item": "1. "}[kind]
    return prefix + _plain(block)


def _metadata(block, prefix, keys):
    _need(block.get("type") == "paragraph", "advisor_store_invalid")
    value = _plain(block)
    _need(value.startswith(prefix), "advisor_store_invalid")
    return _json(value[len(prefix):], keys)


def _source(client, page_id, kind, expected_marker):
    blocks = _marked(client, page_id, expected_marker)
    _need(len(blocks) >= 3, "advisor_store_incomplete")
    meta = _metadata(blocks[1], "v7-interview-advisor-source:1;", ("source_id", "version"))
    _need(not any(b.get("type") == "paragraph" and _plain(b).startswith("v7-interview-advisor-source:")
                  for b in blocks[2:]), "advisor_store_invalid")
    _need(isinstance(meta["source_id"], str) and type(meta["version"]) is int and meta["version"] > 0,
          "advisor_store_invalid")
    text = "\n".join(_body_text(b) for b in blocks[2:])
    _need(bool(text), "advisor_store_incomplete")
    try:
        return advisor.AdvisorSource(meta["source_id"], kind, text, meta["version"])
    except ValueError:
        raise AdvisorStoreError("advisor_store_invalid") from None


def _bank(client, page_id):
    blocks = _marked(client, page_id, "evidence_bank")
    _need(len(blocks) >= 3, "advisor_store_incomplete")
    meta = _metadata(blocks[1], "v7-interview-advisor-bank:1;", ("version",))
    _need(type(meta["version"]) is int and meta["version"] > 0, "advisor_store_invalid")
    keys = ("evidence_id", "version", "canonical_text", "source_ref", "tags", "active")
    records = []
    try:
        for block in blocks[2:]:
            _need(block.get("type") == "paragraph" and not block.get("has_children"), "advisor_store_invalid")
            data = _json(_plain(block), keys)
            _need(isinstance(data["tags"], list), "advisor_store_invalid")
            records.append(advisor.CandidateEvidence(advisor.EvidenceRef(data["evidence_id"], data["version"]),
                data["canonical_text"], data["source_ref"], tuple(data["tags"]), data["active"]))
    except ValueError:
        raise AdvisorStoreError("advisor_store_invalid") from None
    _need(records and len({r.ref for r in records}) == len(records), "advisor_store_invalid")
    norm = [[r.ref.canonical(), r.canonical_text, r.source_ref, list(r.tags), r.active] for r in records]
    return meta["version"], tuple(records), hashlib.sha256(_canonical(norm).encode()).hexdigest()


def _records(client, page_id, marker_kind, signal=False):
    blocks = _marked(client, page_id, marker_kind)
    keys = ("source_id", "version", "source_round", "text", "active") if signal else ("source_id", "version", "text", "active")
    out = []
    try:
        for block in blocks[1:]:
            _need(block.get("type") == "paragraph" and not block.get("has_children"), "advisor_store_invalid")
            data = _json(_plain(block), keys)
            out.append(advisor.AdvisorSource(data["source_id"], advisor.SourceKind.ACCEPTED_SIGNAL if signal else advisor.SourceKind.GUIDANCE,
                                             data["text"], data["version"], data.get("source_round"), data["active"]))
    except ValueError:
        raise AdvisorStoreError("advisor_store_invalid") from None
    _need(len({(x.source_id, x.version) for x in out}) == len(out), "advisor_store_invalid")
    active = tuple(x for x in out if x.active)
    _need(len(active) <= (100 if signal else 3), "advisor_store_invalid")
    if signal:
        counts = {}
        for item in active:
            counts[item.source_round] = counts.get(item.source_round, 0) + 1
        _need(all(n <= 5 for n in counts.values()), "advisor_store_invalid")
    return active


@dataclass(frozen=True)
class AdvisorStoreSnapshot:
    manifest: advisor.AdvisorManifest = field(repr=False)
    straight_line: advisor.AdvisorSource = field(repr=False)
    game_theory: advisor.AdvisorSource = field(repr=False)
    candidate_profile: advisor.AdvisorSource = field(repr=False)
    evidence: tuple[advisor.CandidateEvidence, ...] = field(repr=False)
    guidance: tuple[advisor.AdvisorSource, ...] = field(repr=False)
    accepted_signals: tuple[advisor.AdvisorSource, ...] = field(repr=False)
    queue_page_id: str = field(repr=False)
    previews_page_id: str = field(repr=False)


def read_store(client, root_page_id=None, environ=os.environ):
    """Read only the explicitly configured root and its exact required descendants."""
    root_page_id = (root_page_id or environ.get("INTERVIEW_ADVISOR_ROOT_PAGE_ID") or "").strip()
    _need(isinstance(root_page_id, str) and bool(root_page_id.strip()), "advisor_store_invalid")
    try:
        _page(client, root_page_id, _ROOT)
        pages = _direct_pages(client, root_page_id, _NAMES)
        for name, page_id in pages.items():
            if name == "Advisor Queue":
                _first_marker(client, page_id, "queue")
                continue
            _marked(client, page_id, _MARKERS[name])
        sub = _direct_pages(client, pages["Advisor Inputs"], _CHILDREN["Advisor Inputs"])
        for name, page_id in sub.items():
            _marked(client, page_id, "guidance" if name == "Guidance" else "accepted_signals")
        sl = _source(client, pages["Straight Line Doctrine"], advisor.SourceKind.STRAIGHT_LINE_DOCTRINE, "straight_line_doctrine")
        gt = _source(client, pages["Game Theory Doctrine"], advisor.SourceKind.GAME_THEORY_DOCTRINE, "game_theory_doctrine")
        profile = _source(client, pages["Candidate Profile"], advisor.SourceKind.CANDIDATE_PROFILE, "candidate_profile")
        manifest_blocks = _marked(client, pages["Corpus Manifest"], "manifest")
        keys = tuple(advisor.AdvisorManifest.__dataclass_fields__)
        _need(len(manifest_blocks) == 2, "advisor_store_incomplete")
        m = _metadata(manifest_blocks[1], "", keys)
        manifest = advisor.AdvisorManifest(**m)
        bank_version, bank, bank_hash = _bank(client, pages["Candidate Evidence Bank"])
        for version_key, expected in (("straight_line_version", str(sl.version)), ("game_theory_version", str(gt.version)),
                                       ("candidate_profile_version", str(profile.version)), ("evidence_bank_version", str(bank_version))):
            _need(getattr(manifest, version_key) == expected, "advisor_store_hash_mismatch")
        for hash_key, text in (("straight_line_hash", sl.text), ("game_theory_hash", gt.text), ("candidate_profile_hash", profile.text)):
            _need(getattr(manifest, hash_key) == hashlib.sha256(text.encode()).hexdigest(), "advisor_store_hash_mismatch")
        _need(manifest.evidence_bank_hash == bank_hash, "advisor_store_hash_mismatch")
        guidance = _records(client, sub["Guidance"], "guidance")
        signals = _records(client, sub["Accepted Signals"], "accepted_signals", True)
        evidence = tuple(item for item in bank if item.active)
        return AdvisorStoreSnapshot(manifest, sl, gt, profile, evidence, guidance, signals,
                                    pages["Advisor Queue"], pages["Advisor Previews"])
    except AdvisorStoreError:
        raise
    except Exception:
        raise AdvisorStoreError("advisor_store_unreadable") from None


def make_client(environ=os.environ):
    token = (environ.get("NOTION_INTERVIEW_TOKEN") or "").strip()
    _need(bool(token), "advisor_store_invalid")
    return Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "interview-advisor-unused"})


def _rich(content):
    _need(len(content) <= 180000, "advisor_preview_invalid")
    return [{"type": "text", "text": {"content": content[i:i + 1800]}} for i in range(0, max(1, len(content)), 1800)]


def _para(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich(text)}}


def _heading(text):
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rich(text)}}


_FINDINGS = (("Straight Line", "straight_line"), ("Game Theory", "game_theory"),
             ("Decision Criteria", "decision_criteria"), ("Objections", "objections"), ("Close Strategy", "close_strategy"))
_PREP = (("Focus", "focus"), ("Strongest Evidence", "strongest_evidence"),
         ("Pressure Points", "pressure_points"), ("Questions", "questions"))


def _human_lines(draft, prep):
    lines = []
    for title, field in _FINDINGS:
        lines.append("## " + title)
        for item in getattr(draft, field):
            lines.extend(("- " + item.text, "refs: " + ",".join(item.source_refs)))
    lines.append("## Compiled PrepEvidence")
    for title, field in _PREP:
        lines.append("### " + title)
        lines.extend("- " + value for value in getattr(prep, field))
    return tuple(lines)


def render_preview(generation_id, bundle, draft, prep=None):
    _need(isinstance(generation_id, str) and _RX.fullmatch(generation_id), "advisor_preview_invalid")
    compiled = advisor.compile_prep(bundle, draft)
    digest = advisor.bundle_digest(bundle)
    _need(prep is None or prep == compiled, "advisor_preview_invalid")
    lines = _human_lines(draft, compiled)
    body = "\n".join(lines)
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    marker = f"v7-interview-advisor-preview:1;{generation_id};{digest};{body_hash}"
    blocks = [_para(marker)]
    for line in lines:
        if line.startswith("## "):
            blocks.append(_heading(line[3:]))
        elif line.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": _rich(line[4:])}})
        elif line.startswith("- "):
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": _rich(line[2:])}})
        else:
            blocks.append(_para(line))
    _need(len(blocks) <= 100, "advisor_preview_invalid")
    return f"Advisor Preview {generation_id}", digest, body_hash, body, blocks, compiled


@dataclass(frozen=True)
class VerifiedPreview:
    generation_id: str
    bundle_hash: str
    body_hash: str
    prep: PrepEvidence = field(repr=False)


def _parse_preview_body(blocks):
    lines, prep = [], {key: [] for _, key in _PREP}
    titles, subheads, current, mode = [], [], None, "private"
    current_items, pending_refs = 0, False
    for block in blocks:
        _need(not block.get("has_children"), "advisor_preview_invalid")
        kind = block.get("type")
        text = _plain(block)
        if kind in ("heading_2", "heading_3"):
            _need(not pending_refs, "advisor_preview_invalid")
            line = ("## " if kind == "heading_2" else "### ") + text
            lines.append(line)
            if kind == "heading_2":
                if current in ("Straight Line", "Game Theory"):
                    _need(current_items > 0 and not pending_refs, "advisor_preview_invalid")
                titles.append(text)
                current = text
                mode = "prep" if text == "Compiled PrepEvidence" else "private"
                current_items = 0
            elif mode == "prep":
                subheads.append(text)
                current = text
                current_items = 0
            else:
                raise AdvisorStoreError("advisor_preview_invalid")
        elif kind in ("paragraph", "bulleted_list_item"):
            line = "- " + text if kind == "bulleted_list_item" else text
            lines.append(line)
            if mode == "private":
                _need(current in [x[0] for x in _FINDINGS], "advisor_preview_invalid")
                if kind == "bulleted_list_item":
                    _need(not pending_refs and bool(text), "advisor_preview_invalid")
                    current_items += 1
                    pending_refs = True
                else:
                    refs = text[6:].split(",") if text.startswith("refs: ") else []
                    _need(pending_refs and bool(refs) and all(re.fullmatch(r"[A-Za-z0-9._-]+@[1-9][0-9]*", ref) for ref in refs),
                          "advisor_preview_invalid")
                    pending_refs = False
            elif kind == "bulleted_list_item":
                key = dict((title, field) for title, field in _PREP).get(current)
                _need(key is not None and bool(text), "advisor_preview_invalid")
                prep[key].append(text)
            else:
                raise AdvisorStoreError("advisor_preview_invalid")
        else:
            raise AdvisorStoreError("advisor_preview_invalid")
    expected = [x[0] for x in _FINDINGS] + ["Compiled PrepEvidence"]
    _need(titles == expected, "advisor_preview_invalid")
    _need(not pending_refs, "advisor_preview_invalid")
    _need(subheads == [x[0] for x in _PREP], "advisor_preview_invalid")
    _need(current == "Questions", "advisor_preview_invalid")
    try:
        result = PrepEvidence(**{key: tuple(value) for key, value in prep.items()})
        from .prep import normalize
        normalize(result)
    except Exception:
        raise AdvisorStoreError("advisor_preview_invalid") from None
    return "\n".join(lines), result


def read_preview(client, preview_page_id, expected_previews_parent_id, expected_generation_id=None,
                 expected_bundle_hash=None):
    try:
        _need(isinstance(preview_page_id, str) and preview_page_id and
              isinstance(expected_previews_parent_id, str) and expected_previews_parent_id, "advisor_preview_invalid")
        page = _page(client, preview_page_id, parent=expected_previews_parent_id, code="advisor_preview_invalid")
        blocks = _blocks(client, preview_page_id)
        _need(len(blocks) >= 2 and blocks[0].get("type") == "paragraph", "advisor_preview_invalid")
        _need(not blocks[0].get("has_children"), "advisor_preview_invalid")
        _need(not any(_plain(b).startswith("v7-interview-advisor-preview") for b in blocks[1:]), "advisor_preview_invalid")
        match = re.fullmatch(r"v7-interview-advisor-preview:1;([0-9a-f]{32});([0-9a-f]{64});([0-9a-f]{64})", _plain(blocks[0]))
        _need(match is not None, "advisor_preview_invalid")
        gen, bundle_hash, body_hash = match.groups()
        _need(_title(page) == "Advisor Preview " + gen, "advisor_preview_invalid")
        _need(expected_generation_id is None or gen == expected_generation_id, "advisor_preview_invalid")
        _need(expected_bundle_hash is None or bundle_hash == expected_bundle_hash, "advisor_preview_invalid")
        body, prep = _parse_preview_body(blocks[1:])
        _need(hashlib.sha256(body.encode()).hexdigest() == body_hash, "advisor_preview_invalid")
        return VerifiedPreview(gen, bundle_hash, body_hash, prep)
    except AdvisorStoreError:
        raise
    except Exception:
        raise AdvisorStoreError("advisor_preview_invalid") from None


def create_preview(client, previews_page_id, generation_id, bundle, draft):
    title, digest, body_hash, body, blocks, prep = render_preview(generation_id, bundle, draft)
    _page(client, previews_page_id, "Advisor Previews")
    _marked(client, previews_page_id, "previews")
    existing = _blocks(client, previews_page_id)
    matches = [b for b in existing if b.get("type") == "child_page" and (b.get("child_page") or {}).get("title") == title]
    _need(not matches, "advisor_preview_conflict")
    # Once POST begins, never retry: a transport error may follow a committed insert.
    try:
        post = getattr(client, "call_once", client.call)
        page = post("POST", "/pages", {"parent": {"type": "page_id", "page_id": previews_page_id},
            "properties": {"title": {"title": _rich(title)}}, "children": blocks})
    except Exception:
        raise AdvisorStoreError("advisor_preview_readback_failed") from None
    try:
        page_id = page.get("id")
        _need(isinstance(page_id, str) and re.fullmatch(
              r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", page_id),
              "advisor_preview_readback_failed")
        verified = read_preview(client, page_id, previews_page_id, generation_id, digest)
        _need(verified.body_hash == body_hash and verified.prep == prep, "advisor_preview_readback_failed")
        return verified
    except Exception:
        raise AdvisorStoreError("advisor_preview_readback_failed") from None
