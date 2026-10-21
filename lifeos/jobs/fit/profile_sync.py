"""Fit profile sync: Jim's Notion page is the authority; this stage copies its approved evidence block into one small table of the private store (v7_fit_profile_sync), and the Fit
profile loader merges that block in (profile.load(..., synced=stored_block())). Bitwarden stays read-only for everyone and nothing here writes a secret.

Runs in its own isolated job (profile-sync.yml): it holds the store credentials and one Notion read token for the one page, and nothing else. Nothing about the page content is ever printed:
the log carries one fixed code and counts.

The page (NOTION_FIT_SYNC_PAGE_ID) carries two things, written by Jim or by a prompt:
  * one JSON code block, the managed evidence block: {"capabilities": [...], "functions": [...]} in the shape of profile.py. Every capability id starts with "nsync_" and every
    function carries "owner": "nsync", so the block can never replace an entry of the secret profile;
  * one paragraph that is exactly "APPROVED HASH: <16 hex>": the content hash of the block Jim approved. A block whose hash differs is NOT adopted, so a stray edit never reaches scoring.

Adoption is: read page -> parse -> validate (strict keys, size, loads as a Profile) -> approved? -> changed? -> write the row -> read it back. Any failure keeps the stored row exactly as it was.
The reader returns the block only when the stored hash matches the block's own recomputed hash; a store that cannot be read raises ProfileError (the Fit stage then does nothing that run,
never scoring against a profile without its block). A dry run (the default) does everything except the write."""
from hashlib import sha256
import json
import os
import re

from lifeos.jobs import store
from lifeos.jobs.fit.profile import Profile, ProfileError
from lifeos.platform.notion_client import Client, NotionError

OWNER = "nsync"
ID_PREFIX = "nsync_"
ALLOWED_KEYS = ("capabilities", "functions")
MAX_BYTES = 24_000
MAX_ENTRIES = 80
_HASH = re.compile(r"^\s*APPROVED HASH:\s*([0-9a-f]{16})\s*$", re.I)       # the WHOLE paragraph: prose that quotes the line is never an approval
ROW_ID = 1


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
            found = _HASH.match(plain)
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


class Store:
    """The one row. The connection is the pipeline's own store connection (an SSH tunnel to the private database)."""

    def read(self):
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT content_hash, block FROM v7_fit_profile_sync WHERE id = %s", (ROW_ID,))
            row = cursor.fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def write(self, block):
        digest = content_hash(block)
        with store.connect() as connection:
            store.ensure_schema(connection)
            with connection.cursor() as cursor:
                cursor.execute("REPLACE INTO v7_fit_profile_sync (id, content_hash, block, synced_at) VALUES (%s, %s, %s, UTC_TIMESTAMP())",
                               (ROW_ID, digest, json.dumps(block, sort_keys=True, separators=(",", ":"))))
            connection.commit()


def stored_block(environ=os.environ, vault=None):
    """-> the approved block the sync stored, or None when nothing has been synced. Raises ProfileError when the store cannot be read or the row does not match its own hash:
    the caller then treats the profile as unavailable for this run (it never scores against a profile that silently lost its block)."""
    if vault is None and not (environ.get("LIFEOS_ACQ_DB_NAME") or "").strip():
        return None                                                       # no store configured (a unit test, a local run): nothing was ever synced here, and the stage could not write anyway
    try:
        digest, block = (vault or Store()).read()
        if block is None:
            return None
        if content_hash(block) != digest:
            raise ProfileError("synced block does not match its hash")
        Profile({"years": 1, **block})
        return block
    except ProfileError:
        raise
    except Exception as error:                                            # a store or JSON failure: fixed message, nothing about the data
        raise ProfileError("synced block unavailable") from error


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
        vault = vault or Store()
        try:
            stored_digest, _ = vault.read()
        except Exception:
            raise SyncError("SYNC_STORE_UNAVAILABLE") from None
        if approved != digest:
            out["code"] = "SYNC_NOT_APPROVED"
            return out
        if stored_digest == digest:
            out["code"] = "SYNC_UNCHANGED"
            return out
        if not live:
            out["code"] = "SYNC_WOULD_WRITE"
            return out
        try:
            vault.write(block)
            readback, saved = vault.read()
        except Exception:
            raise SyncError("SYNC_STORE_UNAVAILABLE") from None
        if readback != digest or saved != block:
            raise SyncError("SYNC_READBACK_MISMATCH")
        out["code"] = "SYNC_WRITTEN"
    except SyncError as error:
        out["code"] = str(error)
    return out
