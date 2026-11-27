"""The Weekly Review's Drive lane, read-only: counts of one Drive account's files made or changed in a window, plus unfiled and duplicate counts.

One metadata listing (GET files with `modifiedTime` after the window start, never file content) through the read-only
Google client the governed MCP uses (google_read.Google). Returns counts only: no name, id or link leaves this module.
Unfiled means created in the window directly in My Drive's top level; a duplicate is a second file with the same
content checksum.
"""

import urllib.parse
from datetime import datetime, timedelta, timezone

try:
    from lifeos.platform import google_read
except ImportError:  # installed beside the Oracle helper, outside the lifeos package
    import google_read

PAGE, MAX_PAGES = 1000, 5
FIELDS = "nextPageToken,files(createdTime,modifiedTime,parents,md5Checksum,mimeType)"


def _when(text):
    return datetime.fromisoformat((text or "1970-01-01T00:00:00Z").replace("Z", "+00:00"))


def summary(google, account, days, now=None):
    """-> {"new", "changed", "unfiled", "duplicates", "missing_metadata", "complete"} for the last `days` days. Raises
    google_read.GoogleError with its fixed reason on any failure.
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    google_read.check_account(account)
    root = (google._get(account, "drive", f"{google_read.DRIVE}/root?fields=id") or {}).get("id")
    files, token = [], None
    for _ in range(MAX_PAGES):
        query = {
            "q": f"modifiedTime > '{since.strftime('%Y-%m-%dT%H:%M:%SZ')}' and trashed = false",
            "pageSize": PAGE,
            "fields": FIELDS,
            **({"pageToken": token} if token else {}),
        }
        page = google._get(account, "drive", f"{google_read.DRIVE}?{urllib.parse.urlencode(query)}") or {}
        files.extend(page.get("files") or [])
        token = page.get("nextPageToken")
        if not token:
            break
    new = [f for f in files if _when(f.get("createdTime")) >= since]
    unfiled = sum(1 for f in new if not f.get("parents") or f.get("parents") == [root])
    seen, duplicates = {}, 0
    for f in new:
        if f.get("md5Checksum"):
            duplicates += 1 if seen.get(f["md5Checksum"]) else 0
            seen[f["md5Checksum"]] = True
    return {
        "new": len(new),
        "changed": len(files) - len(new),
        "unfiled": unfiled,
        "duplicates": duplicates,
        "missing_metadata": unfiled,
        "complete": token is None,
    }
