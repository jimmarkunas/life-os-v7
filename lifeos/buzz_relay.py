"""Scoped Buzz relay client: reads via POST /query, writes via POST /events (the surface Block's `buzz` CLI uses). The transport is injectable so tests never touch a network.
Authorization is explicit: a community needs a configured relay URL and its own scoped key; a write also needs that exact (community, channel) in `writes`. Errors are fixed codes."""
import hashlib, json, re, threading, urllib.error, urllib.request
from lifeos import buzz_nip98 as nip98

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TOKEN = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
URL = re.compile(r"^https://[a-z0-9-]+\.communities\.buzz\.xyz$")
COMMUNITIES = ("life-os", "bytalos")
MAX_TEXT, MAX_CACHE = 4000, 256


class BuzzError(Exception):
    def __init__(self, code, **detail):
        super().__init__(code)
        self.code, self.detail = code, detail


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def urllib_transport(method, url, headers, body, timeout=15):
    try:
        with urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, body, headers, method=method), timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Relay:
    def __init__(self, urls, keys, writes=(), transport=urllib_transport):
        self.urls, self.keys, self.writes, self.send, self.sent = urls, keys, set(writes), transport, {}
        self.lock = threading.Lock()  # one writer at a time: precheck, event choice and submit are one atomic step

    def _call(self, community, path, payload):
        if community not in COMMUNITIES or community not in self.urls:
            raise BuzzError("community_not_authorized")
        if community not in self.keys:
            raise BuzzError("key_not_configured")
        url, body = self.urls[community] + path, json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Authorization": nip98.auth_header(self.keys[community], "POST", url, body), "Content-Type": "application/json"}
        try:
            status, raw = self.send("POST", url, headers, body)
        except Exception:
            raise BuzzError("relay_unreachable") from None
        if status >= 500 or status == 429:
            raise BuzzError("relay_error", status=status)
        if status >= 400:
            raise BuzzError("relay_refused", status=status)
        try:
            return json.loads(raw)
        except ValueError:
            raise BuzzError("relay_bad_response") from None

    def _query(self, community, flt):
        out = self._call(community, "/query", [flt])
        if not isinstance(out, list):
            raise BuzzError("relay_bad_response")
        return out

    @staticmethod
    def _tag(e, name):
        return next((t[1] for t in e.get("tags", []) if len(t) > 1 and t[0] == name), None)

    def _view(self, e):
        return {"event_id": e.get("id"), "pubkey": e.get("pubkey"), "created_at": e.get("created_at"), "channel": self._tag(e, "h"),
                "actor": self._tag(e, "actor"), "untrusted_text": str(e.get("content", ""))[:MAX_TEXT]}

    @staticmethod
    def _ok(value, pattern):
        if not isinstance(value, str) or not pattern.match(value):
            raise BuzzError("invalid_argument")
        return value

    @staticmethod
    def _limit(limit, default):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            if limit is not None:
                raise BuzzError("invalid_argument")
            return default
        return limit

    def list_channels(self, community, limit=None):
        evs = self._query(community, {"kinds": [39000], "limit": self._limit(limit, 50)})
        return [{"channel_id": self._tag(e, "d"), "name": self._tag(e, "name")} for e in evs]

    def get_messages(self, community, channel, limit=None):
        flt = {"kinds": [9], "#h": [self._ok(channel, UUID)], "limit": self._limit(limit, 20)}
        return [self._view(e) for e in self._query(community, flt)]

    def search_messages(self, community, query, channel=None, limit=None):
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise BuzzError("invalid_argument")
        flt = {"kinds": [9], "search": query, "limit": self._limit(limit, 20)}
        if channel is not None:
            flt["#h"] = [self._ok(channel, UUID)]
        return [self._view(e) for e in self._query(community, flt)]

    def post_message(self, community, channel, text, idempotency_key):
        self._ok(channel, UUID), self._ok(idempotency_key, TOKEN)
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
            raise BuzzError("invalid_argument")
        if community not in COMMUNITIES or community not in self.urls:
            raise BuzzError("community_not_authorized")
        if (community, channel) not in self.writes:
            raise BuzzError("write_disabled")
        if community not in self.keys:
            raise BuzzError("key_not_configured")
        actor = "key:" + nip98.pubkey(self.keys[community])[:16]  # fixed by the server's own signing key; never caller-supplied
        with self.lock:
            return self._post(community, channel, text, actor, idempotency_key)

    def _post(self, community, channel, text, actor, idempotency_key):
        digest = hashlib.sha256(f"{community}|{channel}|{actor}|{idempotency_key}|{text}".encode()).hexdigest()[:32]
        prior = self.sent.get((community, idempotency_key))
        if prior and prior[0] != digest:
            raise BuzzError("idempotency_conflict")
        try:  # read before write: a retry after an ambiguous outcome must never duplicate
            found = self._query(community, {"kinds": [9], "#h": [channel], "#i": [digest], "limit": 1})
        except BuzzError as e:
            if e.code in ("community_not_authorized", "key_not_configured"):
                raise
            raise BuzzError("precheck_unavailable") from None
        if found:
            return {"status": "already_delivered", "event_id": found[0].get("id"), "actor": actor}
        ev = prior[1] if prior else nip98.event(self.keys[community], 9, [["h", channel], ["actor", actor], ["i", digest]], text)
        self.sent[(community, idempotency_key)] = (digest, ev)
        while len(self.sent) > MAX_CACHE:
            self.sent.pop(next(iter(self.sent)))
        try:
            ack = self._call(community, "/events", ev)
        except BuzzError as e:
            if e.code in ("relay_error", "relay_unreachable"):
                raise BuzzError("delivery_unknown", event_id=ev["id"], retryable=False, next="retry with the same idempotency_key; it reads before resending") from None
            raise
        if not isinstance(ack, dict) or ack.get("accepted") is not True or ack.get("event_id") != ev["id"]:
            raise BuzzError("delivery_unknown", event_id=ev["id"], retryable=False, next="read the channel before any retry")
        return {"status": "sent_not_read_back", "event_id": ev["id"], "pubkey": ev["pubkey"], "actor": actor, "community": community, "channel": channel}
