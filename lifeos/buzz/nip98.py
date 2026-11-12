"""NIP-98 (kind 27235) request signing for the Buzz relay: BIP-340 Schnorr over secp256k1, stdlib only. Keys are passed in by the caller; nothing is stored or logged."""
import base64, hashlib, json, os, time, uuid

P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798, 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)
B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


def _add(a, b):
    if a is None or b is None:
        return a or b
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    m = (3 * a[0] * a[0] * pow(2 * a[1], -1, P) if a == b else (b[1] - a[1]) * pow(b[0] - a[0], -1, P)) % P
    x = (m * m - a[0] - b[0]) % P
    return x, (m * (a[0] - x) - a[1]) % P


def _mul(k, pt=G):
    r = None
    while k:
        r = _add(r, pt) if k & 1 else r
        pt, k = _add(pt, pt), k >> 1
    return r


def _tagged(tag, *parts):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + b"".join(parts)).digest()


def secret_from(value):
    """Hex or bech32 `nsec1...` to a 32-byte secret. Raises ValueError without echoing the input."""
    v = value.strip().lower()
    if v.startswith("nsec1"):
        data = [B32.index(c) for c in v[5:]]
        chk = 1
        for x in [ord(c) >> 5 for c in "nsec"] + [0] + [ord(c) & 31 for c in "nsec"] + data:
            top, chk = chk >> 25, ((chk & 0x1FFFFFF) << 5) ^ x
            for i in range(5):
                chk ^= GEN[i] if (top >> i) & 1 else 0
        bits = "".join(f"{x:05b}" for x in data[:-6])
        raw = bytes(int(bits[i:i + 8], 2) for i in range(0, 256, 8)) if chk == 1 and len(bits) >= 256 else b""
    else:
        raw = bytes.fromhex(v)
    if len(raw) != 32 or not 1 <= int.from_bytes(raw, "big") < N:
        raise ValueError("invalid key")
    return raw


def pubkey(secret):
    return _mul(int.from_bytes(secret, "big"))[0].to_bytes(32, "big").hex()


def sign(secret, msg, aux=None):
    d0 = int.from_bytes(secret, "big")
    pt = _mul(d0)
    d, px = (d0 if pt[1] % 2 == 0 else N - d0), pt[0].to_bytes(32, "big")
    t = (d ^ int.from_bytes(_tagged("BIP0340/aux", aux or os.urandom(32)), "big")).to_bytes(32, "big")
    k0 = int.from_bytes(_tagged("BIP0340/nonce", t, px, msg), "big") % N
    r = _mul(k0)
    k, rx = (k0 if r[1] % 2 == 0 else N - k0), r[0].to_bytes(32, "big")
    e = int.from_bytes(_tagged("BIP0340/challenge", rx, px, msg), "big") % N
    return (rx + ((k + e * d) % N).to_bytes(32, "big")).hex()


def event(secret, kind, tags, content="", created_at=None):
    pk, ts = pubkey(secret), int(time.time()) if created_at is None else created_at
    ident = hashlib.sha256(json.dumps([0, pk, ts, kind, tags, content], separators=(",", ":"), ensure_ascii=False).encode()).digest()
    return {"id": ident.hex(), "pubkey": pk, "created_at": ts, "kind": kind, "tags": tags, "content": content, "sig": sign(secret, ident)}


def auth_header(secret, method, url, body=None):
    tags = [["u", url], ["method", method], ["nonce", str(uuid.uuid4())]]
    if body is not None:
        tags.append(["payload", hashlib.sha256(body).hexdigest()])
    return "Nostr " + base64.b64encode(json.dumps(event(secret, 27235, tags), separators=(",", ":")).encode()).decode()
