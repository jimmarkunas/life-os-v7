"""Scoped Buzz relay client: reads via POST /query, writes via POST /events (the surface Block's `buzz` CLI uses). The transport is injectable so tests never touch a network.
Authorization is explicit: a community needs a configured relay URL and its own scoped key; a write also needs that exact (community, channel) in `writes`. Errors are fixed codes."""
import base64, hashlib, hmac, json, re, threading, time, urllib.error, urllib.request
from lifeos.buzz import nip98

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TOKEN = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
ALIAS, HEX = re.compile(r"^[A-Za-z0-9-]{1,32}$"), re.compile(r"^[0-9a-f]{64}$")
URL = re.compile(r"^https://[a-z0-9-]+\.communities\.buzz\.xyz$")
COMMUNITIES = ("life-os", "bytalos")
MAX_TEXT, MAX_CACHE = 4000, 256
RELAY_MAX, CURSOR_TTL = 1000, 21600  # the hosted relay's advertised max_limit (NIP-11); a cursor lives six hours
ETYPES = ("handoff_request", "handoff_ack", "progress", "blocker", "completion", "evidence")  # publishable event types
COUNCIL_TYPES = ("council_case", "council_contribution", "council_decision", "council_usage", "council_stop", "council_escalation")  # readable evidence shapes; publishing them is a separate authorization
UA = "life-os-buzz-bridge/0.2"


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
    def __init__(self, urls, keys, writes=(), transport=urllib_transport, recipients=None, may_address=False, clock=time.time):
        self.urls, self.keys, self.writes, self.send, self.sent = urls, keys, set(writes), transport, {}
        self.recipients, self.may_address = dict(recipients or {}), may_address  # {(community, lowercase alias): hex pubkey}; owner-approved, and addressing is off unless this sender is allowed
        self.clock = clock
        self.lock = threading.Lock()  # one writer at a time: precheck, event choice and submit are one atomic step

    def _call(self, community, path, payload):
        if community not in COMMUNITIES or community not in self.urls:
            raise BuzzError("community_not_authorized")
        if community not in self.keys:
            raise BuzzError("key_not_configured")
        url, body = self.urls[community] + path, json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Authorization": nip98.auth_header(self.keys[community], "POST", url, body), "Content-Type": "application/json", "User-Agent": UA}  # Cloudflare in front of hosted Buzz refuses the default Python-urllib signature (403, code 1010)
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
                "actor": self._tag(e, "actor"), "recipients": [t[1] for t in e.get("tags", []) if len(t) > 1 and t[0] == "p"], "untrusted_text": str(e.get("content", ""))[:MAX_TEXT]}

    def _view2(self, e):
        return {**self._view(e), "type": self._tag(e, "t"), "case": self._tag(e, "c"), "reply_to": next((t[1] for t in e.get("tags", []) if len(t) > 3 and t[0] == "e" and t[3] == "reply"), None)}

    def _mac(self, community, body):
        return hmac.new(hashlib.sha256(b"buzz-cursor-v1" + self.keys[community]).digest(), body.encode(), "sha256").hexdigest()[:32]  # keyed by this identity's own key: nothing new to store

    def _seal(self, community, scope, ts, last):
        body = base64.urlsafe_b64encode(json.dumps([1, scope, ts, last, int(self.clock()) + CURSOR_TTL], separators=(",", ":")).encode()).decode().rstrip("=")
        return body + "." + self._mac(community, body)

    def _open(self, community, scope, token):
        try:
            body, mac = token.split(".")
            ok, (v, sc, ts, last, exp) = hmac.compare_digest(mac, self._mac(community, body)), json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, TypeError, AttributeError):
            raise BuzzError("invalid_cursor") from None
        if not ok or v != 1 or sc != scope or not isinstance(ts, int) or not isinstance(last, str) or not isinstance(exp, int):
            raise BuzzError("invalid_cursor")
        if exp < self.clock():
            raise BuzzError("expired_cursor")
        return ts, last

    def _paged(self, community, op, flt, limit=None, cursor=None):
        """Lossless newest-first paging by (created_at, id), whatever order the relay returns ties in. A full window may have cut its oldest second short, so only newer seconds count as complete;
        the window widens up to the relay's max_limit, and more than that many matching events in ONE second is reported as unsupported rather than skipped."""
        if community not in self.keys:
            raise BuzzError("key_not_configured")
        lim = self._limit(limit, 20)
        scope = hashlib.sha256(f"{op}|{json.dumps(flt, sort_keys=True)}".encode()).hexdigest()[:16]
        ts, last = self._open(community, scope, cursor) if cursor is not None else (None, None)
        window = lim + 2
        while True:
            raw = self._query(community, {**flt, "limit": window, **({"until": ts} if ts is not None else {})})
            batch = {e["id"]: e for e in raw if isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("created_at"), int)}.values()
            full = len(raw) >= window
            floor = min((e["created_at"] for e in batch), default=0) if full else -1
            pool = sorted((e for e in batch if e["created_at"] > floor and (last is None or (e["created_at"], e["id"]) < (ts, last))), key=lambda e: (e["created_at"], e["id"]), reverse=True)
            if not pool and full and last is not None and floor < ts:  # the window reached an older second, so the cursor's own second is fully enumerated and nothing in it is left: step to the next older second
                ts, last, window = ts - 1, None, lim + 2
                continue
            if len(pool) > lim or not full or window >= RELAY_MAX:
                break
            window = min(window * 4, RELAY_MAX)
        if full and not pool:
            raise BuzzError("page_boundary_unsupported", max_same_second=RELAY_MAX)
        page = pool[:lim]
        return {"events": [self._view2(e) for e in page], "next_cursor": self._seal(community, scope, page[-1]["created_at"], page[-1]["id"]) if page and (len(pool) > lim or full) else None}

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

    def get_addressed(self, community, channel=None, limit=None, before=None):
        """Messages whose signed `p` tag names THIS identity, newest first: plain relay history, stateless, so reading never marks anything delivered or acknowledged.
        Older ones are reached by passing `next_before` back as `before` (the relay's inclusive `until`); the boundary second may repeat, so dedupe by event_id."""
        if community not in self.keys:
            raise BuzzError("key_not_configured")
        flt = {"kinds": [9], "#p": [nip98.pubkey(self.keys[community])], "limit": self._limit(limit, 20)}
        if channel is not None:
            flt["#h"] = [self._ok(channel, UUID)]
        if before is not None:
            if not isinstance(before, int) or isinstance(before, bool) or before < 1:
                raise BuzzError("invalid_argument")
            flt["until"] = before
        found = [self._view(e) for e in self._query(community, flt)]
        return {"messages": found, "next_before": min(m["created_at"] for m in found) if len(found) >= flt["limit"] else None}  # None: nothing older remains

    def messages_page(self, community, channel, limit=None, cursor=None):
        return self._paged(community, "messages", {"kinds": [9], "#h": [self._ok(channel, UUID)]}, limit, cursor)

    def addressed_page(self, community, channel=None, limit=None, cursor=None):
        flt = {"kinds": [9], "#p": [nip98.pubkey(self.keys[community]) if community in self.keys else ""], **({"#h": [self._ok(channel, UUID)]} if channel is not None else {})}
        return self._paged(community, "addressed", flt, limit, cursor)

    def thread_page(self, community, root, limit=None, cursor=None):
        return self._paged(community, "thread", {"kinds": [9], "#e": [self._ok(root, HEX)]}, limit, cursor)

    def evidence_page(self, community, etype=None, case=None, channel=None, limit=None, cursor=None):
        if (etype is None) == (case is None) or (etype is not None and etype not in ETYPES + COUNCIL_TYPES):
            raise BuzzError("invalid_argument")  # exactly one of type or case, and a registered type
        flt = {"kinds": [9], **({"#t": [etype]} if etype else {"#c": [self._ok(case, TOKEN)]}), **({"#h": [self._ok(channel, UUID)]} if channel is not None else {})}
        return self._paged(community, "evidence", flt, limit, cursor)

    def get_event(self, community, event_id):
        found = self._query(community, {"ids": [self._ok(event_id, HEX)], "kinds": [9], "limit": 1})
        if not found:
            raise BuzzError("not_found")
        return self._view2(found[0])

    def channel_info(self, community, channel):
        meta = self._query(community, {"kinds": [39000], "#d": [self._ok(channel, UUID)], "limit": 1})
        if not meta:
            raise BuzzError("not_found")
        roster = self._query(community, {"kinds": [39002], "#d": [channel], "limit": 1})
        members = [t[1] for e in roster[:1] for t in e.get("tags", []) if len(t) > 1 and t[0] == "p"]
        names = {v: a for (c, a), v in self.recipients.items() if c == community}
        return {"channel_id": channel, "name": self._tag(meta[0], "name"), "about": self._tag(meta[0], "about"), "public": any(t[:1] == ["public"] for t in meta[0]["tags"]), "member_count": len(members),
                "members": [names.get(k, "key:" + k[:16]) for k in members[:100]], "you_are_listed": nip98.pubkey(self.keys[community]) in members}

    def _recipient(self, community, channel, alias):
        """A recipient is an owner-approved alias for this community AND a current member of the destination channel (Buzz's mention contract: every recipient is a member when the message is written); never a caller-supplied key. Nobody is invited or added here."""
        if not isinstance(alias, str) or not ALIAS.match(alias):
            raise BuzzError("invalid_argument")
        pk = self.recipients.get((community, alias.lower()))
        if not self.may_address or pk is None or pk == nip98.pubkey(self.keys[community]):
            raise BuzzError("recipient_not_authorized")
        try:
            roster = self._query(community, {"kinds": [39002], "#d": [channel], "limit": 1})  # the channel's current member list
        except BuzzError as e:
            if e.code in ("community_not_authorized", "key_not_configured"):
                raise
            raise BuzzError("precheck_unavailable") from None
        if not any(t[:2] == ["p", pk] for e in roster for t in e.get("tags", [])):
            raise BuzzError("recipient_not_channel_member")
        return pk

    def search_messages(self, community, query, channel=None, limit=None):
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise BuzzError("invalid_argument")
        flt = {"kinds": [9], "search": query, "limit": self._limit(limit, 20)}
        if channel is not None:
            flt["#h"] = [self._ok(channel, UUID)]
        return [self._view(e) for e in self._query(community, flt)]

    def post_message(self, community, channel, text, idempotency_key, recipient=None, etype=None, case=None, reply_to=None, flat=False):
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
        if etype is not None and etype not in ETYPES or case is not None and not (isinstance(case, str) and TOKEN.match(case)):
            raise BuzzError("invalid_argument")
        to = self._recipient(community, channel, recipient) if recipient is not None else None  # every refusal happens here, before any event exists
        extra, bind = ([["p", to]] if to else []), (f"p:{to}|" if to else "")
        if reply_to is not None:
            root, parent = self._parent(community, channel, reply_to)
            if not (flat and root == parent):               # LIFE-397 (Jim, 5:58 PM CT): a plain reply to a top-level message is posted top-level (no thread tag); it stays in a thread only when the target is already inside one
                extra += [["e", root, "", "reply"]] if root == parent else [["e", root, "", "root"], ["e", parent, "", "reply"]]
        if etype or case or reply_to:
            extra += ([["t", etype]] if etype else []) + ([["c", case]] if case else [])
            bind += f"x:{etype or ''}|{case or ''}|{reply_to or ''}|"  # type, case and reply target are bound into the digest like the recipient
        with self.lock:
            return self._post(community, channel, text, actor, idempotency_key, extra, bind)

    def _parent(self, community, channel, event_id):
        try:
            found = self._query(community, {"ids": [self._ok(event_id, HEX)], "kinds": [9], "limit": 1})
        except BuzzError as e:
            if e.code in ("community_not_authorized", "key_not_configured"):
                raise
            raise BuzzError("precheck_unavailable") from None
        if not found or self._tag(found[0], "h") != channel:
            raise BuzzError("reply_target_not_found")
        marked = {t[3]: t[1] for t in found[0]["tags"] if len(t) > 3 and t[0] == "e"}
        return marked.get("root") or marked.get("reply") or event_id, event_id  # the parent's own root, else the message it replies to, else the parent itself

    def _post(self, community, channel, text, actor, idempotency_key, extra=(), bind=""):
        digest = hashlib.sha256((bind + f"{community}|{channel}|{actor}|{idempotency_key}|{text}").encode()).hexdigest()[:32]  # the recipient is part of the digest: a retry can never silently retarget
        ident = hashlib.sha256(f"{community}|{channel}|{actor}|{idempotency_key}".encode()).hexdigest()[:32]  # the key alone, so a restart still sees a reused key
        prior = self.sent.get((community, idempotency_key))
        if prior and prior[0] != digest:
            raise BuzzError("idempotency_conflict")
        try:  # read before write: a retry after an ambiguous outcome must never duplicate
            found = self._query(community, {"kinds": [9], "#h": [channel], "#k": [ident], "limit": 1})
        except BuzzError as e:
            if e.code in ("community_not_authorized", "key_not_configured"):
                raise
            raise BuzzError("precheck_unavailable") from None
        if found and self._tag(found[0], "i") != digest:
            raise BuzzError("idempotency_conflict")  # same key, different text, even across a restart
        if found:
            return {"status": "already_delivered", "event_id": found[0].get("id"), "actor": actor}
        ev = prior[1] if prior else nip98.event(self.keys[community], 9, [["h", channel], ["actor", actor], ["i", digest], ["k", ident]] + list(extra), text)
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
        return {"status": "sent_not_read_back", "event_id": ev["id"], "pubkey": ev["pubkey"], "actor": actor, "community": community, "channel": channel, **next(({"recipient": "key:" + t[1][:16]} for t in extra if t[0] == "p"), {})}
