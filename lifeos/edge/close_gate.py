"""LIFE-47 S2 (Edge): transport gate of the private Close Opportunity service (Option B, LIFE-47 10248/10249). One process per connection (127.0.0.1:47344,
Accept=yes) as a DynamicUser isolated from OpenClaw (`ubuntu`, no root); Tailscale Serve terminates TLS on 47443 and adds PROXY v2. Before the app sees the request:
  1. the loopback peer must be root (tailscaled): no local user, OpenClaw included, can present a PROXY header;
  2. PROXY v2 source -> tailscaled whois: the approved login, untagged, StableID on the approved list (iPhone, MacBook; never Oracle or the Mac mini);
  3. ONE bounded HTTP/1.1 request: GET or POST, exact Host, no duplicate header or chunked body; POST same-origin and form-encoded;
  4. GET never gets the Notion token, only a read-only client; POST gets the token. Refusals are fixed statuses; nothing is logged.
After a POST's response is written, a refresh the app asked for (Jim, Oct 24: closed rows should leave the Daily Report without waiting for the hour) is one GitHub
workflow_dispatch made with the optional `github` credential, which the app never sees; without that credential nothing is asked."""
import json
import os
import subprocess
import sys
import urllib.request
from urllib.parse import parse_qsl

from lifeos.imessage import alerts_endpoint as ep
from lifeos.platform.notion_client import Client

POLICY = "/etc/lifeos-close/policy.json"
API = "https://api.github.com/repos/jimmarkunas/life-os-v7/actions/workflows/domains.yml"
REFRESH = {"hiring": {"refresh": "hiring", "live": "true"}}           # the only runs an app may ask for: the Hiring Pipeline region, redrawn by its own hourly writer
WAITING = ("queued", "pending", "waiting", "requested")
MAX_HEAD, MAX_BODY = 8192, 16384
REASONS = {200: "OK", 303: "See Other", 400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed", 409: "Conflict", 500: "Internal Server Error"}
# same-origin, never no-referrer: under no-referrer a browser sends `Origin: null` on the form POST and the origin check below refuses every Review (iPhone Safari, Oct 16 2026)
SAFE = (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin"), ("X-Frame-Options", "DENY"),
        ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"), ("Connection", "close"))


class ReadOnly:
    """GET's Notion client. Reads pass through (GET calls, data-source queries, page reads, the configured source id); every write is refused
    before anything is sent. The token stays inside the wrapped client."""

    def __init__(self, client):
        self._client, self.source = client, client.source

    def call(self, method, path, body=None):
        if method == "GET" or (method == "POST" and str(path).endswith("/query")):
            return self._client.call(method, path, body)
        raise PermissionError("READ_ONLY")

    def get_page(self, page_id):
        return self._client.get_page(page_id)

    def query_data_source(self, source_id=None, body=None):
        return self._client.query_data_source(source_id, body)


def notion_client(token, policy):
    """The real client for the canonical Job Ledger; a policy without the ledger id fails closed (NOTION_CONFIG_MISSING)."""
    return Client({"NOTION_API_TOKEN": token.decode(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": policy.get("ledger", "")})


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


def refresh(token, wants, opener=urllib.request.urlopen):
    """One workflow_dispatch per known refresh the app asked for, unless a domains run is already waiting: that run redraws the region anyway, and a second waiting run would
    replace it in the shared concurrency group (the Oct 21 stall). Best effort and silent: the response is already sent, and the hourly run still redraws. -> codes, for tests."""
    def call(method, path, body=None):
        req = urllib.request.Request(API + path, None if body is None else json.dumps(body).encode(), method=method, headers={
            "Authorization": "Bearer " + token.decode(), "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lifeos-close"})
        with opener(req, timeout=10) as response:
            return json.loads(response.read() or b"{}")
    done = []
    for name in dict.fromkeys(w for w in wants if isinstance(w, str) and w in REFRESH):
        try:
            if any(r.get("status") in WAITING for r in call("GET", "/runs?per_page=10").get("workflow_runs") or []):
                done.append("waiting")
                continue
            call("POST", "/dispatches", {"ref": "main", "inputs": REFRESH[name]})
            done.append("dispatched")
        except Exception:                                                  # noqa: BLE001 - nothing is reported to anyone: the close itself is already verified
            done.append("failed")
    return done


def main(app, stdin=None, stdout=None, lookup=ep.whois, uid=ep.loopback_peer_uid, policy_path=POLICY, creds=None, client=notion_client):
    """app(req, who, secrets) -> (status, content type, body bytes, extra headers).
    secrets: GET {"link", "reader": ReadOnly client, "config"}; POST {"link", "notion": the token, "config"}; config = canonical ledger/pipeline ids."""
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
    secrets = {n: open(os.path.join(creds, n), "rb").read().strip() for n in ("link", "notion")}
    github = os.path.join(creds, "github")
    try:
        token = open(github, "rb").read().strip() if os.path.isfile(github) else b""  # optional: absent or unreadable = no refresh is ever asked
    except OSError:
        token = b""
    after = []
    secrets["config"] = {k: policy.get(k) for k in ("ledger", "pipeline")}
    if policy.get("recruiters"):                                          # D221 Mark as Sent; without it /sent refuses NOTION_CONFIG_MISSING and nothing is written
        secrets["config"]["recruiters"] = policy["recruiters"]
    try:
        if req["method"] == "GET":
            secrets["reader"] = ReadOnly(client(secrets.pop("notion"), policy))
        else:
            secrets["after"] = after                                       # the app appends refresh names (REFRESH keys) after a verified write
        status, ctype, body, extra = app({**req, "form": dict(parse_qsl(req["body"].decode("utf-8", "replace")))}, who, secrets)
    except Exception:                                                      # noqa: BLE001 - no failure detail ever leaves the gate
        status, ctype, body, extra = 500, "text/plain; charset=utf-8", b"", ()
    respond(stdout, status, body, ctype, extra)
    if token and after and status == 200:
        refresh(token, after)
    return 0
