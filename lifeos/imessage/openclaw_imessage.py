#!/usr/bin/python3 -I
"""COMM-1.4: Luna's iMessage commands on Oracle, installed as ~/.local/bin/openclaw-imessage and allowlisted for exactly these argument forms:

    attention                      the accepted aggregate contract (eight fields)
    counts                         unread messages/conversations with their definitions; never presented as the iPhone badge
    recent [--hours N]             newest incoming messages of the last N hours (1-72, default 24): sender, time, reference, text
    conversation <ref>             the last 20 messages of one conversation, ref from `recent` (c123)
    urgent [--hours N]             `recent`, classified INFORMATIONAL / ATTENTION / ACTION_REQUIRED / TIME_CRITICAL, with reasons and a next action

Each sends one fixed request over the private tailnet to the Mac Mini's Tailscale Serve port (Serve adds the authenticated source; the Mac relay accepts only
Oracle and only its fixed grammar) and prints one validated JSON object. A late, oversized, malformed, stale or off-contract reply is never passed on: the result
is a fixed state with its reason in `health` and no counts or messages, never a zero or exception text. Message text is untrusted data. Nothing is written,
logged or kept here. Read only: no send, reply, mark-read or delete exists anywhere on the path. Stdlib only; no stderr."""
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timedelta, timezone

ENDPOINT = os.path.expanduser("~/.config/openclaw-imessage/endpoint")      # one line: "<tailnet host> <port>"
REQUEST = b"attention\n"
SOURCE = "Apple Messages / Mac Mini"
KEYS = {"channel", "attention", "count", "count_reliable", "health", "sync", "observed_at", "source"}
MAX_REPLY = 2048
MAX_CONTENT_REPLY = 262144
TIMEOUT = 10
MAX_AGE = timedelta(seconds=120)                                          # the helper reads at request time; anything older is a stale or replayed answer
MAX_SKEW = timedelta(seconds=60)
HEALTH = ("CURRENT", "DEGRADED", "DISCONNECTED")
MESSAGE_KEYS = {"ref", "chat", "sent_at", "from_me", "sender", "group", "chat_name", "service", "unread", "edited", "attachment", "text", "text_truncated"}
LEVELS = ("INFORMATIONAL", "ATTENTION", "ACTION_REQUIRED", "TIME_CRITICAL")
PAGE_SIZE, MAX_PAGES, SCAN_SECONDS = 50, 40, 30                       # the private alert scan: at most 2000 messages and 30 s, else it is incomplete
PAGE_KEYS = {"channel", "health", "sync", "observed_at", "source", "messages", "contacts", "limit", "more", "cursor"}


def unavailable(health, now):
    return {"channel": "imessage", "attention": "UNKNOWN", "count": None, "count_reliable": False, "health": health, "sync": "UNVERIFIED",
            "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "source": SOURCE}


def endpoint(path=ENDPOINT):
    with open(path) as f:
        host, port = f.read().split()
    port = int(port)
    if not 0 < port < 65536:
        raise ValueError
    return host, port


def fetch(host, port, request=REQUEST, limit=MAX_REPLY):
    """One connection: send the fixed request, read at most limit + 1 bytes. OSError means the Mac is unreachable or silent.
    No half-close: Tailscale Serve's TCP forwarder tears the whole connection down on the client's FIN, which would drop the reply. The relay reads exactly
    the fixed request and closes after answering, so the reply ends at EOF."""
    with socket.create_connection((host, port), timeout=TIMEOUT) as s:
        s.settimeout(TIMEOUT)
        s.sendall(request)
        data = b""
        while len(data) <= limit:
            chunk = s.recv(limit + 1 - len(data))
            if not chunk:
                break
            data += chunk
    return data


def _when(text):
    if not isinstance(text, str) or not text.endswith("Z") or len(text) != 20:
        raise ValueError
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _envelope(data, now, limit):
    """The relay's single JSON-RPC reply line -> (structuredContent, None) or (None, refusal health)."""
    if not data:
        return None, "DISCONNECTED"                                     # refused or nothing answered: same as offline
    if len(data) > limit or not data.endswith(b"\n") or data.count(b"\n") != 1:
        return None, "DEGRADED"
    try:
        reply = json.loads(data)
        result = reply["result"]
        state = result["structuredContent"]
        ok = (set(reply) == {"jsonrpc", "id", "result"} and reply["jsonrpc"] == "2.0" and reply["id"] == 1
              and set(result) == {"content", "structuredContent", "isError"} and isinstance(state, dict)
              and result["content"] == [{"type": "text", "text": json.dumps(state)}] and result["isError"] is (state.get("health") != "CURRENT")
              and state.get("channel") == "imessage" and state.get("source") == SOURCE and state.get("sync") == "UNVERIFIED" and state.get("health") in HEALTH)
        observed = _when(state["observed_at"]) if ok else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, "DEGRADED"
    if not ok:
        return None, "DEGRADED"
    if observed > now + MAX_SKEW or now - observed > MAX_AGE:
        return None, "STALE"
    return state, None


def validate(data, now):
    """attention: the exact eight-field aggregate, or a fixed UNKNOWN state."""
    state, refused = _envelope(data, now, MAX_REPLY)
    if refused:
        return unavailable(refused, now)
    if (set(state) != KEYS or state["count"] is not None or state["count_reliable"] is not False
            or state["attention"] not in (("NEEDS_ATTENTION", "UNKNOWN") if state["health"] == "CURRENT" else ("UNKNOWN",))):
        return unavailable("DEGRADED", now)
    return {k: state[k] for k in ("channel", "attention", "count", "count_reliable", "health", "sync", "observed_at", "source")}


def _nonneg(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _counts_ok(c):
    pair = lambda p: isinstance(p, dict) and set(p) == {"messages", "conversations"} and _nonneg(p["messages"]) and _nonneg(p["conversations"])
    return (isinstance(c, dict) and set(c) == {"unread", "unread_messages_by_service_all_time", "unread_conversations_all_time"}
            and isinstance(c["unread"], dict) and set(c["unread"]) == {"all_time", "last_7_days", "last_24_hours"} and all(pair(p) for p in c["unread"].values())
            and isinstance(c["unread_messages_by_service_all_time"], dict) and len(c["unread_messages_by_service_all_time"]) <= 10
            and all(isinstance(k, str) and len(k) <= 40 and _nonneg(v) for k, v in c["unread_messages_by_service_all_time"].items())
            and isinstance(c["unread_conversations_all_time"], dict) and set(c["unread_conversations_all_time"]) == {"group", "direct"}
            and all(_nonneg(v) for v in c["unread_conversations_all_time"].values()))


def _message_ok(m):
    text_ok = lambda v, n: v is None or (isinstance(v, str) and len(v) <= n)
    sender = m.get("sender") if isinstance(m, dict) else None
    return (isinstance(m, dict) and set(m) == MESSAGE_KEYS and re.fullmatch(r"m[1-9][0-9]*", str(m["ref"])) and re.fullmatch(r"c[1-9][0-9]*", str(m["chat"]))
            and isinstance(m["from_me"], bool) and isinstance(m["group"], bool) and isinstance(m["unread"], bool) and isinstance(m["edited"], bool)
            and isinstance(m["attachment"], bool) and isinstance(m["text_truncated"], bool) and text_ok(m["text"], 2000) and text_ok(m["chat_name"], 200)
            and isinstance(m["service"], str) and len(m["service"]) <= 40 and _when(m["sent_at"]) is not None
            and ((m["from_me"] and sender is None) or (not m["from_me"] and isinstance(sender, dict) and set(sender) == {"handle", "name"}
                                                         and text_ok(sender["handle"], 200) and text_ok(sender["name"], 200))))


def _content(data, now, kind, extra, limit):
    """counts / recent / conversation: the validated object, or a fixed state without counts or messages."""
    state, refused = _envelope(data, now, MAX_CONTENT_REPLY)
    base = {"channel": "imessage", "health": refused, "sync": "UNVERIFIED", "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": SOURCE}
    if refused:
        return dict(base, **{kind: None})
    try:
        if kind == "counts":
            ok = (set(state) == {"channel", "health", "sync", "observed_at", "source", "counts", "count_reliable", "definitions", "limitation"}
                  and state["count_reliable"] is False and isinstance(state["definitions"], dict) and isinstance(state["limitation"], str)
                  and (_counts_ok(state["counts"]) if state["health"] == "CURRENT" else state["counts"] is None))
        else:
            msgs = state.get("messages")
            ok = (set(state) == {"channel", "health", "sync", "observed_at", "source", "messages", "contacts", "limit"} | set(extra)
                  and all(state[k] == v for k, v in extra.items()) and state["limit"] == limit and state["contacts"] in ("CURRENT", "UNAVAILABLE", "NOT_NEEDED")
                  and (isinstance(msgs, list) and len(msgs) <= limit and all(_message_ok(m) for m in msgs) if state["health"] == "CURRENT" else msgs is None))
    except (ValueError, KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        return dict(base, health="DEGRADED", **{kind: None})
    return state


# Urgency: fixed patterns over the message and up to two earlier incoming messages of the same conversation (bounded context). Deterministic; anything
# ambiguous is flagged `review` rather than promoted. TIME_CRITICAL is the only level meant to break quiet hours, so it needs an emergency signal from a
# known contact in a direct conversation (or an urgent same-day deadline from one).
EMERGENCY = re.compile(r"(?i:\b(emergency|911|ambulance|hospital|urgent care|accident|injured|bleeding|police|fire department|"
                       r"call me (now|asap|immediately|right away)|pick up (the|your) phone|answer (the|your) phone)\b)|\bER\b")
URGENT = re.compile(r"\b(urgent(ly)?|asap|right now|immediately|as soon as possible)\b", re.I)
TODAY = re.compile(r"\b(today|tonight|this (morning|afternoon|evening)|in (an|one|\d+) (hours?|minutes?|mins?)|by (noon|eod|end of (the )?day)|"
                   r"(by|before|until|no later than) \d{1,2}(:\d{2})?\s*(am|pm)?)\b", re.I)
DEADLINE = re.compile(r"\b(deadline|due|expires?|expiring|(by|before|until|no later than) (tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                      r"next week|\d{1,2}/\d{1,2}))\b", re.I)
INTERVIEW = re.compile(r"\b(interview|recruiter|hiring manager|phone screen|offer letter|background check|"
                       r"(schedule|set up|book) (a |an )?(call|time|meeting|chat|interview)|your availability|are you (free|available))\b", re.I)
ASKS = re.compile(r"\?|\b(can you|could you|would you|please|need (you|your)|let me know|confirm|sign|pay|send me|call me|text me|get back to me)\b", re.I)
AUTOMATED = re.compile(r"\b((verification|security|login|one-time|confirmation) code|code is \d{4,8}|\d{4,8} is your|do not share|reply stop|text stop|"
                       r"unsubscribe|msg&data rates)\b", re.I)


def classify(msg, context=()):
    text = msg.get("text") or ""
    sender = msg.get("sender") or {}
    handle = sender.get("handle") or ""
    known = bool(sender.get("name"))
    direct = not msg.get("group")
    wider = " ".join([text] + [c for c in context if c])
    reasons, review = [], False
    if AUTOMATED.search(text) or re.fullmatch(r"\d{3,6}", handle):
        level, reasons = "INFORMATIONAL", ["automated_or_code"]
    elif EMERGENCY.search(text):
        reasons = ["emergency_language"]
        if known and direct:
            level = "TIME_CRITICAL"
        else:
            level, review = "ACTION_REQUIRED", True
            reasons.append("unknown_sender" if not known else "group_conversation")
    elif URGENT.search(text) and TODAY.search(wider) and known and direct:
        level, reasons = "TIME_CRITICAL", ["urgent_same_day_deadline"]
    elif INTERVIEW.search(wider):
        level, reasons = "ACTION_REQUIRED", ["interview_scheduling"]
        review = not known
    elif (DEADLINE.search(wider) or TODAY.search(wider)) and ASKS.search(text):
        level, reasons = "ACTION_REQUIRED", ["deadline_request"]
        review = not known
    elif URGENT.search(text):
        level, reasons, review = "ATTENTION", ["urgent_language"], True
    elif ASKS.search(text) and (direct or re.search(r"\bjim\b", text, re.I)):
        level, reasons = "ATTENTION", ["direct_request" if direct else "mentions_jim"]
    else:
        level, reasons = "INFORMATIONAL", ["no_signal"]
    if not text:
        review, reasons = True, reasons + ["no_text"]
    who = sender.get("name") or handle or "the sender"
    action = {"TIME_CRITICAL": "Call or reply to %s now." % who, "ACTION_REQUIRED": "Reply to %s; there is a request with a deadline or scheduling." % who,
              "ATTENTION": "Read and reply to %s when convenient." % who, "INFORMATIONAL": "No action needed."}[level]
    if review:
        action = "Check this one yourself: the signals are ambiguous. " + action
    return {"level": level, "reasons": reasons, "review": review, "next_action": action}


def verdicts(msgs):
    """(message, verdict) for every incoming message of a `recent` list, each judged with up to two earlier messages of its own conversation."""
    out = []
    for i, m in enumerate(msgs):
        earlier = [x.get("text") for x in msgs[i + 1:] if x["chat"] == m["chat"]][:2]      # newest first, so later entries are earlier messages
        out.append((m, classify(m, earlier)))
    return out


def urgent(recent):
    """`recent` result -> the classified view: every incoming message gets a level; flagged ones (above INFORMATIONAL, or to review) are listed."""
    if recent.get("messages") is None:
        return {k: recent[k] for k in ("channel", "health", "sync", "observed_at", "source")} | {"hours": recent.get("hours"), "summary": None, "flagged": None}
    summary, flagged = {level: 0 for level in LEVELS}, []
    for m, verdict in verdicts(recent["messages"]):
        summary[verdict["level"]] += 1
        if verdict["level"] != "INFORMATIONAL" or verdict["review"]:
            flagged.append({k: m[k] for k in ("ref", "chat", "sent_at", "sender", "group", "chat_name", "unread", "text")} | verdict)
    order = {level: n for n, level in enumerate(reversed(LEVELS))}
    flagged.sort(key=lambda f: (order[f["level"]], f["sent_at"]), reverse=False)
    return {k: recent[k] for k in ("channel", "health", "sync", "observed_at", "source", "hours")} | {"summary": summary, "flagged": flagged}


def run(argv, now=None, path=ENDPOINT, fetcher=fetch):
    """argv -> one validated result, or None for a form outside the allowlist."""
    now = now or datetime.now(timezone.utc)
    hours = 24
    if argv[:1] in (["recent"], ["urgent"]) and len(argv) == 3 and argv[1] == "--hours" and re.fullmatch(r"[1-9][0-9]?", argv[2]) and int(argv[2]) <= 72:
        hours = int(argv[2])
    elif argv[:1] in (["recent"], ["urgent"]) and len(argv) == 1:
        pass
    elif argv == ["attention"] or argv == ["counts"]:
        pass
    elif len(argv) == 2 and argv[0] == "conversation" and re.fullmatch(r"c?[1-9][0-9]{0,8}", argv[1]):
        chat = int(argv[1].lstrip("c"))
    else:
        return None
    kind = argv[0]
    request = {"attention": REQUEST, "counts": b"counts\n", "recent": b"recent %d\n" % hours, "urgent": b"recent %d\n" % hours}.get(kind)
    if kind == "conversation":
        request = b"conversation %d\n" % chat
    try:
        host, port = endpoint(path)
        data = fetcher(host, port, request, MAX_REPLY if kind == "attention" else MAX_CONTENT_REPLY)
    except (OSError, ValueError):
        data = b""
    if kind == "attention":
        return validate(data, now)
    if kind == "counts":
        return _content(data, now, "counts", {}, None)
    if kind == "conversation":
        return _content(data, now, "messages", {"chat": "c%d" % chat}, 20)
    recent = _content(data, now, "messages", {"hours": hours}, 50)
    recent.setdefault("hours", hours)
    return recent if kind == "recent" else urgent(recent)


def _page(data, now):
    """One `page`/`next` reply -> (state, None), or (None, refusal health). A page that claims `more` must be full; its cursor must name its last message."""
    state, refused = _envelope(data, now, MAX_CONTENT_REPLY)
    if refused:
        return None, refused
    if state.get("health") != "CURRENT":
        return None, state.get("health")
    try:
        msgs, cur = state["messages"], state["cursor"]
        ok = (set(state) == PAGE_KEYS and state["limit"] == PAGE_SIZE and isinstance(state["more"], bool) and state["contacts"] in ("CURRENT", "UNAVAILABLE", "NOT_NEEDED")
              and isinstance(msgs, list) and len(msgs) <= PAGE_SIZE and all(_message_ok(m) and not m["from_me"] for m in msgs)
              and (not state["more"] or len(msgs) == PAGE_SIZE)
              and [_when(m["sent_at"]) for m in msgs] == sorted(_when(m["sent_at"]) for m in msgs))
        if ok and msgs:
            ok = (isinstance(cur, dict) and set(cur) == {"date", "rowid"} and all(isinstance(cur[k], int) and not isinstance(cur[k], bool) and cur[k] > 0 for k in cur)
                  and msgs[-1]["ref"] == "m%d" % cur["rowid"])
        elif ok:
            ok = cur is None and not state["more"]
    except (ValueError, KeyError, TypeError):
        ok = False
    return (state, None) if ok else (None, "DEGRADED")


def scan(hours, now=None, path=ENDPOINT, fetcher=fetch, clock=time.monotonic, today=None):
    """Every incoming message of the last `hours`, paged oldest first until the reader proves the end (`more` false). Complete ONLY then: a page
    cap, the time cap, a cursor that does not advance, a repeated or malformed page, or any source failure leaves it incomplete, with what was read so
    far still returned (newest first) so nothing seen is lost. Private: runs on Oracle; nothing here is printed or kept."""
    now = now or datetime.now(timezone.utc)
    today = today or (lambda: datetime.now(timezone.utc))
    out = {"health": "DEGRADED", "observed_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "messages": [], "pages": 0,
           "complete": False, "truncated": False}
    try:
        host, port = endpoint(path)
    except (OSError, ValueError):
        out["health"] = "DISCONNECTED"
        return out
    start, request, last, seen, msgs = clock(), b"page %d\n" % hours, None, set(), []
    while True:
        if out["pages"] >= MAX_PAGES or clock() - start > SCAN_SECONDS:
            out["truncated"] = True
            break
        try:
            data = fetcher(host, port, request, MAX_CONTENT_REPLY)
        except OSError:
            data = b""
        state, refused = _page(data, today())
        if refused:
            out["health"] = refused
            break
        out["pages"] += 1
        if out["pages"] == 1:
            out["observed_at"] = state["observed_at"]
        refs = [m["ref"] for m in state["messages"]]
        cur = state["cursor"]
        if (len(set(refs)) != len(refs) or seen & set(refs)
                or (cur is not None and last is not None and (cur["date"], cur["rowid"]) <= last)):
            break                                                       # a repeated message or a cursor that went backwards: coverage unproven
        seen |= set(refs)
        msgs += state["messages"]
        if not state["more"]:
            out["health"], out["complete"] = "CURRENT", True
            break
        last = (cur["date"], cur["rowid"])
        request = b"next %d %d\n" % last
    out["messages"] = list(reversed(msgs))
    return out


def attention(now=None, path=ENDPOINT, fetcher=fetch):
    return run(["attention"], now, path, fetcher)


def main(argv):
    result = run(argv)
    if result is None:
        sys.stdout.write("usage: openclaw-imessage attention | counts | recent [--hours 1-72] | conversation <ref> | urgent [--hours 1-72]\n")
        return 2
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
