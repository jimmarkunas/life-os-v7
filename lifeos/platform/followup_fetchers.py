"""LIFE-757: the read-only fetchers behind the Followup readers: one per source, GET only, bounded by the checkpoint and a row cap. Fixtures only until the live step runs."""
# `build_fetchers(environ)` -> {source: fetch(credential_ref, since)}, the `fetch` that `followup_readers.read_delta` calls. The reader passes a credential NAME; the fetcher accepts only the names in
# CREDENTIAL_NAMES for ITS source (a grant can never point the Jira fetcher at the Todoist token or at any other environment variable), reads the value at call time and builds a read-only client.
# Read-only is built in, not promised: each client is a subclass whose request path refuses every method but GET (token exchanges aside), and the Calendar scope is calendar.readonly.
# A missing value or setting is a fixed code, which the reader turns into DEGRADED for that source alone. Rows carry only what the normalizers need (id, link, time, a title or subject); bodies,
# snippets, descriptions, attendees and recipients are never kept. At most MAX_ROWS + 1 rows come back, so the reader sees a larger answer as truncated. Personal and Bytalos use separate names, and
# Jira reads only that workspace's project keys. TGP is excluded: there is no TGP name, scope or credential here, and mail from or to a TGP address, or forwarded from one, is dropped.
# The names below are the NEW read-only credentials Jim creates (Bitwarden, loaded by the hourly job); the existing write-capable tokens are never used here.
from datetime import datetime, timedelta, timezone
import math
import re
import urllib.parse

from lifeos.platform import followup_readers as readers
from lifeos.platform import gcal, gmail, jira, notion_client, notion_pages, outlook, todoist

SOURCES = ("jira", "calendar", "gmail", "outlook", "todoist", "notion", "interview_os")
TENANTS = ("personal", "bytalos")
MAX_ROWS = readers.MAX_ROWS
LOOKBACK_DAYS = 7                    # a first read (no checkpoint) looks back this far
CALENDAR_AHEAD_DAYS = 30
MARGIN = timedelta(minutes=5)        # the readers filter exactly on the checkpoint; this only keeps an API's coarse time filter from dropping a row
MAX_PAGES = 10
JIRA_CLOUD_ID = "FOLLOWUP_JIRA_CLOUD_ID"
JIRA_PROJECTS = {"personal": ("JFM", "LIFE", "LOS"), "bytalos": ("BAD", "BPP")}      # one Atlassian account; each workspace's grant reads only its own project keys (Jim, Nov 16 2026)
GMAIL_APP = ("GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET")     # the existing OAuth app (not an account credential); the refresh token below is new and read-only
OUTLOOK_APP = "OUTLOOK_CLIENT_ID"                                      # the existing public client id; the refresh token below is new and read-only
SLOTS = {("outlook", "personal"): 3}                                   # three Personal Outlook accounts; every other source has one account per workspace
NOT_USED = {("outlook", "bytalos"), ("todoist", "bytalos")}            # no Bytalos Outlook, Todoist is Personal only
TGP_ADDRESS = re.compile(r"(?<![a-z0-9-])(?:[a-z0-9-]+\.)*tgpinternational\.com(?![a-z0-9-])", re.I)
GMAIL_HEADERS = ("Subject", "From", "To", "Cc", "Reply-To", "Sender", "Return-Path", "Resent-From", "Resent-To", "Delivered-To", "X-Forwarded-For", "X-Forwarded-To", "X-Original-From",
                 "X-Original-To", "X-Original-Sender")
OUTLOOK_TRACE = {"return-path", "resent-from", "resent-to", "delivered-to", "x-forwarded-for", "x-forwarded-to", "x-original-from", "x-original-to", "x-original-sender", "reply-to", "sender"}
OUTLOOK_FIELDS = "id,receivedDateTime,subject,webLink,from,sender,toRecipients,ccRecipients,replyTo,internetMessageHeaders"


def _name(source, tenant, kind):
    return f"FOLLOWUP_{source}_{tenant.upper()}_{kind}"


def _credential_name(source, tenant, slot):
    if source == "outlook":
        return f"FOLLOWUP_OUTLOOK_READONLY_{tenant.upper()}_{slot}"                   # refresh token from the Mail.Read + Calendars.Read sign-in, one per account
    kind = {"jira": ("JIRA", "TOKEN"),                                                # email:token of a scoped Atlassian token (read:jira-work), one per workspace though the account is shared
            "calendar": ("GCAL", "READONLY_SA_JSON"),                                 # service-account JSON, shared the one calendar read-only
            "gmail": ("GMAIL", "READONLY_REFRESH"),                                   # refresh token from a gmail.readonly consent
            "todoist": ("TODOIST", "TOKEN"),                                          # a data:read token
            "notion": ("NOTION", "READONLY_TOKEN"),                                   # the one read-only integration, stored under each workspace's name so a grant names its workspace
            "interview_os": ("INTERVIEW", "READONLY_TOKEN")}[source]                  # the same integration for the Interview OS data sources
    return _name(kind[0], tenant, kind[1])


CREDENTIAL_NAMES = {(source, tenant, slot): _credential_name(source, tenant, slot) for source in SOURCES for tenant in TENANTS if (source, tenant) not in NOT_USED
                    for slot in range(1, SLOTS.get((source, tenant), 1) + 1)}
ACCOUNT_NAMES = {key: name + "_ACCOUNT" for key, name in CREDENTIAL_NAMES.items()}   # the account label per credential comes from the environment (never from the repo)
CONFIG_NAMES = {                                                                      # settings that are identifiers, not secrets (templates; {T} is PERSONAL or BYTALOS)
    "jira": (JIRA_CLOUD_ID,), "calendar": ("FOLLOWUP_GCAL_{T}_CALENDAR_ID",), "gmail": GMAIL_APP, "outlook": (OUTLOOK_APP,),
    "todoist": (), "notion": ("FOLLOWUP_NOTION_{T}_SOURCE_IDS",), "interview_os": ("FOLLOWUP_INTERVIEW_{T}_SOURCE_IDS",)}
_BY_NAME = {name: key for key, name in CREDENTIAL_NAMES.items()}


class FetchError(RuntimeError):
    """The message is always a fixed code."""


def missing(environ):
    """Credential names not set, sorted; names only."""
    return sorted(name for name in CREDENTIAL_NAMES.values() if not (environ.get(name) or "").strip())


def _refuse_write(*_args, **_kwargs):
    raise FetchError("FOLLOWUP_READ_ONLY")


class ReadOnlyJira(jira.Jira):
    def request(self, method, path, params=None, payload=None, retry=None):
        if method != "GET":
            raise FetchError("FOLLOWUP_READ_ONLY")
        return super().request(method, path, params, payload, retry)

    post = _refuse_write


class ReadOnlyCalendar(gcal.GoogleCalendar):
    def __init__(self, service_account_json, calendar_id, **kwargs):
        super().__init__(service_account_json, calendar_id, scope=gcal.SCOPE_READONLY, **kwargs)

    def request(self, method, path, params=None, body=None, ok=(200,)):
        if method != "GET":
            raise FetchError("FOLLOWUP_READ_ONLY")
        return super().request(method, path, params, body, ok)


class ReadOnlyGmail(gmail.Gmail):
    def _request(self, method, url, body=None, form=None, auth=True):
        if not (method == "GET" or (method == "POST" and url == gmail.TOKEN_URL and not auth)):         # the one POST is the refresh-token exchange
            raise FetchError("FOLLOWUP_READ_ONLY")
        return super()._request(method, url, body, form, auth)

    def message_metadata(self, message_id):
        """One message's time and the few headers the reader needs (subject, and the routing headers the TGP filter checks); never the body."""
        query = "&".join("metadataHeaders=" + urllib.parse.quote(name) for name in GMAIL_HEADERS)
        return self._request("GET", f"{gmail.API}/messages/{urllib.parse.quote(str(message_id), safe='')}?format=metadata&{query}")


class ReadOnlyOutlook(outlook.Outlook):
    def _request(self, method, url, params=None, body=None, prefer=""):
        if method != "GET":
            raise FetchError("FOLLOWUP_READ_ONLY")
        return super()._request(method, url, params, body, prefer)


class ReadOnlyNotion(notion_client.Client):
    def call(self, method, path, body=None):
        if not (method == "GET" or (method == "POST" and path.startswith("/data_sources/") and path.endswith("/query"))):         # a data-source query is a POST but reads
            raise FetchError("FOLLOWUP_READ_ONLY")
        return super().call(method, path, body)

    call_once = _refuse_write


def _make_jira(secret, cloud_id):
    email, _, token = secret.partition(":")
    return ReadOnlyJira(f"https://api.atlassian.com/ex/jira/{urllib.parse.quote(cloud_id, safe='')}", email, token)


def _make_notion(secret, first_source):
    return ReadOnlyNotion({"T": secret, "S": first_source}, token_name="T", source_name="S")


FACTORIES = {
    "jira": _make_jira,
    "calendar": lambda secret, calendar_id: ReadOnlyCalendar(secret, calendar_id),
    "gmail": lambda client_id, client_secret, refresh: ReadOnlyGmail(client_id, client_secret, refresh),
    "outlook": lambda client_id, refresh: ReadOnlyOutlook(client_id, refresh),
    "todoist": lambda secret: todoist.Todoist(secret),
    "notion": _make_notion,
    "interview_os": _make_notion,
}


def _config(environ, name):
    value = (environ.get(name) or "").strip()
    if not value:
        raise FetchError("FOLLOWUP_CONFIG_MISSING")
    return value


def _start(since, now):
    return (since - MARGIN) if since else now - timedelta(days=LOOKBACK_DAYS)


def _utc(text):
    parsed = datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text.replace("Z", "+00:00"))) if isinstance(text, str) else None
    return parsed.astimezone(timezone.utc).isoformat() if parsed and parsed.tzinfo else None


def _is_tgp(*texts):
    """True when any value names a TGP address or domain."""
    return any(TGP_ADDRESS.search(str(text)) for text in texts if text)


def _read_jira(make, environ, tenant, secret, since, now):
    client = make(secret, _config(environ, JIRA_CLOUD_ID))
    keys = JIRA_PROJECTS[tenant]
    minutes = math.ceil((now - _start(since, now)).total_seconds() / 60)                  # a relative time: JQL accepts -Nm, so no date format is built here
    jql = f"project in ({', '.join(keys)}) AND (assignee = currentUser() OR reporter = currentUser()) AND updated >= -{minutes}m ORDER BY updated ASC"
    rows, token = [], None
    for _ in range(MAX_PAGES):
        page = client.get("/rest/api/3/search/jql", {"jql": jql, "fields": "summary,updated", "maxResults": 100, **({"nextPageToken": token} if token else {})})
        if not isinstance(page, dict) or not isinstance(page.get("issues"), list):
            raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")
        for issue in page["issues"]:
            key = issue.get("key") if isinstance(issue, dict) else None
            if not isinstance(key, str) or key.split("-")[0] not in keys:                 # defense in depth: a key outside this workspace's projects is never returned
                continue
            fields = issue.get("fields") or {}
            rows.append({"key": key, "fields": {"updated": _utc(fields.get("updated")), "summary": fields.get("summary")}})
        token = page.get("nextPageToken")
        if len(rows) > MAX_ROWS or page.get("isLast", not token):
            return rows
        if not isinstance(token, str) or not token:
            raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")
    raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")


def _read_calendar(make, environ, tenant, secret, since, now):
    client = make(secret, _config(environ, f"FOLLOWUP_GCAL_{tenant.upper()}_CALENDAR_ID"))
    events = client.list_events((now - timedelta(days=LOOKBACK_DAYS)).isoformat(), (now + timedelta(days=CALENDAR_AHEAD_DAYS)).isoformat())
    return [{"id": e.get("id"), "htmlLink": e.get("htmlLink"), "updated": _utc(e.get("updated")), "summary": e.get("summary")} for e in events]


def _read_gmail(make, environ, tenant, secret, since, now):
    client = make(_config(environ, GMAIL_APP[0]), _config(environ, GMAIL_APP[1]), secret)
    ids = client.list_ids(f"after:{int(_start(since, now).timestamp())}", limit=MAX_ROWS + 1)
    rows = []
    for message_id in ids:
        reply = client.message_metadata(message_id)
        if not isinstance(reply, dict):
            raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")
        headers = [h for h in (reply.get("payload") or {}).get("headers") or [] if isinstance(h, dict)]
        if _is_tgp(*[h.get("value") for h in headers if str(h.get("name")).lower() != "subject"]):
            continue                                                                   # TGP is excluded: never a row, never a count
        subject = next((h.get("value") for h in headers if str(h.get("name")).lower() == "subject"), "")
        try:
            at = datetime.fromtimestamp(int(reply["internalDate"]) / 1000, timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            at = None
        rows.append({"id": str(reply.get("id") or message_id), "link": f"https://mail.google.com/mail/u/0/#all/{urllib.parse.quote(str(message_id), safe='')}", "at": at, "subject": subject})
    return rows


def _addresses(*people):
    out = []
    for person in people:
        for entry in person if isinstance(person, list) else [person]:
            address = ((entry or {}).get("emailAddress") or {}).get("address") if isinstance(entry, dict) else None
            if address:
                out.append(address)
    return out


def _outlook_tgp(message):
    traced = [h.get("value") for h in message.get("internetMessageHeaders") or [] if isinstance(h, dict) and str(h.get("name")).lower() in OUTLOOK_TRACE]
    return _is_tgp(*_addresses(message.get("from"), message.get("sender"), message.get("toRecipients"), message.get("ccRecipients"), message.get("replyTo")), *traced)


def _read_outlook(make, environ, tenant, secret, since, now):
    client = make(_config(environ, OUTLOOK_APP), secret)
    start = _start(since, now).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    messages = client.messages(folder="inbox", since=start, limit=MAX_ROWS + 1, fields=OUTLOOK_FIELDS)
    return [{"id": m.get("id"), "link": m.get("webLink"), "at": _utc(m.get("receivedDateTime")), "subject": m.get("subject")} for m in messages if not _outlook_tgp(m)]


def _read_todoist(make, environ, tenant, secret, since, now):
    tasks = make(secret).tasks(MAX_ROWS + 1)
    return [{"id": t.get("id"), "url": f"https://app.todoist.com/app/task/{urllib.parse.quote(str(t.get('id')), safe='')}", "updated_at": _utc(t.get("updated_at") or t.get("added_at")),
             "content": t.get("content")} for t in tasks]


def _title(page):
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(str(part.get("plain_text") or "") for part in prop.get("title") or [] if isinstance(part, dict))
    return ""


def _read_notion_like(setting, time_key, title_key):
    def read(make, environ, tenant, secret, since, now):
        ids = [item.strip() for item in _config(environ, setting.format(T=tenant.upper())).split(",") if item.strip()]
        client = make(secret, ids[0])
        body = {"page_size": 100, "filter": {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": _start(since, now).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}},
                "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}]}
        rows = []
        for source_id in ids:
            for page in notion_pages.query(client, source_id, body, FetchError, incomplete="FOLLOWUP_LISTING_INCOMPLETE", limit=MAX_PAGES):
                for item in page["results"]:
                    if isinstance(item, dict):
                        rows.append({"id": item.get("id"), "url": item.get("url"), time_key: _utc(item.get("last_edited_time")), title_key: _title(item)})
                if len(rows) > MAX_ROWS:
                    return rows
        return rows
    return read


READ = {"jira": _read_jira, "calendar": _read_calendar, "gmail": _read_gmail, "outlook": _read_outlook, "todoist": _read_todoist,
        "notion": _read_notion_like("FOLLOWUP_NOTION_{T}_SOURCE_IDS", "last_edited_time", "title"),
        "interview_os": _read_notion_like("FOLLOWUP_INTERVIEW_{T}_SOURCE_IDS", "at", "title")}


def build_fetchers(environ, factories=None, clock=None):
    """{source: fetch(credential_ref, since)}. `environ` is read at call time; `factories` replaces the client builders (tests); `clock` is the current UTC time."""
    make = {**FACTORIES, **(factories or {})}
    now = clock or (lambda: datetime.now(timezone.utc))

    def wrap(source):
        def fetch(credential_ref, since):
            key = _BY_NAME.get(credential_ref)
            if key is None or key[0] != source:
                raise FetchError("FOLLOWUP_CREDENTIAL_NOT_ALLOWED")
            secret = (environ.get(credential_ref) or "").strip()
            if not secret:
                raise FetchError("FOLLOWUP_CREDENTIAL_MISSING")
            rows = READ[source](make[source], environ, key[1], secret, since, now())[:MAX_ROWS + 1]
            if readers.SECRET.search(repr(rows)):
                raise FetchError("FOLLOWUP_SECRET_IN_REPLY")
            return rows
        return fetch

    return {source: wrap(source) for source in SOURCES}


__all__ = ["SOURCES", "CREDENTIAL_NAMES", "ACCOUNT_NAMES", "CONFIG_NAMES", "JIRA_PROJECTS", "FetchError", "build_fetchers", "missing"]
