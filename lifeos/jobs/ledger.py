"""Production target guard: before any Notion write (publish, audit trash, retention trash) verify the configured data source really is the LIFE OS
Job Ledger, by the properties this code reads and writes and their types. Wrong or unreadable target = no write. Checked once per client."""
from datetime import timezone
from zoneinfo import ZoneInfo

from lifeos.platform.notion_client import NotionError

REQUIRED = {"Job": "title", "Apply URL": "url", "Stable Job Key": "rich_text", "Applied": "checkbox", "Applied On": "date", "Saturn Decision": "select",
            "Visible Lane": "select", "Admission Status": "select", "Posting Date": "date", "First Surfaced": "date", "LIFE OS Fit": "number"}
OK, UNREADABLE, MISMATCH = "ok", "target_unreadable", "target_mismatch"
CT = ZoneInfo("America/Chicago")


def ct_day(moment):
    """LIFE-170 (Jim, Oct 30 2026): the Ledger's day stamps (First Surfaced, Last Seen) are Jim's calendar day in US Central, so "today" turns over at midnight CT,
    not 7 PM. Jobs OS stores naive UTC instants; a date (or None) passes through unchanged."""
    if not hasattr(moment, "hour"):
        return moment
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(CT).date()


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


def page_state(client, page_id):
    """'gone' | 'open' | 'unreadable': whether Notion still holds this page. A page in the trash or archived answers a read (so it is told apart from one that cannot be read)
    but refuses every property write, which is how a page that was deleted by hand stays 'due' and 'failed' on every run. Read only."""
    try:
        page = client.call("GET", f"/pages/{page_id}")
    except NotionError:
        return "unreadable"
    if not isinstance(page, dict):
        return "unreadable"
    return "gone" if page.get("archived") or page.get("in_trash") else "open"


def missing(client):
    """Names of required properties that are absent or the wrong type (counts-only diagnostics: names are schema, not content)."""
    props = client.call("GET", f"/data_sources/{client.source}").get("properties") or {}
    return sorted(n for n, k in REQUIRED.items() if (props.get(n) or {}).get("type") != k)


def select_options(client, prop):
    """The option names of one select property, as the Ledger spells them (a filter naming an option that does not exist is refused with HTTP 400)."""
    props = client.call("GET", f"/data_sources/{client.source}").get("properties") or {}
    return [option["name"] for option in ((props.get(prop) or {}).get("select") or {}).get("options") or [] if option.get("name")]

