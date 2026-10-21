"""Fit profile sync: Jim's Notion page is the authority; this stage copies its approved evidence block into the one Bitwarden secret the Fit engine already merges
(FIT_PROFILE_EXTRA_JSON, D105), so the main pipeline keeps reading only Bitwarden.

Runs in its own isolated job (profile-sync.yml): it holds a Bitwarden write token and a Notion read token and nothing else. Nothing about the page content is ever printed:
the log carries one fixed code and counts.

The page (NOTION_FIT_SYNC_PAGE_ID) carries two things, written by Jim or by a prompt:
  * one JSON code block, the managed evidence block: {"capabilities": [...], "functions": [...]} in the shape of profile.py. Every managed capability id starts with
    "nsync_" and every managed function carries "owner": "nsync", so the sync owns exactly those entries and never touches the rest of the secret;
  * one paragraph "APPROVED HASH: <16 hex>": the content hash of the block Jim approved. A block whose hash differs is NOT adopted, so a stray edit never reaches scoring.

Adoption is: read page -> parse -> validate (strict keys, size, loads as a Profile on top of the current secret) -> approved? -> changed? -> write -> read back. Any failure keeps the
secret exactly as it was (never blank, never partial). A dry run (the default) does everything except the write."""
from hashlib import sha256
import json
import os
import re
import shutil
import subprocess

from lifeos.jobs.fit.profile import Profile, ProfileError
from lifeos.platform.notion_client import Client, NotionError

OWNER = "nsync"
ID_PREFIX = "nsync_"
ALLOWED_KEYS = ("capabilities", "functions")
MAX_BYTES = 24_000
MAX_ENTRIES = 80
_HASH = re.compile(r"APPROVED HASH:\s*([0-9a-f]{16})\b", re.I)
_SECRET_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class SyncError(RuntimeError):
    """The message is always a fixed code."""


def content_hash(block):
    return sha256(json.dumps(block, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def read_page(client, page_id):
    """-> (json text of the first JSON code block, the approved hash or None). Counts only on failure."""
    try:
        children = client.call("GET", f"/blocks/{Client._page_id(page_id)}/children?page_size=100")
    except NotionError as error:
        raise SyncError("SYNC_PAGE_UNREADABLE") from error
    if children.get("has_more"):
        raise SyncError("SYNC_PAGE_TOO_LARGE")
    text, approved = None, None
    for block in children.get("results") or []:
        kind = block.get("type")
        payload = block.get(kind) or {}
        plain = "".join(piece.get("plain_text", "") for piece in payload.get("rich_text") or [])
        if kind == "code" and payload.get("language") == "json" and text is None:
            text = plain
        elif kind == "paragraph":
            found = _HASH.search(plain)
            if found:
                approved = found.group(1).lower()
    if text is None:
        raise SyncError("SYNC_NO_BLOCK")
    return text, approved


def parse(text):
    """The managed block, validated by shape only (the secret it lands in is checked separately). Raises SyncError with a fixed code."""
    if len(text.encode()) > MAX_BYTES:
        raise SyncError("SYNC_BLOCK_TOO_LARGE")
    try:
        block = json.loads(text)
    except ValueError:
        raise SyncError("SYNC_BLOCK_NOT_JSON") from None
    if not isinstance(block, dict) or set(block) - set(ALLOWED_KEYS):
        raise SyncError("SYNC_BLOCK_KEYS")
    entries = [*(block.get("capabilities") or []), *(block.get("functions") or [])]
    if not entries or len(entries) > MAX_ENTRIES or not all(isinstance(row, dict) for row in entries):
        raise SyncError("SYNC_BLOCK_SIZE")
    for row in block.get("capabilities") or []:
        if not str(row.get("id") or "").startswith(ID_PREFIX):
            raise SyncError("SYNC_CAPABILITY_ID")
    block = {"capabilities": list(block.get("capabilities") or []),
             "functions": [{**row, "owner": OWNER} for row in block.get("functions") or []]}
    try:
        Profile({"years": 1, **block})
    except (ValueError, TypeError, KeyError, ProfileError):
        raise SyncError("SYNC_BLOCK_INVALID") from None
    return block


def merge(current, block):
    """The secret's current extra block with the sync-owned entries replaced by `block`; every entry the sync does not own is kept exactly."""
    kept = dict(current)
    kept["capabilities"] = [row for row in current.get("capabilities") or [] if not str(row.get("id") or "").startswith(ID_PREFIX)] + block["capabilities"]
    kept["functions"] = [row for row in current.get("functions") or [] if row.get("owner") != OWNER] + block["functions"]
    return kept


def owned(extra):
    return {"capabilities": [r for r in extra.get("capabilities") or [] if str(r.get("id") or "").startswith(ID_PREFIX)],
            "functions": [r for r in extra.get("functions") or [] if r.get("owner") == OWNER]}


class Bitwarden:
    """The one secret, through the pinned bws CLI (installed by the workflow). The value never reaches a log."""

    def __init__(self, secret_id, environ=os.environ, run=subprocess.run):
        if not _SECRET_ID.match(secret_id or ""):
            raise SyncError("SYNC_SECRET_ID_INVALID")
        if not shutil.which("bws") and run is subprocess.run:
            raise SyncError("SYNC_BWS_MISSING")
        self.id, self.run, self.env = secret_id, run, {**environ}

    def _bws(self, *args):
        done = self.run(["bws", *args, "--output", "json"], capture_output=True, text=True, env=self.env, timeout=60)
        if done.returncode != 0:
            raise SyncError("SYNC_BWS_FAILED")
        try:
            return json.loads(done.stdout)
        except ValueError:
            raise SyncError("SYNC_BWS_FAILED") from None

    def read(self):
        value = (self._bws("secret", "get", self.id).get("value") or "").strip()
        try:
            data = json.loads(value) if value else {}
        except ValueError:
            raise SyncError("SYNC_SECRET_NOT_JSON") from None
        if not isinstance(data, dict):
            raise SyncError("SYNC_SECRET_NOT_JSON")
        return data

    def write(self, data):
        self._bws("secret", "edit", self.id, "--value", json.dumps(data, sort_keys=True, separators=(",", ":")))


def run(limit, live, environ=os.environ, notion=None, vault=None):
    """-> counts-only result with one fixed `code`. Everything except the write happens in a dry run."""
    out = {"code": "SYNC_DRY_RUN" if not live else "SYNC_UNCHANGED", "live": bool(live)}
    try:
        page = (environ.get("NOTION_FIT_SYNC_PAGE_ID") or "").strip()
        if not page:
            raise SyncError("SYNC_PAGE_UNCONFIGURED")
        notion = notion or Client(environ, token_name="NOTION_FIT_SYNC_TOKEN", source_name="NOTION_FIT_SYNC_PAGE_ID")
        text, approved = read_page(notion, page)
        block = parse(text)
        digest = content_hash(block)
        out["entries"] = len(block["capabilities"]) + len(block["functions"])
        vault = vault or Bitwarden((environ.get("FIT_PROFILE_EXTRA_SECRET_ID") or "").strip(), environ)
        current = vault.read()
        merged = merge(current, block)
        try:
            Profile({"years": 1, **merged})                                    # the whole secret must still load
        except (ValueError, TypeError, KeyError, ProfileError):
            raise SyncError("SYNC_MERGED_INVALID") from None
        if approved != digest:
            out["code"] = "SYNC_NOT_APPROVED"
            return out
        if owned(current) == block:
            out["code"] = "SYNC_UNCHANGED"
            return out
        if not live:
            out["code"] = "SYNC_WOULD_WRITE"
            return out
        vault.write(merged)
        if owned(vault.read()) != block:
            raise SyncError("SYNC_READBACK_MISMATCH")
        out["code"] = "SYNC_WRITTEN"
    except SyncError as error:
        out["code"] = str(error)
    return out
