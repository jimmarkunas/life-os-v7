"""Fit profile sync: copy the approved evidence block of the Notion page into the private store.

The table is v7_fit_profile_sync, which profile.load merges in. The Notion page is the authority.

Input: one JSON code block ({"capabilities": [...], "functions": [...]}, ids "nsync_*", every function "owner":
"nsync") and one paragraph "APPROVED HASH: <16 hex>", the content hash of the approved block; a block whose hash
differs is NOT adopted. Adoption: read page -> parse -> validate -> approved? -> changed? -> write the row -> read
it back. Any failure keeps the stored row; a dry run (the default) skips the write. Output: one fixed code and
counts, never page content. The reader returns the block only when the stored hash matches its recomputed hash; an
unreadable store raises ProfileError and the Fit stage then does nothing.
"""
from hashlib import sha256
import json
import os
import re

from lifeos.jobs import store
from lifeos.jobs.fit.profile import Profile, ProfileError
from lifeos.platform import notion_text
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import Client, NotionError

OWNER = "nsync"
ID_PREFIX = "nsync_"
ALLOWED_KEYS = ("capabilities", "functions")
MAX_BYTES = 24_000
MAX_ENTRIES = 80
_HASH = re.compile(r"^\s*APPROVED HASH:\s*([0-9a-f]{16})\s*$", re.I)
ROW_ID = 1


class SyncError(RuntimeError):
    """The message is always a fixed code."""


def content_hash(block):
    return sha256(json.dumps(block, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def read_page(client, page_id):
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
        plain = notion_text.plain(payload.get("rich_text"))
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
    """The managed block, validated by shape only (the secret it lands in is checked separately). Raises SyncError
    with a fixed code.
    """
    if len(text.encode()) > MAX_BYTES:
        raise SyncError("SYNC_BLOCK_TOO_LARGE")
    with fixed(ValueError, SyncError, "SYNC_BLOCK_NOT_JSON"):
        block = json.loads(text)
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
    with fixed((ValueError, TypeError, KeyError, ProfileError), SyncError, "SYNC_BLOCK_INVALID"):
        Profile({"years": 1, **block})
    return block


class Store:

    def read(self):
        with store.connect() as connection, connection.cursor() as cursor:
            try:
                cursor.execute("SELECT content_hash, block FROM v7_fit_profile_sync WHERE id = %s", (ROW_ID,))
            except Exception as error:
                if error.args and error.args[0] == 1146:
                    return (None, None)
                raise
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
    """-> the approved block the sync stored, or None when nothing has been synced. Raises ProfileError when the
    store cannot be read or the row fails its own hash: the profile is then unavailable, never scored without its
    block.
    """
    if vault is None and not (environ.get("LIFEOS_ACQ_DB_NAME") or "").strip():
        return None
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
    except Exception as error:
        raise ProfileError("synced block unavailable") from error


def run(limit, live, environ=os.environ, notion=None, vault=None):
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
        with fixed(Exception, SyncError, "SYNC_STORE_UNAVAILABLE"):
            stored_digest, _ = vault.read()
        if approved != digest:
            out["code"] = "SYNC_NOT_APPROVED"
            return out
        if stored_digest == digest:
            out["code"] = "SYNC_UNCHANGED"
            return out
        if not live:
            out["code"] = "SYNC_WOULD_WRITE"
            return out
        with fixed(Exception, SyncError, "SYNC_STORE_UNAVAILABLE"):
            vault.write(block)
            readback, saved = vault.read()
        if readback != digest or saved != block:
            raise SyncError("SYNC_READBACK_MISMATCH")
        out["code"] = "SYNC_WRITTEN"
    except SyncError as error:
        out["code"] = str(error)
    return out
