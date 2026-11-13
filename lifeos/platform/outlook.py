"""Minimal Microsoft Graph mail client (stdlib only, platform: no product knowledge). Delegated OAuth with refresh tokens (device-code
sign-in once, then silent refresh), immutable message ids, bounded full enumeration. Every error is a fixed code: never a URL, a token,
an address or a response body. Reads retry transient failures. Writes are limited to creating one mail folder and moving a message into
it (both safe to repeat); nothing here ever deletes, sends or edits mail."""
from collections.abc import Callable
from typing import Any
from datetime import datetime
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from lifeos.platform import rest

AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"      # personal Microsoft accounts and work/school tenants
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access Mail.Read Calendars.Read"                       # read-only sign-in
SCOPES_WRITE = "offline_access Mail.ReadWrite Calendars.Read"            # adds moving mail into a folder (a separate, explicit sign-in)
MAX_PAGES = 100
PAGE_SIZE = 100
EVENT_FIELDS = "id,iCalUId,subject,start,end,isAllDay,isCancelled,showAs,responseStatus,location,webLink,type,seriesMasterId"
MESSAGE_FIELDS = "id,internetMessageId,receivedDateTime,isRead,from,subject,hasAttachments,parentFolderId,conversationId"


class OutlookError(RuntimeError):
    """The message is always a fixed code."""


def _down_marker(refresh_token):
    """LIFE-646: one marker file per mailbox in the runner's temp folder (empty outside a runner, so nothing changes locally). It lives only as long as the job: once a mailbox has
    answered a request with a server error or no answer after the client's own retries, every later request to it in the same job fails at once with a fixed code instead of
    retrying the same outage again in each step. No cross-run state."""
    base = os.environ.get("RUNNER_TEMP")
    return os.path.join(base, "lifeos-outlook-down-" + hashlib.sha256(str(refresh_token).encode()).hexdigest()[:12]) if base else None


def _is_outage(code):
    return code == "OUTLOOK_NETWORK" or bool(re.fullmatch(r"OUTLOOK_HTTP_5\d\d", code))


def _post_form(url, form, timeout=30):
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            code = json.loads(error.read()).get("error", "")
        except (ValueError, OSError, AttributeError):
            code = ""
        return {"error": code if code in ("authorization_pending", "slow_down", "expired_token", "authorization_declined",
                                           "invalid_grant", "bad_verification_code") else f"http_{error.code}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise OutlookError("OUTLOOK_NETWORK") from None


def device_start(client_id: str, write: bool = False) -> dict[str, Any]:
    reply = _post_form(f"{AUTHORITY}/devicecode", {"client_id": client_id, "scope": SCOPES_WRITE if write else SCOPES})
    if "device_code" not in reply:
        raise OutlookError("OUTLOOK_DEVICE_START_FAILED")
    return reply


def device_wait(client_id: str, started: dict[str, Any], sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Poll until the person finishes signing in. Returns the token reply; fixed codes for every other outcome."""
    deadline = clock() + int(started.get("expires_in", 900))
    interval = int(started.get("interval", 5))
    while clock() < deadline:
        sleep(interval)
        reply = _post_form(f"{AUTHORITY}/token", {"client_id": client_id, "device_code": started["device_code"],
                                                   "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if "refresh_token" in reply:
            return reply
        error = reply.get("error")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            raise OutlookError("OUTLOOK_SIGNIN_" + (error or "failed").upper())
    raise OutlookError("OUTLOOK_SIGNIN_TIMEOUT")


def refresh(client_id: str, refresh_token: str) -> dict[str, Any]:
    reply = _post_form(f"{AUTHORITY}/token", {"client_id": client_id, "refresh_token": refresh_token,
                                               "grant_type": "refresh_token"})     # no scope: keep whatever this sign-in was granted
    if "access_token" not in reply:
        raise OutlookError("OUTLOOK_REFRESH_" + str(reply.get("error", "failed")).upper())
    return reply


class Outlook:
    """One mailbox. `save_refresh` is called with the new refresh token whenever Microsoft rotates it."""

    def __init__(self, client_id: str, refresh_token: str, save_refresh: Callable[[str], None] | None = None, timeout: float = 30, sleep: Callable[[float], None] = time.sleep) -> None:
        if not client_id or not refresh_token:
            raise OutlookError("OUTLOOK_CONFIG_INVALID")
        self._client, self._refresh, self._save = client_id, refresh_token, save_refresh
        self._timeout, self._sleep, self._token, self._down = timeout, sleep, None, _down_marker(refresh_token)

    def _access(self):
        if self._token is None:
            reply = refresh(self._client, self._refresh)
            self._token = reply["access_token"]
            if reply.get("refresh_token") and reply["refresh_token"] != self._refresh:
                self._refresh = reply["refresh_token"]
                if self._save:
                    self._save(self._refresh)
        return self._token

    def get(self, url: str, params: dict[str, Any] | None = None, prefer: str = "") -> Any:
        """GET a Graph path (or a full @odata.nextLink). Immutable ids are requested on every call."""
        return self._request("GET", url, params, None, prefer)

    def _request(self, method, url, params=None, body=None, prefer=""):
        if url.startswith("/"):
            url = GRAPH + url + ("?" + urllib.parse.urlencode(params) if params else "")
        elif not url.startswith(GRAPH + "/"):
            raise OutlookError("OUTLOOK_BAD_LINK")                    # never follow a link off Graph with our token
        data = None if body is None else json.dumps(body).encode()
        def build():
            headers = {"Authorization": "Bearer " + self._access(), "Accept": "application/json", "User-Agent": "life-os-v7",
                       "Prefer": 'IdType="ImmutableId"' + (", " + prefer if prefer else "")}
            if data is not None:
                headers["Content-Type"] = "application/json"
            return urllib.request.Request(url, data=data, headers=headers, method=method)
        if self._down and os.path.exists(self._down):
            raise OutlookError("OUTLOOK_UNAVAILABLE_THIS_JOB")
        try:
            return rest.call(build, OutlookError, "OUTLOOK", self._sleep, self._timeout, bad_json="BAD_RESPONSE",
                             codes={401: "OUTLOOK_UNAUTHORIZED"}, honor_retry_after=True)
        except OutlookError as error:
            if self._down and _is_outage(str(error)):
                try:
                    open(self._down, "w").close()
                except OSError:
                    pass                                                        # a marker is a convenience; the error itself is still raised
            raise

    def folder_id(self, name: str, create: bool = False) -> str | None:
        """The id of a top-level mail folder by name; created when asked and missing. None when missing and not creating."""
        quoted = name.replace("'", "''")
        found = self.get("/me/mailFolders", {"$filter": f"displayName eq '{quoted}'", "$select": "id,displayName", "$top": 10})
        for folder in found.get("value") or []:
            if folder.get("displayName") == name and folder.get("id"):
                return folder["id"]
        if not create:
            return None
        return self._request("POST", "/me/mailFolders", None, {"displayName": name})["id"]

    def move(self, message_id: str, folder_id: str) -> bool:
        """Move one message into a folder (repeating it is harmless) and read it back: True only if it is now in that folder."""
        quoted = urllib.parse.quote(message_id, safe="")
        self._request("POST", f"/me/messages/{quoted}/move", None, {"destinationId": folder_id})
        return self.get(f"/me/messages/{quoted}", {"$select": "id,parentFolderId"}).get("parentFolderId") == folder_id

    def _pages(self, url: str, params: dict[str, Any], limit: int, prefer: str = "") -> list[Any]:
        """Every row of a Graph listing, following each @odata.nextLink. A listing that cannot be proven complete (page cap hit, or `limit` rows with more to come) raises instead of returning a partial census."""
        out: list[Any] = []
        for page in range(MAX_PAGES):
            reply = self.get(url, params if page == 0 else None, prefer)
            out += reply.get("value") or []
            url = reply.get("@odata.nextLink")
            if not url:
                return out
            if len(out) >= limit:
                break
        raise OutlookError("OUTLOOK_LISTING_INCOMPLETE")

    def messages(self, folder: str = "inbox", since: str | None = None, limit: int = 5000, fields: str | None = None, time_field: str = "receivedDateTime", prefer: str = "") -> list[Any]:
        """Every message in a folder received at or after `since` (ISO 8601), newest first, following every page. A listing that
        cannot be proven complete (page cap hit with more to come) raises instead of returning a partial census."""
        params = {"$select": fields or MESSAGE_FIELDS, "$top": PAGE_SIZE, "$orderby": f"{time_field} desc"}
        if since:
            params["$filter"] = f"{time_field} ge {since}"
        return self._pages(f"/me/mailFolders/{urllib.parse.quote(folder)}/messages", params, limit, prefer)[:limit]

    def replied_since(self, conversation_id: str, since_iso: str) -> bool:
        """True when Jim sent a message in this conversation after `since_iso` (Sent Items, compared here so the query stays a plain equality). Read-only; ids and times only."""
        if not conversation_id:
            return False
        quoted = str(conversation_id).replace("'", "''")
        reply = self.get("/me/mailFolders/sentitems/messages", {"$select": "id,sentDateTime", "$top": 25, "$filter": f"conversationId eq '{quoted}'"})
        since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        for message in reply.get("value") or []:
            sent = message.get("sentDateTime")
            if sent and datetime.fromisoformat(str(sent).replace("Z", "+00:00")) > since:
                return True
        return False

    def messages_in_category(self, category: str, limit: int = 5000) -> list[Any]:
        """Every message in the mailbox that carries the Outlook category (a name, or several spellings of it: one query per spelling, merged by message id), read-only (the message stays
        where it is), following every page. An enumeration that cannot be proven complete raises instead of returning a partial list."""
        names = [category] if isinstance(category, str) else list(category)
        merged: dict[str, Any] = {}
        for name in names:
            quoted = name.replace("'", "''")
            params = {"$select": MESSAGE_FIELDS + ",categories", "$top": PAGE_SIZE, "$filter": f"categories/any(c:c eq '{quoted}')"}
            found = self._pages("/me/messages", params, limit)
            for message in found:
                merged.setdefault(message["id"], message)
        return list(merged.values())[:limit]

    def events(self, start: str, end: str, limit: int = 2000, fields: str | None = None) -> list[Any]:
        """Every calendar event overlapping [start, end) (ISO 8601, UTC), recurring series expanded into occurrences, times in UTC.
        Like the mail listing, an incomplete enumeration raises."""
        params = {"startDateTime": start, "endDateTime": end, "$top": 100, "$select": fields or EVENT_FIELDS,
                  "$orderby": "start/dateTime"}
        return self._pages("/me/calendarView", params, limit, 'outlook.timezone="UTC"')

    def message_html(self, message_id: str) -> str:
        """The HTML body of one message (content stays in memory only)."""
        reply = self.get(f"/me/messages/{urllib.parse.quote(message_id, safe='')}", {"$select": "id,body"},
                         prefer='outlook.body-content-type="html"')
        body = reply.get("body") or {}
        return body.get("content") or "" if str(body.get("contentType", "")).lower() == "html" else ""
