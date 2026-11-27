"""Hourly capture of LIFE-project decisions from Jira into shared memory through the governed memory tools.

A comment on a ticket updated in the last two hours is a decision when it has an `[approvals]` line; each becomes one
Personal memory item. Output: counts and fixed words, never text.
"""

# A comment never stores twice (one private state row each); an edit corrects its item, a deletion revokes it; nothing
# older than the window is backfilled.
# Writes go through the governed MCP as the `jira-capture` client, whose server applies the sensitive-text filter; a
# refused decision is counted, never stored or logged. No filter here; never recalls.
import hashlib
import json
import os
import http.client
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from lifeos.jira.search import search
from lifeos.platform.jira import Jira, JiraError
from lifeos.platform.snapshot_store import Store

PROJECT = "LIFE"
WINDOW_HOURS = 2
MAX_ISSUES, WRITE_CAP, TEXT_MAX = 40, 30, 500
URL_DEFAULT = "https://lifeos-buzz.tail96b07f.ts.net/mcp"
SOURCE = "jira-decision"
TAG = re.compile(r"\[approvals\]", re.I)
LEAD = re.compile(
    r"^\W*(?:jim'?s (?:decisions?|rules?|approvals?)\b|jim,\s+[A-Z][a-z]{2,8}\s+\d{1,2}\b[^\n:]{0,60}:)", re.I
)
COMMENT_STORE = Store("jira_capture_comment", "comment_id", "BIGINT")
ISSUE_STORE = Store("jira_capture_issue", "issue_key", "VARCHAR(32)")


def flatten(node):
    if isinstance(node, dict):
        inner = "".join(flatten(c) for c in node.get("content") or ())
        text = node.get("text", "") + inner
        return (
            text + "\n"
            if node.get("type") in ("paragraph", "heading", "listItem", "hardBreak", "bulletList", "orderedList")
            else text
        )
    return str(node or "")


def decision_text(body):
    """The decision a comment states ("" when it is not one): its `[approvals]` lines, else its opening paragraph."""
    text = flatten(body).strip()
    tagged = [ln.strip() for ln in text.splitlines() if TAG.search(ln)]
    if tagged:
        return " ".join(tagged)[:TEXT_MAX]
    first = next((p.strip() for p in text.split("\n\n") if p.strip()), "")
    return re.sub(r"\s+", " ", first)[:TEXT_MAX] if LEAD.search(text[:160]) else ""


def public_address(host, urlopen=urllib.request.urlopen):
    """The public (Funnel) address of a name, over DNS-over-HTTPS. A runner on the tailnet resolves the name to a tailnet address its policy does not allow; the public address answers."""
    request = urllib.request.Request(f"https://cloudflare-dns.com/dns-query?name={host}&type=A", headers={"Accept": "application/dns-json", "User-Agent": "life-os-v7"})
    with urlopen(request, timeout=10) as reply:
        answers = json.loads(reply.read()).get("Answer") or []
    return next(a["data"] for a in answers if a.get("type") == 1)


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to a fixed address with the certificate and SNI checked against the real name."""

    address = ""

    def connect(self):
        sock = socket.create_connection((self.address, self.port or 443), timeout=30)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)


def pinned_open(address):
    class Handler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(lambda host, **kw: type("Pinned", (_Pinned,), {"address": address})(host, **kw), req)
    return urllib.request.build_opener(Handler).open


class Memory:
    def __init__(self, url, token, urlopen=urllib.request.urlopen):
        self.url, self.token, self.urlopen = url, token, urlopen
        self.public = False
        self.down = ""  # the error class of the first failed call; later calls fail at once instead of waiting out 30 seconds each

    def call(self, operation, arguments):
        if self.down:
            return {"ok": False, "error": "capture_unreachable"}
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "buzz_read", "arguments": {"operation": operation, "arguments": arguments}},
            }
        ).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
                "Authorization": "Bearer " + self.token,
                "User-Agent": "life-os-v7",
            },
        )
        try:
            try:
                with self.urlopen(request, timeout=30) as reply:
                    return json.loads(json.loads(reply.read())["result"]["content"][0]["text"])
            except (urllib.error.URLError, OSError) as error:
                if self.urlopen is not urllib.request.urlopen or self.public or not isinstance(getattr(error, "reason", error), (TimeoutError, socket.timeout)):
                    raise
                self.public = True  # one retry through the public address; the first failure is not the answer
                host = urllib.parse.urlsplit(self.url).hostname
                self.urlopen = pinned_open(public_address(host))
                with self.urlopen(request, timeout=30) as reply:
                    return json.loads(json.loads(reply.read())["result"]["content"][0]["text"])
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
            reason = getattr(error, "reason", None)
            self.down = (type(error).__name__ + ("/" + type(reason).__name__ if reason is not None else ""))[:40]
            return {"ok": False, "error": "capture_unreachable"}

    def store(self, text, source):
        return self.call("memory_store", {"workspace": "Personal", "text": text, "source": source})

    def correct(self, memory_id, text, source):
        return self.call("memory_correct", {"workspace": "Personal", "id": memory_id, "text": text, "source": source})

    def revoke(self, memory_id):
        return self.call("memory_revoke", {"workspace": "Personal", "id": memory_id})


class State:
    def __init__(self, connection):
        self.connection = connection
        COMMENT_STORE.ensure(connection)
        ISSUE_STORE.ensure(connection)

    @staticmethod
    def _wrap(payload, now):
        return {"taken_at": now.isoformat(), "schema": 1, **payload}

    def comment(self, comment_id):
        return COMMENT_STORE.load(self.connection, int(comment_id))

    def put_comment(self, comment_id, payload, now):
        COMMENT_STORE.save(self.connection, int(comment_id), self._wrap(payload, now))

    def issue(self, key):
        return (ISSUE_STORE.load(self.connection, key) or {}).get("comments") or []

    def put_issue(self, key, ids, now):
        ISSUE_STORE.save(self.connection, key, self._wrap({"comments": sorted(ids)}, now))


def _tick(total, key, why=None):
    total[key] += 1
    if why:
        total["why"][why] = total["why"].get(why, 0) + 1


def _item(key, comment_id, decision):
    return f"{decision} (Jira {key}, comment {comment_id}; Jira is the source of truth)"


def capture_issue(jira, key, memory, state, live, now, total):
    data = jira.get(f"/rest/api/3/issue/{key}/comment", {"maxResults": 100, "orderBy": "created"})
    comments = [c for c in (data or {}).get("comments") or [] if isinstance(c, dict) and str(c.get("id", "")).isdigit()]
    seen = set()
    for comment in comments:
        decision = decision_text(comment.get("body"))
        if not decision:
            continue
        cid = int(comment["id"])
        seen.add(cid)
        total["decisions"] += 1
        digest = hashlib.sha256(decision.encode()).hexdigest()
        prior = state.comment(cid) if state else None
        if prior and prior.get("state") in ("stored", "filtered") and prior.get("sha") == digest:
            total["unchanged"] += 1
            continue
        if total["writes"] >= WRITE_CAP:
            _tick(total, "deferred")
            continue
        if not live:
            total["would_write"] += 1
            continue
        total["writes"] += 1
        text, source = _item(key, cid, decision), f"{SOURCE}:{key}#{cid}"
        reply = (
            memory.correct(prior["memory_id"], text, source)
            if prior and prior.get("state") == "stored"
            else memory.store(text, source)
        )
        new = (reply.get("data") or {}).get("id")
        if reply.get("ok") and new:
            _tick(total, "corrected" if prior and prior.get("state") == "stored" else "stored")
            state.put_comment(cid, {"issue": key, "sha": digest, "memory_id": new, "state": "stored"}, now)
        elif reply.get("error") == "sensitive_refused":
            _tick(total, "filtered")
            state.put_comment(cid, {"issue": key, "sha": digest, "memory_id": "", "state": "filtered"}, now)
        else:
            _tick(total, "failed", str(reply.get("error") or "capture_failed")[:40])
            if getattr(memory, "down", ""):
                total["why"]["down_" + memory.down.replace("/", "_")] = 1
                return
    if live and state:
        for gone in sorted(set(state.issue(key)) - seen):
            prior = state.comment(gone) or {}
            if prior.get("state") == "stored" and prior.get("memory_id") and total["writes"] < WRITE_CAP:
                total["writes"] += 1
                if memory.revoke(prior["memory_id"]).get("ok"):
                    _tick(total, "revoked")
                    state.put_comment(gone, {**prior, "state": "revoked"}, now)
                else:
                    _tick(total, "failed", "revoke_failed")
        state.put_issue(key, seen, now)


def run(limit, live, environ=os.environ, jira=None, now=None, connect=None, memory=None):
    """Counts and fixed words only; not configured is the fixed `capture_not_configured`, never a failure."""
    now = now or datetime.now(timezone.utc)
    total = {
        "issues": 0,
        "decisions": 0,
        "stored": 0,
        "corrected": 0,
        "revoked": 0,
        "unchanged": 0,
        "filtered": 0,
        "failed": 0,
        "deferred": 0,
        "would_write": 0,
        "writes": 0,
        "why": {},
    }
    token = (environ.get("LIFEOS_MCP_JIRA_CAPTURE") or "").strip()
    if live and memory is None and not token:
        return {**total, "why": {"capture_not_configured": 1}}
    memory = memory or Memory((environ.get("LIFEOS_MCP_URL") or URL_DEFAULT).strip(), token)
    jira = jira or Jira.from_env(environ)
    issues = search(jira, f"project = {PROJECT} AND updated >= -{WINDOW_HOURS}h ORDER BY updated DESC", "summary")[
        :MAX_ISSUES
    ]
    total["issues"] = len(issues)
    state = None
    if live or connect:
        with (connect or __import__("lifeos.platform.db", fromlist=["connect"]).connect)() as connection:
            state = State(connection)
            for issue in issues:
                try:
                    capture_issue(jira, issue["key"], memory, state, live, now, total)
                except JiraError:
                    _tick(total, "failed", "jira_comments")
    if total["failed"]:
        raise JiraError("JIRA_CAPTURE_FAILED:" + ",".join(sorted(total["why"])))
    return total
