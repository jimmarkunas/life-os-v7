"""Minimal Google Calendar client (stdlib + the system openssl for the JWT signature; platform: no product knowledge). Service-account
auth: the account only ever sees the one calendar its owner shared with it. Every error is a fixed code: never a URL, a token, an id
or a response body."""
import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from lifeos.platform import rest

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
MAX_PAGES = 40


class GcalError(RuntimeError):
    """The message is always a fixed code."""


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def openssl_sign(private_key_pem, message):
    """RS256 signature of `message` with the service-account key. The key touches disk only in a private temp file, briefly."""
    handle, path = tempfile.mkstemp()
    try:
        os.write(handle, private_key_pem.encode())
        os.close(handle)
        os.chmod(path, 0o600)
        return subprocess.run(["openssl", "dgst", "-sha256", "-sign", path], input=message, capture_output=True, check=True,
                              timeout=20).stdout
    except (subprocess.SubprocessError, OSError):
        raise GcalError("GCAL_SIGN_FAILED") from None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class GoogleCalendar:
    def __init__(self, service_account_json, calendar_id, timeout=30, sleep=time.sleep, sign=openssl_sign, clock=time.time):
        try:
            info = json.loads(service_account_json)
            self._email, self._key = info["client_email"], info["private_key"]
        except (ValueError, KeyError, TypeError):
            raise GcalError("GCAL_CONFIG_INVALID") from None
        if not calendar_id:
            raise GcalError("GCAL_CONFIG_INVALID")
        self._calendar = urllib.parse.quote(calendar_id, safe="")
        self._timeout, self._sleep, self._sign, self._clock, self._token = timeout, sleep, sign, clock, None

    @classmethod
    def from_env(cls, environ=os.environ, **kwargs):
        raw, cal = (environ.get("GCAL_SERVICE_ACCOUNT_JSON") or "").strip(), (environ.get("GCAL_CALENDAR_ID") or "").strip()
        missing = [n for n, v in (("GCAL_SERVICE_ACCOUNT_JSON", raw), ("GCAL_CALENDAR_ID", cal)) if not v]
        if missing:
            raise GcalError("GCAL_CONFIG_MISSING:" + ",".join(missing))
        return cls(raw, cal, **kwargs)

    def _access(self):
        if self._token is None:
            now = int(self._clock())
            head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
            body = _b64(json.dumps({"iss": self._email, "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 3000}).encode())
            signing = head + b"." + body
            assertion = (signing + b"." + _b64(self._sign(self._key, signing))).decode()
            request = urllib.request.Request(TOKEN_URL, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"},
                                             data=urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                                          "assertion": assertion}).encode())
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    self._token = json.loads(response.read())["access_token"]
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
                raise GcalError("GCAL_AUTH_FAILED") from None
        return self._token

    def request(self, method, path, params=None, body=None, ok=(200,)):
        url = f"{API}/calendars/{self._calendar}{path}" + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        data = None if body is None else json.dumps(body).encode()
        attempts = 4 if method in ("GET", "PUT", "DELETE") else 2          # insert/patch retried once: both are safe to repeat here
        build = lambda: urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self._access(), "Content-Type": "application/json", "User-Agent": "life-os-v7"})
        return rest.call(build, GcalError, "GCAL", self._sleep, self._timeout, attempts=attempts, bad_json="BAD_RESPONSE")

    def list_events(self, time_min, time_max, private_property=None):
        """Every non-cancelled single event in the window (optionally only those carrying a private extended property k=v)."""
        params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "maxResults": 250, "showDeleted": "false"}
        if private_property:
            params["privateExtendedProperty"] = private_property
        out = []
        for _ in range(MAX_PAGES):
            page = self.request("GET", "/events", params)
            out += page.get("items") or []
            token = page.get("nextPageToken")
            if not token:
                return out
            params = {**params, "pageToken": token}
        raise GcalError("GCAL_LISTING_INCOMPLETE")

    def insert(self, event):
        return self.request("POST", "/events", body=event)

    def update(self, event_id, event):
        return self.request("PUT", f"/events/{urllib.parse.quote(event_id, safe='')}", body=event)

    def delete(self, event_id):
        try:
            self.request("DELETE", f"/events/{urllib.parse.quote(event_id, safe='')}")
        except GcalError as error:
            if str(error) not in ("GCAL_HTTP_404", "GCAL_HTTP_410"):          # already gone is fine
                raise
