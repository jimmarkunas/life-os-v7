"""Minimal Microsoft Graph mail client (stdlib only, platform: no product knowledge). Delegated OAuth with refresh tokens (device-code
sign-in once, then silent refresh), immutable message ids, bounded full enumeration. Every error is a fixed code: never a URL, a token,
an address or a response body. Reads retry transient failures. Writes are limited to creating one mail folder and moving a message into
it (both safe to repeat); nothing here ever deletes, sends or edits mail."""
import json
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


def device_start(client_id, write=False):
    reply = _post_form(f"{AUTHORITY}/devicecode", {"client_id": client_id, "scope": SCOPES_WRITE if write else SCOPES})
    if "device_code" not in reply:
        raise OutlookError("OUTLOOK_DEVICE_START_FAILED")
    return reply


def device_wait(client_id, started, sleep=time.sleep, clock=time.monotonic):
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


def refresh(client_id, refresh_token):
    reply = _post_form(f"{AUTHORITY}/token", {"client_id": client_id, "refresh_token": refresh_token,
                                               "grant_type": "refresh_token"})     # no scope: keep whatever this sign-in was granted
    if "access_token" not in reply:
        raise OutlookError("OUTLOOK_REFRESH_" + str(reply.get("error", "failed")).upper())
    return reply


class Outlook:
    """One mailbox. `save_refresh` is called with the new refresh token whenever Microsoft rotates it."""

    def __init__(self, client_id, refresh_token, save_refresh=None, timeout=30, sleep=time.sleep):
        if not client_id or not refresh_token:
            raise OutlookError("OUTLOOK_CONFIG_INVALID")
        self._client, self._refresh, self._save = client_id, refresh_token, save_refresh
        self._timeout, self._sleep, self._token = timeout, sleep, None

    def _access(self):
        if self._token is None:
            reply = refresh(self._client, self._refresh)
            self._token = reply["access_token"]
            if reply.get("refresh_token") and reply["refresh_token"] != self._refresh:
                self._refresh = reply["refresh_token"]
                if self._save:
                    self._save(self._refresh)
        return self._token

    def get(self, url, params=None, prefer=""):
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
        return rest.call(build, OutlookError, "OUTLOOK", self._sleep, self._timeout, bad_json="BAD_RESPONSE",
                         codes={401: "OUTLOOK_UNAUTHORIZED"}, honor_retry_after=True)

    def folder_id(self, name, create=False):
        """The id of a top-level mail folder by name; created when asked and missing. None when missing and not creating."""
        quoted = name.replace("'", "''")
        found = self.get("/me/mailFolders", {"$filter": f"displayName eq '{quoted}'", "$select": "id,displayName", "$top": 10})
        for folder in found.get("value") or []:
            if folder.get("displayName") == name and folder.get("id"):
                return folder["id"]
        if not create:
            return None
        return self._request("POST", "/me/mailFolders", None, {"displayName": name})["id"]

    def move(self, message_id, folder_id):
        """Move one message into a folder (repeating it is harmless) and read it back: True only if it is now in that folder."""
        quoted = urllib.parse.quote(message_id, safe="")
        self._request("POST", f"/me/messages/{quoted}/move", None, {"destinationId": folder_id})
        return self.get(f"/me/messages/{quoted}", {"$select": "id,parentFolderId"}).get("parentFolderId") == folder_id

    def messages(self, folder="inbox", since=None, limit=5000):
        """Every message in a folder received at or after `since` (ISO 8601), newest first, following every page. A listing that
        cannot be proven complete (page cap hit with more to come) raises instead of returning a partial census."""
        params = {"$select": MESSAGE_FIELDS, "$top": PAGE_SIZE, "$orderby": "receivedDateTime desc"}
        if since:
            params["$filter"] = f"receivedDateTime ge {since}"
        out, url = [], f"/me/mailFolders/{urllib.parse.quote(folder)}/messages"
        for page in range(MAX_PAGES):
            reply = self.get(url, params if page == 0 else None)
            out += reply.get("value") or []
            link = reply.get("@odata.nextLink")
            if not link:
                return out[:limit]
            if len(out) >= limit:
                raise OutlookError("OUTLOOK_LISTING_INCOMPLETE")
            url = link
        raise OutlookError("OUTLOOK_LISTING_INCOMPLETE")

    def messages_in_category(self, category, limit=5000):
        """Every message in the mailbox that carries one Outlook category (read-only; the message stays where it is), following every page. An enumeration that
        cannot be proven complete raises instead of returning a partial list."""
        quoted = category.replace("'", "''")
        params = {"$select": MESSAGE_FIELDS + ",categories", "$top": PAGE_SIZE, "$filter": f"categories/any(c:c eq '{quoted}')"}
        out, url = [], "/me/messages"
        for page in range(MAX_PAGES):
            reply = self.get(url, params if page == 0 else None)
            out += reply.get("value") or []
            link = reply.get("@odata.nextLink")
            if not link:
                return out[:limit]
            if len(out) >= limit:
                raise OutlookError("OUTLOOK_LISTING_INCOMPLETE")
            url = link
        raise OutlookError("OUTLOOK_LISTING_INCOMPLETE")

    def events(self, start, end, limit=2000):
        """Every calendar event overlapping [start, end) (ISO 8601, UTC), recurring series expanded into occurrences, times in UTC.
        Like the mail listing, an incomplete enumeration raises."""
        params = {"startDateTime": start, "endDateTime": end, "$top": 100, "$select": EVENT_FIELDS,
                  "$orderby": "start/dateTime"}
        out, url = [], "/me/calendarView"
        for page in range(MAX_PAGES):
            reply = self.get(url, params if page == 0 else None, prefer='outlook.timezone="UTC"')
            out += reply.get("value") or []
            link = reply.get("@odata.nextLink")
            if not link:
                return out
            if len(out) >= limit:
                raise OutlookError("OUTLOOK_LISTING_INCOMPLETE")
            url = link
        raise OutlookError("OUTLOOK_LISTING_INCOMPLETE")

    def message_html(self, message_id):
        """The HTML body of one message (content stays in memory only)."""
        reply = self.get(f"/me/messages/{urllib.parse.quote(message_id, safe='')}", {"$select": "id,body"},
                         prefer='outlook.body-content-type="html"')
        body = reply.get("body") or {}
        return body.get("content") or "" if str(body.get("contentType", "")).lower() == "html" else ""
