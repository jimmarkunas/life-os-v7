"""Minimal Microsoft Graph mail client (stdlib only, platform: no product knowledge). Delegated OAuth with refresh tokens (device-code
sign-in once, then silent refresh), immutable message ids, bounded full enumeration. Every error is a fixed code: never a URL, a token,
an address or a response body. Reads retry transient failures; nothing here writes to a mailbox."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

AUTHORITY = "https://login.microsoftonline.com/common/oauth2/v2.0"      # personal Microsoft accounts and work/school tenants
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access Mail.Read Calendars.Read"                       # read-only; cleanup later needs its own consent
TRANSIENT = (429, 500, 502, 503, 504)
MAX_PAGES = 100
PAGE_SIZE = 100
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


def device_start(client_id):
    reply = _post_form(f"{AUTHORITY}/devicecode", {"client_id": client_id, "scope": SCOPES})
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
                                               "grant_type": "refresh_token", "scope": SCOPES})
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

    def get(self, url, params=None):
        """GET a Graph path (or a full @odata.nextLink). Immutable ids are requested on every call."""
        if url.startswith("/"):
            url = GRAPH + url + ("?" + urllib.parse.urlencode(params) if params else "")
        elif not url.startswith(GRAPH + "/"):
            raise OutlookError("OUTLOOK_BAD_LINK")                    # never follow a link off Graph with our token
        for attempt in range(4):
            request = urllib.request.Request(url, headers={"Authorization": "Bearer " + self._access(), "Accept": "application/json",
                                                           "Prefer": 'IdType="ImmutableId"', "User-Agent": "life-os-v7"})
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    return json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as error:
                if error.code in TRANSIENT and attempt < 3:
                    try:
                        wait = min(float(error.headers.get("Retry-After") or 0), 30) if error.headers else 0
                    except (TypeError, ValueError):
                        wait = 0
                    self._sleep(wait or 2 ** (attempt + 1))
                    continue
                if error.code == 401:
                    raise OutlookError("OUTLOOK_UNAUTHORIZED") from None
                raise OutlookError(f"OUTLOOK_HTTP_{error.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt < 3:
                    self._sleep(2 ** (attempt + 1))
                    continue
                raise OutlookError("OUTLOOK_NETWORK") from None
            except ValueError:
                raise OutlookError("OUTLOOK_BAD_RESPONSE") from None
        raise OutlookError("OUTLOOK_NETWORK")

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
