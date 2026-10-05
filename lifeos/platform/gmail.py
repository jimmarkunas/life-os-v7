"""Minimal Gmail REST client (stdlib only): bounded listing, message reads and explicit label changes."""
import base64
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_LIST_PAGES = 100


RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}


class GmailError(RuntimeError):
    pass


def _reason(error):
    """Google's short error reason code (e.g. rateLimitExceeded), or '' - never free text."""
    try:
        reason = json.loads(error.read())["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError, OSError):
        return ""
    return reason if re.fullmatch(r"[A-Za-z]{1,40}", str(reason)) else ""


class Gmail:
    def __init__(self, client_id, client_secret, refresh_token, timeout=30):
        self._creds = (client_id, client_secret, refresh_token)
        self._timeout = timeout
        self._token = None

    @classmethod
    def from_env(cls):
        try:
            return cls(os.environ["GMAIL_OAUTH_CLIENT_ID"], os.environ["GMAIL_OAUTH_CLIENT_SECRET"],
                       os.environ["GMAIL_OAUTH_REFRESH_TOKEN"])
        except KeyError as missing:
            raise GmailError(f"missing secret {missing}") from None

    def _request(self, method, url, body=None, form=None, auth=True):
        data, headers = None, {}
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if auth:
            headers["Authorization"] = "Bearer " + self._access_token()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    raw = response.read()
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as error:
                reason = _reason(error)
                transient = error.code in (429, 500, 502, 503, 504) or reason in RATE_REASONS
                if transient and attempt < 3:
                    time.sleep(2 ** (attempt + 1))
                    continue
                # only a short enumerated reason code is surfaced; bodies never reach logs
                raise GmailError(f"{method} {url.split('?')[0].split('/messages')[0]} -> HTTP {error.code} {reason}") from None

    def _access_token(self):
        if self._token is None:
            client_id, client_secret, refresh_token = self._creds
            reply = self._request("POST", TOKEN_URL, auth=False, form={
                "client_id": client_id, "client_secret": client_secret,
                "refresh_token": refresh_token, "grant_type": "refresh_token"})
            self._token = reply["access_token"]
        return self._token

    def label_id(self, name, create=False):
        for label in self._request("GET", f"{API}/labels").get("labels", []):
            if label["name"] == name:
                return label["id"]
        if not create:
            raise GmailError(f"label not found: {name}")
        return self._request("POST", f"{API}/labels", body={"name": name})["id"]

    def list_ids(self, query, limit=5000):
        ids, token = [], None
        while len(ids) < limit:
            params = {"q": query, "maxResults": 500}
            if token:
                params["pageToken"] = token
            page = self._request("GET", f"{API}/messages?" + urllib.parse.urlencode(params))
            ids += [m["id"] for m in page.get("messages", [])]
            token = page.get("nextPageToken")
            if not token:
                break
        return ids[:limit]

    def list_ids_complete(self, query, limit):
        """Return the complete matching ID set, or fail when it exceeds the caller's bound."""
        if not isinstance(limit, int) or limit < 1:
            raise GmailError("GMAIL_LIST_LIMIT_INVALID")
        ids, token, seen_tokens, seen_ids = [], None, set(), set()
        for _ in range(MAX_LIST_PAGES):
            params = {"q": query, "maxResults": min(500, limit + 1 - len(ids))}
            if token:
                if token in seen_tokens:
                    raise GmailError("GMAIL_LISTING_INCOMPLETE")
                seen_tokens.add(token)
                params["pageToken"] = token
            page = self._request("GET", f"{API}/messages?" + urllib.parse.urlencode(params))
            if not isinstance(page, dict):
                raise GmailError("GMAIL_LISTING_INCOMPLETE")
            messages = page.get("messages", [])
            if not isinstance(messages, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in messages):
                raise GmailError("GMAIL_LISTING_INCOMPLETE")
            for item in messages:
                if item["id"] in seen_ids:
                    raise GmailError("GMAIL_LISTING_INCOMPLETE")
                seen_ids.add(item["id"])
                ids.append(item["id"])
                if len(ids) > limit:
                    raise GmailError("GMAIL_LIST_LIMIT_EXCEEDED")
            token = page.get("nextPageToken")
            if token is None or token == "":
                return ids
            if not isinstance(token, str):
                raise GmailError("GMAIL_LISTING_INCOMPLETE")
        raise GmailError("GMAIL_LISTING_INCOMPLETE")

    def message_record(self, message_id):
        """Read source fields and labels; decoded content stays in memory and errors never include message data."""
        full = self._request("GET", f"{API}/messages/{urllib.parse.quote(str(message_id), safe='')}?format=full")
        if not isinstance(full, dict) or not isinstance(full.get("payload"), dict):
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        headers = full["payload"].get("headers") or []
        if not isinstance(headers, list) or any(not isinstance(header, dict) for header in headers):
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        values = {}
        for header in headers:
            name, value = header.get("name"), header.get("value")
            if isinstance(name, str) and isinstance(value, str):
                values.setdefault(name.lower(), value)
        raw_date = full.get("internalDate")
        try:
            received = datetime.fromtimestamp(int(raw_date) / 1000, timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE") from None
        labels = full.get("labelIds") or []
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        return {"id": str(message_id), "sender": values.get("from", ""), "subject": values.get("subject", ""),
                "received_at": received, "body_text": _find_text(full["payload"]), "label_ids": labels}

    SENT_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Auto-Submitted", "Precedence", "List-Id", "List-Unsubscribe")

    def sent_record(self, message_id):
        """Metadata of one message (headers and a short preview, never the body): sender, recipients, subject, sent time, thread id, bulk-mail markers, labels."""
        query = "&".join(["format=metadata"] + ["metadataHeaders=" + name for name in self.SENT_HEADERS])
        full = self._request("GET", f"{API}/messages/{urllib.parse.quote(str(message_id), safe='')}?{query}")
        if not isinstance(full, dict) or not isinstance(full.get("payload"), dict):
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE")
        values = {}
        for header in full["payload"].get("headers") or []:
            if isinstance(header, dict) and isinstance(header.get("name"), str) and isinstance(header.get("value"), str):
                values.setdefault(header["name"].lower(), header["value"])
        try:
            sent = datetime.fromtimestamp(int(full.get("internalDate")) / 1000, timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            raise GmailError("GMAIL_MESSAGE_INCOMPLETE") from None
        labels = full.get("labelIds") if isinstance(full.get("labelIds"), list) else []
        return {"id": str(message_id), "thread": str(full.get("threadId") or ""), "sender": values.get("from", ""), "to": values.get("to", ""), "cc": values.get("cc", ""),
                "subject": values.get("subject", ""), "snippet": str(full.get("snippet") or ""), "sent_at": sent, "labels": labels,
                "bulk": any(values.get(k) for k in ("list-id", "list-unsubscribe")) or values.get("precedence", "").lower() in ("bulk", "list", "junk")
                or values.get("auto-submitted", "no").lower() not in ("", "no")}

    def profile_address(self):
        """The mailbox's own address (used only in memory, to tell self-sends from real recipients)."""
        reply = self._request("GET", f"{API}/profile")
        address = reply.get("emailAddress") if isinstance(reply, dict) else None
        if not isinstance(address, str) or "@" not in address:
            raise GmailError("GMAIL_PROFILE_INCOMPLETE")
        return address.lower()

    def message_labels(self, message_id):
        path = f"{API}/messages/{urllib.parse.quote(str(message_id), safe='')}?format=minimal"
        result = self._request("GET", path)
        labels = result.get("labelIds") if isinstance(result, dict) else None
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise GmailError("GMAIL_LABEL_READBACK_INCOMPLETE")
        return labels

    def apply_amazon(self, message_id, amazon_label_id):
        """Archive one accepted message under the existing Amazon label; never create labels or delete mail."""
        if not isinstance(amazon_label_id, str) or not amazon_label_id or amazon_label_id == "INBOX":
            raise GmailError("GMAIL_AMAZON_LABEL_INVALID")
        message = urllib.parse.quote(str(message_id), safe="")
        return self._request("POST", f"{API}/messages/{message}/modify",
                             body={"addLabelIds": [amazon_label_id], "removeLabelIds": ["INBOX"]})

    def trash(self, message_id):
        """Move one message to the Gmail Trash (recoverable for 30 days); never a permanent delete."""
        return self._request("POST", f"{API}/messages/{urllib.parse.quote(str(message_id), safe='')}/trash")

    def sender(self, message_id):
        url = f"{API}/messages/{message_id}?format=metadata&metadataHeaders=From"
        for header in self._request("GET", url).get("payload", {}).get("headers", []):
            if header["name"].lower() == "from":
                return header["value"]
        return ""

    def message(self, message_id):
        """Return (sender header, html body, received epoch seconds) for one message. Content stays in memory only."""
        full = self._request("GET", f"{API}/messages/{message_id}?format=full")
        payload = full.get("payload", {})
        sender = next((h["value"] for h in payload.get("headers", []) if h["name"].lower() == "from"), "")
        return sender, _find_html(payload), int(full.get("internalDate", 0)) // 1000

    def relabel(self, ids, add=(), remove=()):
        for start in range(0, len(ids), 1000):
            self._request("POST", f"{API}/messages/batchModify", body={
                "ids": ids[start:start + 1000], "addLabelIds": list(add), "removeLabelIds": list(remove)})


def _find_html(part):
    """Depth-first search of a Gmail payload for the text/html part; decoded from base64url."""
    if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "=" * (-len(part["body"]["data"]) % 4)).decode("utf-8", "replace")
    for child in part.get("parts", []) or []:
        found = _find_html(child)
        if found:
            return found
    return ""


class _TextFromHtml(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pieces, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self.hidden += 1
        elif tag.lower() in ("br", "p", "div", "li", "tr") and self.hidden == 0:
            self.pieces.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style") and self.hidden:
            self.hidden -= 1
        elif tag.lower() in ("p", "div", "li", "tr") and self.hidden == 0:
            self.pieces.append("\n")

    def handle_data(self, data):
        if self.hidden == 0:
            self.pieces.append(data)


def _find_text(part):
    """Prefer a text/plain alternative and fall back to readable HTML text."""
    plain, html = [], []

    def visit(node):
        if not isinstance(node, dict):
            return
        mime = node.get("mimeType")
        data = (node.get("body") or {}).get("data")
        if mime in ("text/plain", "text/html") and data:
            try:
                decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            except (ValueError, TypeError):
                raise GmailError("GMAIL_BODY_DECODE_FAILED") from None
            (plain if mime == "text/plain" else html).append(decoded)
        for child in node.get("parts") or []:
            visit(child)

    visit(part)
    if plain:
        return "\n".join(plain)
    if not html:
        return ""
    parser = _TextFromHtml()
    try:
        parser.feed("\n".join(html))
        parser.close()
    except Exception:
        raise GmailError("GMAIL_BODY_DECODE_FAILED") from None
    return " ".join(" ".join(parser.pieces).split())
