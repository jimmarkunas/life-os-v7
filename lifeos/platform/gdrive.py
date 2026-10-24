"""Minimal Google Drive client (stdlib only; platform: no job knowledge). Jim, Oct 24 2026: résumés live in Drive as real .docx files, directly in
My Drive / LIFE OS / Résumés, with no hand moving ("full", Oct 24): the credential is the full `drive` scope, because the narrower drive.file cannot
see the LIFE OS folder Jim made himself. This client still only ever finds that one folder (exactly one "LIFE OS" folder at the top of My Drive),
creates or finds Résumés inside it, uploads, reads back and locks its own uploads. Files are uploaded as .docx and never converted to Google Docs,
so their bytes and checksum stay fixed. Every error is a fixed code: never a URL, a token, an id or a response body. The credential is one JSON
object {"client_id", "client_secret", "refresh_token"} (the Edge stores it encrypted as `gdrive`)."""
import hashlib
import json
import secrets
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive"                      # Jim, Oct 24: "full" (see the docstring)
FOLDER_MIME = "application/vnd.google-apps.folder"
HOME_NAME, FOLDER_NAME, FOLDER_TAG = "LIFE OS", "Résumés", "resumes"   # My Drive / LIFE OS / Résumés
FIELDS = "id,name,md5Checksum,webViewLink,trashed,mimeType,appProperties,contentRestrictions"
MAX_FILE = 20 * 1024 * 1024


class DriveError(RuntimeError):
    """The message is always a fixed code."""


def credential(raw):
    """The stored credential -> (client_id, client_secret, refresh_token), or DriveError."""
    try:
        info = json.loads(raw)
        out = tuple(info[k] for k in ("client_id", "client_secret", "refresh_token"))
    except (ValueError, KeyError, TypeError):
        raise DriveError("GDRIVE_CONFIG_INVALID") from None
    if not all(isinstance(v, str) and v for v in out):
        raise DriveError("GDRIVE_CONFIG_INVALID")
    return out


def _quote(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


class Drive:
    def __init__(self, raw, opener=urllib.request.urlopen, timeout=30):
        self._creds, self._open, self._timeout, self._token = credential(raw), opener, timeout, None

    def _send(self, method, url, data=None, content_type=None, auth=True):
        headers = {"Content-Type": content_type} if content_type else {}
        if auth:
            headers["Authorization"] = "Bearer " + self._access_token()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raise DriveError(f"GDRIVE_HTTP_{error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise DriveError("GDRIVE_NETWORK") from None

    def _json(self, method, url, body):
        return self._send(method, url, json.dumps(body).encode(), "application/json")

    def _access_token(self):
        if self._token is None:
            client_id, client_secret, refresh = self._creds
            reply = self._send("POST", TOKEN_URL, urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret,
                               "refresh_token": refresh, "grant_type": "refresh_token"}).encode(), "application/x-www-form-urlencoded", auth=False)
            token = reply.get("access_token")
            if not isinstance(token, str) or not token:
                raise DriveError("GDRIVE_TOKEN_REFUSED")
            if "scope" in reply and SCOPE not in str(reply["scope"]).split():
                raise DriveError("GDRIVE_SCOPE_NARROW")                   # the stored grant predates "full": rerun the Drive sign-in (docs/DRIVE_RESUMES.md)
            self._token = token
        return self._token

    def find(self, query, order="createdTime"):
        reply = self._send("GET", API + "?" + urllib.parse.urlencode({"q": query + " and trashed=false", "spaces": "drive", "orderBy": order,
                                                                       "pageSize": 100, "fields": f"files({FIELDS})"}))
        files = reply.get("files")
        if not isinstance(files, list):
            raise DriveError("GDRIVE_OFF_CONTRACT")
        return files

    def folder(self):
        """My Drive / LIFE OS / Résumés -> its id. LIFE OS must be exactly one folder at the top of My Drive (never guessed, never created); Résumés is
        found inside it (the oldest if there are several) or created there once, tagged."""
        homes = self.find(f"mimeType='{FOLDER_MIME}' and name='{HOME_NAME}' and 'root' in parents")
        if len(homes) != 1:
            raise DriveError("GDRIVE_LIFE_OS_FOLDER_" + ("MISSING" if not homes else "AMBIGUOUS"))
        home = homes[0]["id"]
        names = " or ".join(f"name='{n}'" for n in dict.fromkeys(unicodedata.normalize(f, FOLDER_NAME) for f in ("NFC", "NFD")))   # a Mac may store the accents decomposed
        found = self.find(f"mimeType='{FOLDER_MIME}' and ({names}) and '{_quote(home)}' in parents")
        if found:
            return found[0]["id"]
        made = self._json("POST", API + "?fields=id", {"name": FOLDER_NAME, "mimeType": FOLDER_MIME, "parents": [home], "appProperties": {"lifeos": FOLDER_TAG}})
        if not isinstance(made.get("id"), str):
            raise DriveError("GDRIVE_OFF_CONTRACT")
        return made["id"]

    def upload(self, folder_id, name, data, mime, tags):
        """One file as itself (never converted) -> its metadata, checked: the checksum Drive reports must be the bytes sent."""
        if not data or len(data) > MAX_FILE:
            raise DriveError("GDRIVE_SIZE")
        boundary = "lifeos-" + secrets.token_hex(12)
        meta = json.dumps({"name": name, "parents": [folder_id], "mimeType": mime, "appProperties": dict(tags)}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n").encode() + meta + \
            f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + data + f"\r\n--{boundary}--\r\n".encode()
        made = self._send("POST", UPLOAD + "?" + urllib.parse.urlencode({"uploadType": "multipart", "fields": FIELDS}), body,
                          f"multipart/related; boundary={boundary}")
        if made.get("md5Checksum") != hashlib.md5(data).hexdigest() or not str(made.get("webViewLink", "")).startswith("https://"):
            raise DriveError("GDRIVE_UPLOAD_UNVERIFIED")
        return made

    def get(self, file_id):
        return self._send("GET", f"{API}/{urllib.parse.quote(file_id, safe='')}?fields={FIELDS}")

    def tagged(self, key, value):
        """LIFE OS's own files carrying this tag (e.g. every résumé built for one job), oldest first."""
        return self.find(f"appProperties has {{ key='{_quote(key)}' and value='{_quote(value)}' }}")

    def lock(self, file_id, reason):
        """Make the file read-only in Drive (a content restriction): what was sent cannot change afterwards. -> the file's metadata."""
        return self._json("PATCH", f"{API}/{urllib.parse.quote(file_id, safe='')}?fields={FIELDS}",
                          {"contentRestrictions": [{"readOnly": True, "reason": reason[:100]}]})


def locked(meta):
    return any(isinstance(r, dict) and r.get("readOnly") is True for r in meta.get("contentRestrictions") or [])
