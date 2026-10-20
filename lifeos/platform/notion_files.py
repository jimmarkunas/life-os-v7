"""Notion file upload (platform, no job knowledge; LIFE-87 D234). One small file in one part: create the upload, send the bytes as multipart/form-data, and
require Notion to report it "uploaded". -> the upload id, which a file block can then reference. Fixed error codes only; the token and bytes are never logged."""
import json
import secrets
import urllib.error
import urllib.request

from lifeos.platform.notion_client import API, VERSION, NotionError

MAX_SINGLE_PART = 20 * 1024 * 1024          # Notion's single-part limit


def _send(token, path, body, content_type, opener, timeout=60):
    request = urllib.request.Request(API + path, method="POST", data=body,
                                     headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": content_type})
    try:
        with opener(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise NotionError(f"NOTION_UPLOAD_HTTP_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise NotionError("NOTION_UPLOAD_NETWORK") from None


def upload(token, filename, data, content_type, opener=urllib.request.urlopen):
    if not data or len(data) > MAX_SINGLE_PART:
        raise NotionError("NOTION_UPLOAD_SIZE")
    created = _send(token, "/file_uploads", json.dumps({"mode": "single_part", "filename": filename, "content_type": content_type}).encode(), "application/json", opener)
    upload_id = created.get("id")
    if not isinstance(upload_id, str) or not upload_id:
        raise NotionError("NOTION_UPLOAD_NOT_CREATED")
    boundary = "lifeos-" + secrets.token_hex(12)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode() \
        + data + f"\r\n--{boundary}--\r\n".encode()
    sent = _send(token, f"/file_uploads/{upload_id}/send", body, f"multipart/form-data; boundary={boundary}", opener)
    if sent.get("status") != "uploaded":
        raise NotionError("NOTION_UPLOAD_INCOMPLETE")
    return upload_id
