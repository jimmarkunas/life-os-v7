"""The read-only fetchers behind the Followup readers: one per source, GET only, bounded by the checkpoint and a row cap.

`build_fetchers(environ)` -> {source: fetch(credential_ref, since)}. The reader passes a credential NAME; a fetcher
accepts only the names in CREDENTIAL_NAMES for its source and builds a GET-only client at call time. A missing value or
setting is a fixed code. Rows keep only id, link, time and a title or subject; MAX_ROWS + 1 come back so truncation is
visible. TGP mail is dropped. Outlook credentials are labels for rotating tokens in the v7_outlook_token store; Jira's
token can write, so its client is GET-only in code and a test pins that.
"""

from datetime import datetime, timedelta, timezone
import functools
import json
import math
import re
import urllib.parse

from lifeos.platform import followup_readers as readers
from lifeos.platform import gcal, gmail, jira, notion_client, outlook, todoist

SOURCES = ("jira", "calendar", "gmail", "outlook", "todoist", "notion", "interview_os")
TENANTS = ("personal", "bytalos")
MAX_ROWS = readers.MAX_ROWS
LOOKBACK_DAYS = 7
NOTION_LOOKBACK_DAYS = 1  # search returns every edited page, so a first read looks back one day
CALENDAR_AHEAD_DAYS = 30
MARGIN = timedelta(minutes=5)
MAX_PAGES = 10
JIRA_ENV = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
JIRA_PROJECTS = {"personal": ("JFM", "LIFE"), "bytalos": ("BAD", "BPP")}
GMAIL_APP = ("GMAIL_OAUTH_CLIENT_ID", "GMAIL_OAUTH_CLIENT_SECRET")
OUTLOOK_SCOPES = outlook.SCOPES
OUTLOOK_APP = "OUTLOOK_CLIENT_ID"
SLOTS = {("outlook", "personal"): 2}
OUTLOOK_LABELS = {1: "personal", 2: "work"}
STORED_TOKENS = {"outlook"}
NOT_USED = {("outlook", "bytalos"), ("todoist", "bytalos")}
TGP_ADDRESS = re.compile(r"(?<![a-z0-9-])(?:[a-z0-9-]+\.)*tgpinternational\.com(?![a-z0-9-])", re.I)
GMAIL_HEADERS = (
    "Subject",
    "From",
    "To",
    "Cc",
    "Reply-To",
    "Sender",
    "Return-Path",
    "Resent-From",
    "Resent-To",
    "Delivered-To",
    "X-Forwarded-For",
    "X-Forwarded-To",
    "X-Original-From",
    "X-Original-To",
    "X-Original-Sender",
)
OUTLOOK_TRACE = {
    "return-path",
    "resent-from",
    "resent-to",
    "delivered-to",
    "x-forwarded-for",
    "x-forwarded-to",
    "x-original-from",
    "x-original-to",
    "x-original-sender",
    "reply-to",
    "sender",
}
OUTLOOK_FIELDS = (
    "id,receivedDateTime,subject,webLink,from,sender,toRecipients,ccRecipients,replyTo,internetMessageHeaders"
)


def _name(source, tenant, kind):
    return f"FOLLOWUP_{source}_{tenant.upper()}_{kind}"


EXISTING = {
    "jira": "JIRA_API_TOKEN",
    "notion": "NOTION_API_TOKEN",
    "interview_os": "NOTION_API_TOKEN",
    "calendar": "GCAL_SERVICE_ACCOUNT_JSON",
}
EXISTING_GMAIL = {"personal": "GMAIL_OAUTH_REFRESH_TOKEN"}


def _credential_name(source, tenant, slot):
    if source in EXISTING:
        return EXISTING[source]
    if source == "gmail" and tenant in EXISTING_GMAIL:
        return EXISTING_GMAIL[tenant]
    if source == "outlook":
        return OUTLOOK_LABELS[slot]
    return {
        "gmail": f"FOLLOWUP_GMAIL_{tenant.upper()}_READONLY_REFRESH",
        "todoist": f"FOLLOWUP_TODOIST_{tenant.upper()}_TOKEN",
    }[source]


CREDENTIAL_NAMES = {
    (source, tenant, slot): _credential_name(source, tenant, slot)
    for source in SOURCES
    for tenant in TENANTS
    if (source, tenant) not in NOT_USED
    for slot in range(1, SLOTS.get((source, tenant), 1) + 1)
}
_SHARED = {name for name in CREDENTIAL_NAMES.values() if list(CREDENTIAL_NAMES.values()).count(name) > 1}
GRANT_REFS = {
    key: (f"{name}:{key[0]}.{key[1]}" if name in _SHARED else name) for key, name in CREDENTIAL_NAMES.items()
}  # what a grant names; a secret shared by several grants carries source.workspace
NEW_NAMES = sorted({name for name in CREDENTIAL_NAMES.values() if name.startswith("FOLLOWUP_")})
REUSED_NAMES = sorted(
    {name for key, name in CREDENTIAL_NAMES.items() if key[0] not in STORED_TOKENS and not name.startswith("FOLLOWUP_")}
)
ACCOUNT_SOURCE = {"calendar": "CALENDAR", "interview_os": "INTERVIEW"}


def _account_name(key):
    source, tenant, slot = key
    return f"FOLLOWUP_ACCOUNT_{ACCOUNT_SOURCE.get(source, source.upper())}_{tenant.upper()}" + (
        f"_{slot}" if (source, tenant) in SLOTS else ""
    )


ACCOUNT_NAMES = {key: _account_name(key) for key in CREDENTIAL_NAMES}
CONFIG_NAMES = {  # settings that are identifiers, not secrets ({T} is PERSONAL or BYTALOS)
    "jira": ("JIRA_BASE_URL", "JIRA_EMAIL"),
    "calendar": ("FOLLOWUP_GCAL_{T}_CALENDAR_ID",),
    "gmail": GMAIL_APP,
    "outlook": (OUTLOOK_APP,),
    "todoist": (),
    "notion": (),
    "interview_os": (),
}
_BY_REF = {ref: key for key, ref in GRANT_REFS.items()}


class FetchError(RuntimeError):
    """The message is always a fixed code."""


def missing(environ):
    return sorted(
        {
            name
            for key, name in CREDENTIAL_NAMES.items()
            if key[0] not in STORED_TOKENS and not (environ.get(name) or "").strip()
        }
    )


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
        if not (method == "GET" or (method == "POST" and url == gmail.TOKEN_URL and not auth)):
            raise FetchError("FOLLOWUP_READ_ONLY")
        return super()._request(method, url, body, form, auth)

    def message_metadata(self, message_id):
        """One message's time and the headers the reader needs (subject, routing headers for the TGP filter); never the body."""
        query = "&".join("metadataHeaders=" + urllib.parse.quote(name) for name in GMAIL_HEADERS)
        return self._request(
            "GET", f"{gmail.API}/messages/{urllib.parse.quote(str(message_id), safe='')}?format=metadata&{query}"
        )


class ReadOnlyOutlook(outlook.Outlook):
    def _request(self, method, url, params=None, body=None, prefer=""):
        if method != "GET":
            raise FetchError("FOLLOWUP_READ_ONLY")
        return super()._request(method, url, params, body, prefer)


class ReadOnlyNotion(notion_client.Client):
    def call(self, method, path, body=None):
        if not (
            method == "GET"
            or (
                method == "POST"
                and (path == "/search" or (path.startswith("/data_sources/") and path.endswith("/query")))
            )
        ):
            raise FetchError("FOLLOWUP_READ_ONLY")
        return super().call(method, path, body)

    call_once = _refuse_write


class DbTokens:
    """The private refresh-token store: load by label, save a rotated token. The write-back is the only write here."""

    def load(self, label):
        from lifeos.platform import db, outlook_tokens as store  # noqa: PLC0415

        with db.connect() as connection:
            return store.load(connection, label)

    def save(self, label, token):
        from lifeos.platform import db, outlook_tokens as store  # noqa: PLC0415

        with db.connect() as connection:
            store.save(connection, label, token)


def _make_jira(base_url, email, token):
    return ReadOnlyJira(base_url, email, token)


def _make_notion(secret):
    return ReadOnlyNotion({"T": secret, "S": "search"}, token_name="T", source_name="S")


FACTORIES = {
    "jira": _make_jira,
    "calendar": lambda secret, calendar_id: ReadOnlyCalendar(secret, calendar_id),
    "gmail": lambda client_id, client_secret, refresh: ReadOnlyGmail(client_id, client_secret, refresh),
    "outlook": lambda client_id, refresh, save=None: ReadOnlyOutlook(client_id, refresh, save),
    "todoist": lambda secret: todoist.Todoist(secret),
    "notion": _make_notion,
    "interview_os": _make_notion,
}


def _config(environ, name):
    value = (environ.get(name) or "").strip()
    if not value:
        raise FetchError("FOLLOWUP_CONFIG_MISSING")
    return value


def _start(since, now, days=LOOKBACK_DAYS):
    return (since - MARGIN) if since else now - timedelta(days=days)


def _own_id(client):
    """The id of the bot behind this Notion token (one GET), or None when unreadable."""
    try:
        me = client.call("GET", "/users/me")
    except Exception:  # noqa: BLE001
        return None
    ident = me.get("id") if isinstance(me, dict) else None
    return ident if isinstance(ident, str) and ident else None


def _utc(text):
    parsed = (
        datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text.replace("Z", "+00:00")))
        if isinstance(text, str)
        else None
    )
    return parsed.astimezone(timezone.utc).isoformat() if parsed and parsed.tzinfo else None


def _is_tgp(*texts):
    return any(TGP_ADDRESS.search(str(text)) for text in texts if text)


def _read_jira(make, environ, tenant, secret, since, now):
    client = make(_config(environ, "JIRA_BASE_URL"), _config(environ, "JIRA_EMAIL"), secret)
    keys = JIRA_PROJECTS[tenant]
    minutes = math.ceil((now - _start(since, now)).total_seconds() / 60)
    jql = f"project in ({', '.join(keys)}) AND (assignee = currentUser() OR reporter = currentUser()) AND updated >= -{minutes}m ORDER BY updated ASC"
    rows, token = [], None
    for _ in range(MAX_PAGES):
        page = client.get(
            "/rest/api/3/search/jql",
            {
                "jql": jql,
                "fields": "summary,updated,duedate",
                "maxResults": 100,
                **({"nextPageToken": token} if token else {}),
            },
        )
        if not isinstance(page, dict) or not isinstance(page.get("issues"), list):
            raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")
        for issue in page["issues"]:
            key = issue.get("key") if isinstance(issue, dict) else None
            if not isinstance(key, str) or key.split("-")[0] not in keys:
                continue
            fields = issue.get("fields") or {}
            rows.append(
                {
                    "key": key,
                    "fields": {
                        "updated": _utc(fields.get("updated")),
                        "summary": fields.get("summary"),
                        "duedate": fields.get("duedate"),
                    },
                }
            )
        token = page.get("nextPageToken")
        if len(rows) > MAX_ROWS or page.get("isLast", not token):
            return rows
        if not isinstance(token, str) or not token:
            raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")
    raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")


def _read_calendar(make, environ, tenant, secret, since, now):
    client = make(secret, _config(environ, f"FOLLOWUP_GCAL_{tenant.upper()}_CALENDAR_ID"))
    events = client.list_events(
        (now - timedelta(days=LOOKBACK_DAYS)).isoformat(), (now + timedelta(days=CALENDAR_AHEAD_DAYS)).isoformat()
    )
    return [
        {
            "id": e.get("id"),
            "htmlLink": e.get("htmlLink"),
            "updated": _utc(e.get("updated")),
            "summary": e.get("summary"),
        }
        for e in events
    ]


def _bare_token(secret):
    if secret.startswith("{"):
        try:
            secret = str(json.loads(secret).get("refresh_token") or "").strip()
        except (ValueError, AttributeError):
            raise FetchError("FOLLOWUP_CREDENTIAL_MISSING") from None
    if not secret:
        raise FetchError("FOLLOWUP_CREDENTIAL_MISSING")
    return secret


def _read_gmail(make, environ, tenant, secret, since, now):
    client = make(_config(environ, GMAIL_APP[0]), _config(environ, GMAIL_APP[1]), _bare_token(secret))
    ids = client.list_ids(f"after:{int(_start(since, now).timestamp())}", limit=MAX_ROWS + 1)
    rows = []
    for message_id in ids:
        reply = client.message_metadata(message_id)
        if not isinstance(reply, dict):
            raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")
        headers = [h for h in (reply.get("payload") or {}).get("headers") or [] if isinstance(h, dict)]
        if _is_tgp(*[h.get("value") for h in headers if str(h.get("name")).lower() != "subject"]):
            continue
        subject = next((h.get("value") for h in headers if str(h.get("name")).lower() == "subject"), "")
        try:
            at = datetime.fromtimestamp(int(reply["internalDate"]) / 1000, timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            at = None
        rows.append(
            {
                "id": str(reply.get("id") or message_id),
                "link": f"https://mail.google.com/mail/u/0/#all/{urllib.parse.quote(str(message_id), safe='')}",
                "at": at,
                "subject": subject,
            }
        )
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
    traced = [
        h.get("value")
        for h in message.get("internetMessageHeaders") or []
        if isinstance(h, dict) and str(h.get("name")).lower() in OUTLOOK_TRACE
    ]
    return _is_tgp(
        *_addresses(
            message.get("from"),
            message.get("sender"),
            message.get("toRecipients"),
            message.get("ccRecipients"),
            message.get("replyTo"),
        ),
        *traced,
    )


def _read_outlook(make, environ, tenant, secret, since, now, save=None):
    client = make(_config(environ, OUTLOOK_APP), secret, save)
    start = _start(since, now).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    messages = client.messages(folder="inbox", since=start, limit=MAX_ROWS + 1, fields=OUTLOOK_FIELDS)
    return [
        {
            "id": m.get("id"),
            "link": m.get("webLink"),
            "at": _utc(m.get("receivedDateTime")),
            "subject": m.get("subject"),
        }
        for m in messages
        if not _outlook_tgp(m)
    ]


def _read_todoist(make, environ, tenant, secret, since, now):
    tasks = make(secret).tasks(MAX_ROWS + 1)
    return [
        {
            "id": t.get("id"),
            "url": f"https://app.todoist.com/app/task/{urllib.parse.quote(str(t.get('id')), safe='')}",
            "updated_at": _utc(t.get("updated_at") or t.get("added_at")),
            "content": t.get("content"),
            "due": (t.get("due") or {}).get("date") if isinstance(t.get("due"), dict) else None,
        }
        for t in tasks
    ]


def _title(page):
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(
                str(part.get("plain_text") or "") for part in prop.get("title") or [] if isinstance(part, dict)
            )
    return ""


def _read_notion_like(time_key, title_key):
    def read(make, environ, tenant, secret, since, now):
        client, start = make(secret), _start(since, now, NOTION_LOOKBACK_DAYS)
        own = _own_id(client)
        body = {
            "page_size": 100,
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        }
        rows, cursor = [], None
        for _ in range(MAX_PAGES):
            reply = client.call("POST", "/search", {**body, **({"start_cursor": cursor} if cursor else {})})
            if not isinstance(reply, dict) or not isinstance(reply.get("results"), list):
                raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")
            for item in reply["results"]:
                edited = _utc(item.get("last_edited_time")) if isinstance(item, dict) else None
                if edited is None:
                    continue
                if datetime.fromisoformat(edited) < start:
                    return rows
                if own and ((item.get("last_edited_by") or {}).get("id") == own):
                    continue
                rows.append({"id": item.get("id"), "url": item.get("url"), time_key: edited, title_key: _title(item)})
                if len(rows) > MAX_ROWS:
                    return rows
            cursor = reply.get("next_cursor")
            if not reply.get("has_more"):
                return rows
            if not isinstance(cursor, str) or not cursor:
                raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")
        raise FetchError("FOLLOWUP_LISTING_INCOMPLETE")

    return read


READ = {
    "jira": _read_jira,
    "calendar": _read_calendar,
    "gmail": _read_gmail,
    "outlook": _read_outlook,
    "todoist": _read_todoist,
    "notion": _read_notion_like("last_edited_time", "title"),
    "interview_os": _read_notion_like("at", "title"),
}


def build_fetchers(environ, factories=None, clock=None, token_store=None):
    """{source: fetch(credential_ref, since)}. `environ` is read at call time; `factories` replaces the client builders; `token_store` holds Outlook refresh tokens."""
    store = token_store or DbTokens()
    make = {**FACTORIES, **(factories or {})}
    now = clock or (lambda: datetime.now(timezone.utc))

    def wrap(source):
        def fetch(credential_ref, since):
            key = _BY_REF.get(credential_ref)
            if key is None or key[0] != source:
                raise FetchError("FOLLOWUP_CREDENTIAL_NOT_ALLOWED")
            read = READ[source]
            if source in STORED_TOKENS:
                secret = (store.load(credential_ref) or "").strip()
                read = functools.partial(read, save=lambda token, label=credential_ref: store.save(label, token))
            else:
                secret = (environ.get(CREDENTIAL_NAMES[key]) or "").strip()
                if not secret and source == "calendar" and gcal.keyless_available(environ):
                    secret = gcal.KEYLESS  # the calendar key is retired; the job signs in with its OIDC token
            if not secret:
                raise FetchError("FOLLOWUP_CREDENTIAL_MISSING")
            rows = read(make[source], environ, key[1], secret, since, now())[: MAX_ROWS + 1]
            if readers.SECRET.search(repr(rows)):
                raise FetchError("FOLLOWUP_SECRET_IN_REPLY")
            return rows

        return fetch

    return {source: wrap(source) for source in SOURCES}


__all__ = [
    "SOURCES",
    "CREDENTIAL_NAMES",
    "GRANT_REFS",
    "NEW_NAMES",
    "REUSED_NAMES",
    "ACCOUNT_NAMES",
    "CONFIG_NAMES",
    "JIRA_PROJECTS",
    "FetchError",
    "build_fetchers",
    "missing",
]
