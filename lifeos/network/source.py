"""Find and fetch the connections file attached to a private Notion page. Read-only. Nothing is written to Notion, and the file is held in
memory only: never saved, logged, uploaded or committed.

Notion serves an attached file from a short-lived signed address (about an hour). The address is read from the page's blocks on each run,
used once, and never stored or printed; only Notion-hosted files are accepted (an external link on the page is refused)."""
import re
import urllib.error
import urllib.parse
import urllib.request

from lifeos.network.errors import NetworkError

MAX_FILE_BYTES = 20 * 1024 * 1024           # a 5,000-row export is well under 1 MB; this only stops a wrong file
MAX_CHILD_PAGES = 20
HOST_SUFFIX = ".amazonaws.com"              # Notion's file storage
PAGE_ID = re.compile(r"[0-9a-fA-F]{32}")


def clean_page_id(value):
    plain = (value or "").strip().replace("-", "")
    if not PAGE_ID.fullmatch(plain):
        raise NetworkError("NETWORK_CONFIG_MISSING")
    return plain


def csv_file_blocks(client, page_id):
    """Every CSV file block on the page, with its name, time and address. Incomplete listings raise rather than guess."""
    path = f"/blocks/{clean_page_id(page_id)}/children?page_size=100"
    out, cursor = [], None
    for _ in range(MAX_CHILD_PAGES):
        reply = client.call("GET", path + (f"&start_cursor={urllib.parse.quote(cursor, safe='')}" if cursor else ""))
        if not isinstance(reply, dict) or not isinstance(reply.get("results"), list) or not isinstance(reply.get("has_more"), bool):
            raise NetworkError("NETWORK_PAGE_INCOMPLETE")
        for block in reply["results"]:
            if not isinstance(block, dict) or block.get("type") != "file":
                continue
            attached = block.get("file") or {}
            if attached.get("type") != "file":
                continue                                          # an external link is never fetched
            url = (attached.get("file") or {}).get("url") or ""
            name = attached.get("name") or urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
            if name.lower().endswith(".csv") and url:
                out.append({"name": name, "url": url, "created": block.get("created_time") or ""})
        if not reply["has_more"]:
            return out
        cursor = reply.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise NetworkError("NETWORK_PAGE_INCOMPLETE")
    raise NetworkError("NETWORK_PAGE_INCOMPLETE")


def newest(blocks):
    """The most recently attached file (later position wins a tie)."""
    best = None
    for block in blocks:
        if best is None or block["created"] >= best["created"]:
            best = block
    return best


def export_date(name):
    """The date in a file name such as ..._20260907.csv or ...-2026-09-07.csv, as an ISO string, else None."""
    match = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", name or "")
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else None


def download(url, opener=urllib.request.urlopen):
    """The file's text. https and Notion-hosted only, size-capped, one retry on a transient failure; fixed codes on every failure."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(HOST_SUFFIX):
        raise NetworkError("NETWORK_FILE_URL_REFUSED")
    for attempt in range(2):
        try:
            with opener(urllib.request.Request(url, headers={"User-Agent": "life-os-v7"}), timeout=60) as response:
                raw = response.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise NetworkError("NETWORK_FILE_TOO_LARGE")
            return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt == 0:
                continue
            raise NetworkError(f"NETWORK_FILE_HTTP_{error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 0:
                continue
            raise NetworkError("NETWORK_FILE_NETWORK") from None
    raise NetworkError("NETWORK_FILE_NETWORK")
