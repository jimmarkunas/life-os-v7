"""Minimal Google Calendar client (stdlib; the retired key path also uses the system openssl).

Service-account auth: the account sees only the calendar shared with it. Inside GitHub Actions it is keyless: the job's
OIDC token is exchanged at Google's Security Token Service (workload identity federation) and then impersonates the
calendar service account, so no key exists. Every error is a fixed code and no token is ever logged."""

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
from lifeos.platform.fixed_code import fixed

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPE_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
MAX_PAGES = 40
# Addresses, not secrets: the workload identity provider the job's OIDC token is exchanged at, and the account it may impersonate.
WIF_AUDIENCE = (
    "//iam.googleapis.com/projects/632113041729/locations/global/workloadIdentityPools/github/providers/life-os-v7"
)
STS_URL = "https://sts.googleapis.com/v1/token"
SERVICE_ACCOUNT = "life-os-v7-calendar@life-os-gmail-508407.iam.gserviceaccount.com"
IAM_URL = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{SERVICE_ACCOUNT}:generateAccessToken"
CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
KEYLESS = "keyless"  # stands in for the key JSON where a caller passes a credential: the keyless path needs none


class GcalError(RuntimeError):
    """The message is always a fixed code."""


def keyless_available(environ=os.environ):
    """True inside a GitHub Actions job that may request an OIDC token (`permissions: id-token: write`)."""
    return bool((environ.get("ACTIONS_ID_TOKEN_REQUEST_URL") or "").strip())


def _mask(value, out):
    """Tell Actions to mask `value` in every later log line (one line, no newlines)."""
    if value and "\n" not in value and "\r" not in value:
        out(f"::add-mask::{value}")


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def openssl_sign(private_key_pem, message):
    """RS256 signature of `message` with the service-account key; the key touches disk only in a private temp file,
    briefly."""
    handle, path = tempfile.mkstemp()
    try:
        os.write(handle, private_key_pem.encode())
        os.close(handle)
        os.chmod(path, 0o600)
        return subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", path], input=message, capture_output=True, check=True, timeout=20
        ).stdout
    except (subprocess.SubprocessError, OSError):
        raise GcalError("GCAL_SIGN_FAILED") from None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class GoogleCalendar:
    def __init__(
        self,
        service_account_json,
        calendar_id,
        timeout=30,
        sleep=time.sleep,
        sign=openssl_sign,
        clock=time.time,
        scope=SCOPE,
        environ=None,
        out=print,
    ):
        self._keyless = service_account_json == KEYLESS
        self._environ = os.environ if environ is None else environ
        self._out = out
        if self._keyless:
            self._email = self._key = None
        else:
            with fixed((ValueError, KeyError, TypeError), GcalError, "GCAL_CONFIG_INVALID"):
                info = json.loads(service_account_json)
                self._email, self._key = info["client_email"], info["private_key"]
        if not calendar_id:
            raise GcalError("GCAL_CONFIG_INVALID")
        self._calendar = urllib.parse.quote(calendar_id, safe="")
        self._timeout, self._sleep, self._sign, self._clock, self._token, self._scope = (
            timeout,
            sleep,
            sign,
            clock,
            None,
            scope,
        )

    @classmethod
    def from_env(cls, environ=os.environ, **kwargs):
        cal = (environ.get("GCAL_CALENDAR_ID") or "").strip()
        if keyless_available(environ):
            if not cal:
                raise GcalError("GCAL_CONFIG_MISSING:GCAL_CALENDAR_ID")
            return cls(KEYLESS, cal, environ=environ, **kwargs)
        raw = (environ.get("GCAL_SERVICE_ACCOUNT_JSON") or "").strip()  # the retired key path, kept one release
        missing = [n for n, v in (("GCAL_SERVICE_ACCOUNT_JSON", raw), ("GCAL_CALENDAR_ID", cal)) if not v]
        if missing:
            raise GcalError("GCAL_CONFIG_MISSING:" + ",".join(missing))
        return cls(raw, cal, **kwargs)

    def _post_json(self, url, doc, headers, code):
        request = urllib.request.Request(
            url,
            method="POST",
            headers={"Content-Type": "application/json", **headers},
            data=json.dumps(doc).encode(),
        )
        with fixed((urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError), GcalError, code):
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read())

    def _keyless_token(self):
        """GitHub OIDC token -> Google STS federated token -> impersonated calendar access token (no key anywhere)."""
        url, bearer = (
            (self._environ.get("ACTIONS_ID_TOKEN_REQUEST_URL") or "").strip(),
            (self._environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN") or "").strip(),
        )
        if not url or not bearer:
            raise GcalError("GCAL_OIDC_UNAVAILABLE")
        request = urllib.request.Request(
            url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"audience": WIF_AUDIENCE}),
            headers={"Authorization": "Bearer " + bearer, "User-Agent": "life-os-v7"},
        )
        with fixed(
            (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, TypeError),
            GcalError,
            "GCAL_OIDC_UNAVAILABLE",
        ):
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                subject = str(json.loads(response.read())["value"])
        _mask(subject, self._out)
        federated = self._post_json(
            STS_URL,
            {
                "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
                "audience": WIF_AUDIENCE,
                "scope": CLOUD_SCOPE,
                "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
                "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt",
                "subjectToken": subject,
            },
            {},
            "GCAL_STS_REFUSED",
        )
        federated = (
            str(federated["access_token"]) if isinstance(federated, dict) and federated.get("access_token") else ""
        )
        if not federated:
            raise GcalError("GCAL_STS_REFUSED")
        _mask(federated, self._out)
        issued = self._post_json(
            IAM_URL,
            {"scope": [self._scope], "lifetime": "3600s"},
            {"Authorization": "Bearer " + federated},
            "GCAL_IAM_REFUSED",
        )
        token = str(issued["accessToken"]) if isinstance(issued, dict) and issued.get("accessToken") else ""
        if not token:
            raise GcalError("GCAL_IAM_REFUSED")
        _mask(token, self._out)
        self._out("gcal auth: keyless ok")
        return token

    def _access(self):
        if self._token is None and self._keyless:
            self._token = self._keyless_token()
        if self._token is None:
            now = int(self._clock())
            head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
            body = _b64(
                json.dumps(
                    {"iss": self._email, "scope": self._scope, "aud": TOKEN_URL, "iat": now, "exp": now + 3000}
                ).encode()
            )
            signing = head + b"." + body
            assertion = (signing + b"." + _b64(self._sign(self._key, signing))).decode()
            request = urllib.request.Request(
                TOKEN_URL,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=urllib.parse.urlencode(
                    {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
                ).encode(),
            )
            with fixed(
                (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError), GcalError, "GCAL_AUTH_FAILED"
            ):
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    self._token = json.loads(response.read())["access_token"]
        return self._token

    def request(self, method, path, params=None, body=None, ok=(200,)):
        url = f"{API}/calendars/{self._calendar}{path}" + (
            "?" + urllib.parse.urlencode(params, doseq=True) if params else ""
        )
        data = None if body is None else json.dumps(body).encode()
        attempts = 4 if method in ("GET", "PUT", "DELETE") else 2
        build = lambda: urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self._access(),
                "Content-Type": "application/json",
                "User-Agent": "life-os-v7",
            },
        )
        return rest.call(
            build, GcalError, "GCAL", self._sleep, self._timeout, attempts=attempts, bad_json="BAD_RESPONSE"
        )

    def list_events(self, time_min, time_max, private_property=None):
        """Every non-cancelled single event in the window, optionally only those carrying a private extended property
        k=v."""
        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
            "showDeleted": "false",
        }
        if private_property:
            params["privateExtendedProperty"] = private_property
        out = []
        for _ in range(MAX_PAGES):
            page = self.request("GET", "/events", params)
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise GcalError("GCAL_LISTING_INCOMPLETE")
            if any(not isinstance(item, dict) for item in page["items"]):
                raise GcalError("GCAL_LISTING_INCOMPLETE")
            out += page["items"]
            token = page.get("nextPageToken")
            if not token:
                return out
            if not isinstance(token, str):
                raise GcalError("GCAL_LISTING_INCOMPLETE")
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
            if str(error) not in ("GCAL_HTTP_404", "GCAL_HTTP_410"):  # already gone is fine
                raise
