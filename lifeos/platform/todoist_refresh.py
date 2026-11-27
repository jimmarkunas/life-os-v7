"""The one refresher per Todoist app: keeps the rotating refresh token safe and the one-hour access token fresh (REQ-88 WP06).

Run by the Oracle ticker's `watch` timer, stdlib only. Per app (read: `data:read`, write: `data:read_write`) it reads the token record
from Bitwarden project EDGE/OPENCLAW, and when under 30 minutes remain it exchanges the refresh token, saves the new record BEFORE it
is used (a recovery file when the vault write fails, flushed first next run), then publishes the access token to project ALERTS. Todoist
revokes every token when a consumed refresh token is reused after 60 seconds, so there is exactly one refresher and it never reuses one.
An unset item is not consented yet and is skipped quietly; a grant whose scope is not exactly the app's is never published. Fixed codes only."""

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://api.todoist.com/oauth/access_token"
MARGIN_S = 30 * 60
APPS = {
    "read": {
        "scope": "data:read",
        "client_id": "a134c1bf-22a7-4592-9214-b50f014be803",
        "client_secret": "20f31540-cdbb-4ccc-8bab-b50f014be825",
        "token": "bb991960-ada2-4353-8e5c-b50f014be841",
        "access": "3accb8df-ba18-4b2f-b8fc-b50f014be86c",
    },
    "write": {
        "scope": "data:read_write",
        "client_id": "04839013-24cc-4864-ae6d-b50f014be891",
        "client_secret": "f617ed8a-ae1b-451b-b5da-b50f014c5a8e",
        "token": "9540ccbf-3eb7-4839-89fd-b50f014c5ab0",
        "access": "bfebe8b5-2b0a-4916-b6f8-b50f014c5ad0",
    },
}  # Bitwarden item ids (ids are not secrets): lifeos/edge/TODOIST_{READ,WRITE}_{CLIENT_ID,CLIENT_SECRET,TOKEN}, lifeos/github-actions/TODOIST_{READ,WRITE}_ACCESS
UNSET = "unset"


class RefreshError(RuntimeError):
    """The message is always a fixed lower-case code."""


def _post(form, opener):
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(form).encode(),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "life-os-v7",
        },
    )
    try:
        with opener(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise RefreshError("reauth_required" if error.code in (400, 401) else f"http_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RefreshError("network") from None
    except ValueError:
        raise RefreshError("bad_response") from None


def _record(value):
    try:
        doc = json.loads(value)
        if (
            doc.get("v") == 1
            and isinstance(doc.get("refresh_token"), str)
            and isinstance(doc.get("expires_at"), (int, float))
        ):
            return doc
    except (ValueError, AttributeError):
        pass
    raise RefreshError("record_unreadable")


def _save(vault, item, value, sleep):
    for attempt in range(3):
        try:
            vault.put(item, value)
            return True
        except Exception:  # noqa: BLE001 - a fixed outcome only
            if attempt < 2:
                sleep(0.5 * (attempt + 1))
    return False


def refresh(app, vault, now, opener=urllib.request.urlopen, recovery=None, sleep=lambda s: None):
    """-> a fixed result word: `not_consented`, `fresh`, `refreshed`, `refreshed_recovery_file`, or raises RefreshError.

    `vault.get(item_id)` / `vault.put(item_id, value)`; `recovery` is a small object with read() -> str|None, write(str), clear()
    (a 0600 file in a private state directory); `now` is epoch seconds."""
    ids = APPS[app]
    if (
        recovery is not None and recovery.read()
    ):  # a record Todoist already issued but the vault never kept: keep it first
        if not _save(vault, ids["token"], recovery.read(), sleep):
            raise RefreshError("vault_unwritable")
        recovery.clear()
    raw = vault.get(ids["token"])
    if raw == UNSET:
        return "not_consented"
    record = _record(raw)
    if record["expires_at"] - now > MARGIN_S and vault.get(ids["access"]) not in (UNSET, ""):
        return "fresh"
    reply = _post(
        {
            "client_id": vault.get(ids["client_id"]),
            "client_secret": vault.get(ids["client_secret"]),
            "grant_type": "refresh_token",
            "refresh_token": record["refresh_token"],
        },
        opener,
    )
    access = reply.get("access_token")
    if not isinstance(access, str) or not access:
        raise RefreshError("bad_response")
    if not reply.get(
        "refresh_token"
    ):  # the grace-window answer: another refresher already rotated, so use what it kept
        again = vault.get(ids["token"])
        if again != raw and again != UNSET and _record(again)["expires_at"] - now > MARGIN_S:
            return "fresh"
        raise RefreshError("rotation_race")
    ttl = reply.get("expires_in")
    new = json.dumps(
        {
            "v": 1,
            "refresh_token": reply["refresh_token"],
            "expires_at": now + (int(ttl) if isinstance(ttl, (int, float)) and ttl > 0 else 3600),
        }
    )
    saved = _save(vault, ids["token"], new, sleep)
    if not saved:
        if recovery is None:
            raise RefreshError("vault_unwritable")
        recovery.write(new)
    scopes = set(str(reply.get("scope") or "").replace(",", " ").split())
    if scopes != {ids["scope"]}:  # never publish an access token whose scope is not exactly this app's
        raise RefreshError("scope_mismatch")
    if not _save(vault, ids["access"], access, sleep):
        raise RefreshError("access_unwritable")
    return "refreshed" if saved else "refreshed_recovery_file"


def exchange_code(app, code, vault, now, opener=urllib.request.urlopen, redirect="http://localhost"):
    """First consent: swap the one-time code for the first record and access token, saving both. -> `consented`."""
    ids = APPS[app]
    if vault.get(ids["token"]) != UNSET:
        raise RefreshError("already_consented")
    reply = _post(
        {
            "client_id": vault.get(ids["client_id"]),
            "client_secret": vault.get(ids["client_secret"]),
            "code": code,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
        opener,
    )
    access, rt, ttl = reply.get("access_token"), reply.get("refresh_token"), reply.get("expires_in")
    if not (isinstance(access, str) and access and isinstance(rt, str) and rt):
        raise RefreshError("bad_response")
    scopes = set(str(reply.get("scope") or "").replace(",", " ").split())
    record = json.dumps(
        {
            "v": 1,
            "refresh_token": rt,
            "expires_at": now + (int(ttl) if isinstance(ttl, (int, float)) and ttl > 0 else 3600),
        }
    )
    if not _save(vault, ids["token"], record, lambda s: None):
        raise RefreshError("vault_unwritable")
    if scopes != {ids["scope"]}:
        raise RefreshError("scope_mismatch")
    if not _save(vault, ids["access"], access, lambda s: None):
        raise RefreshError("access_unwritable")
    return "consented"


class FileRecovery:
    """A 0600 file in a private state directory holding the one record the vault failed to keep."""

    def __init__(self, path):
        self.path = path

    def read(self):
        try:
            with open(self.path) as handle:
                return handle.read() or None
        except FileNotFoundError:
            return None

    def write(self, value):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(value)

    def clear(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


class BwsVault:
    """`bws` items by id, read and edited with the ticker's Bitwarden machine-account token (never an argument)."""

    def __init__(self, token, run=subprocess.run, environ=None):
        self.token, self.run, self.environ = token, run, environ

    def get(self, item):
        from lifeos.platform import alert_store  # noqa: PLC0415 - installed beside this module on Oracle

        try:
            doc = json.loads(
                alert_store._bws(self.token, ["secret", "get", item, "-o", "json"], self.run, self.environ)
            )
            if doc.get("id") != item or not isinstance(doc.get("value"), str):
                raise ValueError("wrong item")
            return doc["value"]
        except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
            raise RefreshError("vault_unreadable") from None

    def put(self, item, value):
        from lifeos.platform import alert_store  # noqa: PLC0415

        alert_store._bws(self.token, ["secret", "edit", item, "--value", value, "-o", "none"], self.run, self.environ)


def refresh_all(token, environ=None, now=None, opener=urllib.request.urlopen, run=subprocess.run):
    """-> {app: fixed result word} for both apps; one app failing never stops the other. The recovery file lives in the
    unit's state directory (`STATE_DIRECTORY`); without one a vault outage after a rotation raises vault_unwritable."""
    environ = environ if environ is not None else os.environ
    state = (environ.get("STATE_DIRECTORY") or "").split(":")[0]
    vault, out = BwsVault(token, run, environ), {}
    for app in APPS:
        try:
            out[app] = refresh(
                app,
                vault,
                now if now is not None else int(time.time()),
                opener,
                FileRecovery(os.path.join(state, f"todoist-{app}.json")) if state else None,
                time.sleep,
            )
        except RefreshError as error:
            out[app] = str(error)
        except Exception as error:  # noqa: BLE001 - a fixed word only
            out[app] = "failed_" + type(error).__name__.lower()[:24]
    return out
