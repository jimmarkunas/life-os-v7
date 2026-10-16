"""LIFE-47 S2 (Edge): the transport gate of the private Close Opportunity service on Oracle (Jim's Option B, LIFE-47 comments 10248/10249).

systemd starts one process per connection (lifeos-close.socket on 127.0.0.1:47344, Accept=yes) as a DynamicUser that cannot read the OpenClaw
account and that OpenClaw (`ubuntu`, no root since Oct 16 2026) cannot read; Tailscale Serve terminates TLS on tailnet port 47443 and forwards
with a PROXY v2 header. Before the application sees a byte of the request this gate:
  1. requires the loopback peer to be root (tailscaled), so no local user, OpenClaw included, can present a PROXY header;
  2. requires the PROXY v2 source and asks tailscaled who it is: the approved login, an untagged node, and a StableID on the approved list
     (Jim's iPhone and MacBook; the Oracle node, the Mac mini and every other device are refused);
  3. reads ONE bounded HTTP/1.1 request: GET or POST only, the exact Host, no duplicate header, no chunked body; a POST must be same-origin
     (Origin and, when sent, Sec-Fetch-Site) and form-encoded;
  4. calls the application with the request, the verified identity and its credentials. A GET gets NO Notion credential, so it cannot write.
Anything else gets a fixed status and no detail; nothing is logged. Policy (host, login, node ids) is root-owned config, never in the repo."""
import json
import os
import subprocess
import sys
from urllib.parse import parse_qsl

from lifeos.imessage import alerts_endpoint as ep

POLICY = "/etc/lifeos-close/policy.json"
MAX_HEAD, MAX_BODY = 8192, 16384
REASONS = {200: "OK", 303: "See Other", 400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed", 409: "Conflict", 500: "Internal Server Error"}
SAFE = (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
        ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"), ("Connection", "close"))


def identity(ip, policy, lookup=ep.whois):
    """The verified caller {login, node} or None: the approved login, untagged, and an approved node StableID."""
    try:
        found = lookup(ip) or {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    node, user = found.get("Node") or {}, found.get("UserProfile") or {}
    if node.get("Tags") or user.get("LoginName") != policy["login"] or node.get("StableID") not in policy["nodes"]:
        return None
    return {"login": user["LoginName"], "node": node["StableID"]}


def read_request(stream):
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        c = stream.read(1)
        if not c or len(head) >= MAX_HEAD:
            return None
        head += c
    lines = head[:-4].decode("latin-1").split("\r\n")
    parts, headers = lines[0].split(" "), {}
    if len(parts) != 3 or parts[2] != "HTTP/1.1":
        return None
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep or name.strip().lower() in headers:
            return None                                                    # a duplicate header is refused, never resolved
        headers[name.strip().lower()] = value.strip()
    size = headers.get("content-length", "0")
    if "transfer-encoding" in headers or not size.isdigit() or int(size) > MAX_BODY:
        return None
    body = ep.read_exact(stream, int(size)) if int(size) else b""
    path, _, query = parts[1].partition("?")
    return None if body is None else {"method": parts[0], "path": path, "query": dict(parse_qsl(query)), "headers": headers, "body": body}


def admitted(req, policy):
    """0 when the request may reach the application, else the fixed status."""
    h = req["headers"]
    if h.get("host") != policy["host"]:
        return 400
    if req["method"] == "GET":
        return 400 if req["body"] else 0
    if req["method"] != "POST":
        return 405
    same = h.get("origin") == "https://" + policy["host"] and h.get("sec-fetch-site", "same-origin") == "same-origin"
    return 0 if same and h.get("content-type") == "application/x-www-form-urlencoded" else 403


def respond(out, status, body=b"", ctype="text/plain; charset=utf-8", extra=()):
    head = [f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}", f"Content-Type: {ctype}", f"Content-Length: {len(body)}"]
    head += [f"{k}: {v}" for k, v in (*SAFE, *extra)]
    out.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body)
    out.flush()


def main(app, stdin=None, stdout=None, lookup=ep.whois, uid=ep.loopback_peer_uid, policy_path=POLICY, creds=None):
    """app(req, who, secrets) -> (status, content type, body bytes, extra headers). secrets: {"link"} on GET, {"link", "notion"} on POST."""
    stdin, stdout = stdin or sys.stdin.buffer, stdout or sys.stdout.buffer
    try:
        if uid() != 0:
            return 1                                                       # not tailscaled: refused before any header is read
    except (OSError, ValueError):
        return 1
    ip = ep.proxy_source(stdin)
    if ip is None:
        return 1
    with open(policy_path) as f:
        policy = json.load(f)
    who = identity(ip, policy, lookup)
    req = read_request(stdin) if who else None
    code = 403 if who is None else 400 if req is None else admitted(req, policy)
    if code:
        respond(stdout, code)
        return 0
    creds = creds or os.environ.get("CREDENTIALS_DIRECTORY", "")
    names = ("link",) if req["method"] == "GET" else ("link", "notion")
    secrets = {n: open(os.path.join(creds, n), "rb").read().strip() for n in names}
    try:
        status, ctype, body, extra = app({**req, "form": dict(parse_qsl(req["body"].decode("utf-8", "replace")))}, who, secrets)
    except Exception:                                                      # noqa: BLE001 - no failure detail ever leaves the gate
        status, ctype, body, extra = 500, "text/plain; charset=utf-8", b"", ()
    respond(stdout, status, body, ctype, extra)
    return 0
