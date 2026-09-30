"""Minimal Gmail REST client (stdlib only). One small surface: list, sender, relabel."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailError(RuntimeError):
    pass


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
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:  # never echo response bodies into logs
            raise GmailError(f"{method} {url.split('?')[0]} -> HTTP {error.code}") from None
        return json.loads(raw) if raw else {}

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

    def sender(self, message_id):
        url = f"{API}/messages/{message_id}?format=metadata&metadataHeaders=From"
        for header in self._request("GET", url).get("payload", {}).get("headers", []):
            if header["name"].lower() == "from":
                return header["value"]
        return ""

    def relabel(self, ids, add=(), remove=()):
        for start in range(0, len(ids), 1000):
            self._request("POST", f"{API}/messages/batchModify", body={
                "ids": ids[start:start + 1000], "addLabelIds": list(add), "removeLabelIds": list(remove)})
