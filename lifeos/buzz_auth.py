"""OAuth 2.1 resource-server checks for the remote Buzz MCP (stdlib only). Access tokens are RS256 JWTs from ONE configured issuer; this module never issues tokens, stores secrets or logs a token.
The calling client label (claude / chatgpt, or "shared" when both products use one registered client) comes from the token's signed client_id/azp matched against a server-side allowlist, never from anything the caller sends.
The signed-in USER must also be allowlisted by `sub`, with the communities that user may reach; an authorized client alone grants nothing."""
import base64, hashlib, hmac, json, threading, time, urllib.request
from lifeos.buzz_relay import _NoRedirect

DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")  # PKCS#1 v1.5 SHA-256 prefix
SCOPES = ("buzz:read", "buzz:write")


class AuthError(Exception):
    def __init__(self, code, status=401):
        super().__init__(code)
        self.code, self.status = code, status


def _b64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def fetch_json(url):
    with urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=10) as r:
        return json.loads(r.read(262144))


class Verifier:
    def __init__(self, issuer, audience, clients, jwks_url, users, as_url=None, fetch=fetch_json, clock=time.time, ttl=300):
        self.issuer, self.audience, self.clients, self.jwks_url, self.users = issuer, audience, dict(clients), jwks_url, {k: frozenset(v) for k, v in users.items()}
        self.as_url = as_url or issuer  # where clients fetch authorization-server metadata; some IdPs (Oracle IAM) publish it away from the `iss` string
        self.fetch, self.clock, self.ttl, self.keys, self.loaded, self.lock = fetch, clock, ttl, {}, -1e9, threading.Lock()

    def _key(self, kid):
        with self.lock:
            age = self.clock() - self.loaded
            if age > self.ttl or (kid not in self.keys and age > 30):  # unknown kid refreshes at most every 30 s
                try:
                    doc = self.fetch(self.jwks_url)
                    self.keys = {k["kid"]: (int.from_bytes(_b64(k["n"]), "big"), int.from_bytes(_b64(k["e"]), "big"))
                                 for k in doc["keys"] if k.get("kty") == "RSA" and k.get("use", "sig") == "sig" and isinstance(k.get("kid"), str)}
                    self.loaded = self.clock()
                except Exception:
                    raise AuthError("auth_unavailable", 503) from None
            if kid not in self.keys:
                raise AuthError("invalid_token")
            return self.keys[kid]

    def verify(self, header):
        if not header.startswith("Bearer "):
            raise AuthError("missing_token")
        try:
            h64, p64, s64 = header[7:].split(".")
            head, claims, sig = json.loads(_b64(h64)), json.loads(_b64(p64)), _b64(s64)
        except ValueError:
            raise AuthError("invalid_token") from None
        if not isinstance(head, dict) or not isinstance(claims, dict) or head.get("alg") != "RS256" or not isinstance(head.get("kid"), str):
            raise AuthError("invalid_token")
        n, e = self._key(head["kid"])
        k = (n.bit_length() + 7) // 8
        want = b"\0\1" + b"\xff" * (k - len(DIGEST_INFO) - 35) + b"\0" + DIGEST_INFO + hashlib.sha256(f"{h64}.{p64}".encode()).digest()
        if n.bit_length() < 2048 or len(sig) != k or not hmac.compare_digest(pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big"), want):
            raise AuthError("invalid_token")
        now, aud, exp = self.clock(), claims.get("aud"), claims.get("exp")
        aud = [aud] if isinstance(aud, str) else aud if isinstance(aud, list) else []
        if not isinstance(claims.get("iss"), str) or claims["iss"].rstrip("/") != self.issuer.rstrip("/") or self.audience not in aud or isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= now \
                or (isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > now):
            raise AuthError("invalid_token")
        cid = claims.get("client_id") or claims.get("azp")
        client = self.clients.get(cid) if isinstance(cid, str) else None
        if not client:
            raise AuthError("client_not_authorized", 403)
        sub = claims.get("sub")
        if not isinstance(sub, str) or sub not in self.users:  # fail closed: absent, wrong-type or unlisted user
            raise AuthError("user_not_authorized", 403)
        scope = claims.get("scope")
        return {"client": client, "scopes": set(scope.split()) if isinstance(scope, str) else set(), "sub": sub[:64], "communities": self.users[sub]}
