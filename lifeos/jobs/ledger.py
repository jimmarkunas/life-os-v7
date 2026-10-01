"""Production target guard: before any Notion write (publish, audit trash, retention trash) verify the configured data source really is the LIFE OS
Job Ledger, by the properties this code reads and writes and their types. Wrong or unreadable target = no write. Checked once per client."""
from lifeos.platform.notion_client import NotionError

REQUIRED = {"Job": "title", "Apply URL": "url", "Stable Job Key": "rich_text", "Applied": "checkbox", "Applied On": "date", "Saturn Decision": "select",
            "Visible Lane": "select", "Admission Status": "select", "Posting Date": "date", "First Surfaced": "date", "LIFE OS Fit": "number"}
OK, UNREADABLE, MISMATCH = "ok", "target_unreadable", "target_mismatch"


def verify(client):
    """-> 'ok' | 'target_unreadable' | 'target_mismatch'. Cached on the client, so each run asks Notion once."""
    cached = getattr(client, "_ledger_target", None)
    if cached:
        return cached
    try:
        props = client.call("GET", f"/data_sources/{client.source}").get("properties") or {}
    except NotionError:
        result = UNREADABLE
    else:
        result = OK if all((props.get(name) or {}).get("type") == kind for name, kind in REQUIRED.items()) else MISMATCH
    try:
        client._ledger_target = result
    except AttributeError:
        pass
    return result


def missing(client):
    """Names of required properties that are absent or the wrong type (counts-only diagnostics: names are schema, not content)."""
    props = client.call("GET", f"/data_sources/{client.source}").get("properties") or {}
    return sorted(n for n, k in REQUIRED.items() if (props.get(n) or {}).get("type") != k)
