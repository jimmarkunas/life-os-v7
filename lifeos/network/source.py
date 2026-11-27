"""Find and fetch the connections file attached to a private Notion page (read-only, in memory only).

The file is never saved, logged or uploaded. Only Notion-hosted files are accepted."""

# Notion serves attachments from a short-lived signed address: read from the page's blocks each run, used once,
# never stored or printed.
import re
import urllib.error
import urllib.parse
import urllib.request

from lifeos.network.errors import NetworkError
from lifeos.platform import notion_pages

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CHILD_PAGES = 20
HOST_SUFFIX = ".amazonaws.com"
PAGE_ID = re.compile(r"[0-9a-fA-F]{32}")


def clean_page_id(value):
    plain = (value or "").strip().replace("-", "")
    if not PAGE_ID.fullmatch(plain):
        raise NetworkError("NETWORK_CONFIG_MISSING")
    return plain


def csv_file_blocks(client, page_id):
    """Every CSV file block on the page, with name, time and address. Incomplete listings raise."""
    out = []
    for reply in notion_pages.children(
        client, clean_page_id(page_id), NetworkError, incomplete="NETWORK_PAGE_INCOMPLETE", limit=MAX_CHILD_PAGES
    ):
        for block in reply["results"]:
            if not isinstance(block, dict) or block.get("type") != "file":
                continue
            attached = block.get("file") or {}
            if attached.get("type") != "file":
                continue
            url = (attached.get("file") or {}).get("url") or ""
            name = attached.get("name") or urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
            if name.lower().endswith(".csv") and url:
                out.append({"name": name, "url": url, "created": block.get("created_time") or ""})
    return out


def newest(blocks):
    best = None
    for block in blocks:
        if best is None or block["created"] >= best["created"]:
            best = block
    return best


def export_date(name):
    """The date in a file name such as ..._20260907.csv, as an ISO string, else None."""
    match = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", name or "")
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else None


def download(url, opener=urllib.request.urlopen):
    """The file's text: https and Notion-hosted only, size-capped, one retry."""
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
