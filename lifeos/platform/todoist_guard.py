"""The guard in front of the governed Todoist writes. Stdlib only; OFF unless TODOIST_GUARD is exactly `on`.

- Stolen client credential: each client may use only the routine verbs; alerts, never blocks, on the first write after 24 quiet
  hours, any refused verb, a write outside 6 AM to 11 PM CT and over 30 writes in 10 minutes (alert-only for the first 14 days).
- Tricked agent: a move between projects, and any write the client declares came from outside (`source` = `outside`), needs a
  fresh approval: an Agent Inbox row (key `todoist-approval-<id>`) that Jim marks Done within 5 minutes, counted only when the
  last edit was not made by the service's own integration, and used once. Delete and share do not exist.
- Writes are recorded (time, client id, verb, outcome; never text) in the state directory; the first call after seven days
  sends one weekly receipt to the Agent Inbox (event-driven, no timer)."""

import hashlib
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp

ROUTINE = (
    "todoist_task_create",
    "todoist_task_update",
    "todoist_task_complete",
    "todoist_task_reopen",
    "todoist_comment_add",
)
APPROVED_ONLY = ("todoist_task_move",)
WRITES = ROUTINE + APPROVED_ONLY
CLIENTS = (  # the eight clients that hold Todoist grants; each may use the routine verbs only
    "claude-chat",
    "chatgpt-chat",
    "claude-code-mac",
    "mac-claude-code",
    "oracle-claude-code",
    "openclaw",
    "hermes-librarian",
    "codex",
)
QUIET_S, RATE_N, RATE_S, HOURS = 24 * 3600, 30, 600, range(6, 23)
APPROVAL_TTL_S, RECEIPT_S = 300, 7 * 86400
SENDER, CT = "todoist-guard", ZoneInfo("America/Chicago")
OUTSIDE = "outside"


class Refused(ValueError):
    """The message and `reason` are a fixed lower-case code; `detail` carries the approval id when one is needed."""

    def __init__(self, reason, **detail):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def request_id(client, op, args):
    body = {k: v for k, v in sorted(args.items()) if k != "source"}
    return hashlib.sha256(json.dumps([client, op, body], sort_keys=True).encode()).hexdigest()[:12]


def _ct(epoch):
    return ct_stamp.stamp(datetime.fromtimestamp(epoch, timezone.utc))


def _report(record):
    import sys

    print(json.dumps(record, separators=(",", ":"), sort_keys=True), file=sys.stderr, flush=True)


def _background(send, item, report):
    def go():
        try:
            send(item)
            report({"event": "todoist_alert", "key": item["key"], "outcome": "sent"})
        except Exception as error:  # noqa: BLE001 - an alert that cannot be delivered never breaks a request
            report(
                {
                    "event": "todoist_alert",
                    "key": item["key"],
                    "outcome": "send_failed",
                    "reason": getattr(error, "reason", type(error).__name__),
                }
            )

    threading.Thread(target=go, daemon=True).start()


class InboxApprovals:
    """Approval rows in the Agent Inbox. `client` is the service's Notion client, `source` the inbox data source id, `deliver`
    sends one item (the service's send_to_jim)."""

    def __init__(self, client, source, deliver):
        self.client, self.source, self.deliver, self.bot = client, source, deliver, None

    def _row(self, key):
        found = (
            self.client.query_data_source(
                self.source, {"page_size": 2, "filter": {"property": "Key", "rich_text": {"equals": key}}}
            ).get("results")
            or []
        )
        return found[0] if len(found) == 1 else None

    def _bot(self):
        if self.bot is None:
            self.bot = str((self.client.call_once("GET", "/users/me") or {}).get("id") or "")
        return self.bot

    def state(self, rid, now):
        """-> `none`, `pending`, `approved`, `stale` or `used` for the approval row of request `rid`."""
        row = self._row("todoist-approval-" + rid)
        if row is None:
            return "none", None
        props = row.get("properties") or {}
        status = ((props.get("Status") or {}).get("select") or {}).get("name")
        if status == "Dismissed":
            return "used", row
        if status != "Done":
            return "pending", row
        who = str((row.get("last_edited_by") or {}).get("id") or "")
        edited = row.get("last_edited_time") or ""
        try:
            age = now - datetime.fromisoformat(edited.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return "pending", row
        if not who or who == self._bot():
            return "pending", row  # marked by the service itself, never by a person
        return ("approved" if 0 <= age <= APPROVAL_TTL_S else "stale"), row

    def ask(self, rid, client, op, summary, now):
        self.deliver(
            {
                "key": "todoist-approval-" + rid,
                "subject": f"Approve Todoist {op.removeprefix('todoist_')} for {client}? {summary}"[:160],
                "urgency": "Now",
                "workspace": "Personal",
                "details": f"Request {rid}. Mark this item Done within 5 minutes to approve it once; leave it to refuse. "
                f"Asked {_ct(now)}.",
            }
        )

    def reset(self, row, now):
        """A stale approval goes back to New so a fresh Done is needed."""
        self.client.call_once(
            "PATCH", f"/pages/{row['id']}", {"properties": {"Status": {"select": {"name": "New"}}}}
        )

    def consume(self, row):
        self.client.call_once(
            "PATCH", f"/pages/{row['id']}", {"properties": {"Status": {"select": {"name": "Dismissed"}}}}
        )


class Guard:
    """send(item): one Agent Inbox item; approvals: InboxApprovals or a test double; state_dir: where the ledger lives
    (None keeps everything in memory); clock -> epoch seconds."""

    def __init__(self, send, approvals, state_dir=None, clock=time.time, dispatch=_background, report=_report):
        self.send, self.approvals, self.state_dir = send, approvals, state_dir
        self.clock, self.dispatch, self.report = clock, dispatch, report
        self.lock = threading.Lock()
        self.recent, self.alerted, self.last_write, self.last_receipt = {}, set(), {}, None
        self.ledger = []
        self._load()

    # state kept in the service's state directory
    def _path(self, name):
        return os.path.join(self.state_dir, name) if self.state_dir else None

    def _load(self):
        path = self._path("todoist-guard.json")
        try:
            with open(path) as handle:
                doc = json.load(handle)
            self.last_write, self.last_receipt = dict(doc.get("last_write") or {}), doc.get("last_receipt")
        except (OSError, TypeError, ValueError):
            pass
        path = self._path("todoist-writes.jsonl")
        try:
            with open(path) as handle:
                self.ledger = [json.loads(line) for line in handle if line.strip()][-5000:]
        except (OSError, TypeError, ValueError):
            pass

    def _save(self):
        if not self.state_dir:
            return
        try:
            os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
            fd = os.open(self._path("todoist-guard.json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump({"last_write": self.last_write, "last_receipt": self.last_receipt}, handle)
        except OSError as error:
            self.report({"event": "todoist_guard_state", "outcome": "save_failed", "reason": type(error).__name__})

    def _append(self, row):
        self.ledger = (self.ledger + [row])[-5000:]
        path = self._path("todoist-writes.jsonl")
        if not path:
            return
        try:
            os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as handle:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        except OSError as error:
            self.report({"event": "todoist_guard_ledger", "outcome": "append_failed", "reason": type(error).__name__})

    def _alert(self, kind, client, bucket, subject, details):
        key = f"todoist-alert-{kind}-{client}-{bucket}"
        if key in self.alerted:
            return
        self.alerted = set(list(self.alerted)[-200:]) | {key}
        self.dispatch(
            self.send,
            {"key": key, "subject": subject[:160], "urgency": "Today", "workspace": "Personal", "details": details},
            self.report,
        )

    # the three entry points
    def denied(self, client, op, reason):
        """A refused Todoist call (gate or guard): one alert per client, verb and hour."""
        now = self.clock()
        with self.lock:
            self._alert(
                "denied",
                str(client),
                f"{op}-{int(now // 3600)}",
                f"Todoist verb refused for {client}: {op}",
                f"Refused {_ct(now)}; code {reason}. Alert only: the call was already refused.",
            )

    def before(self, client, op, args):
        """-> (args without `source`, row to consume or None); raises Refused. Reads and unknown verbs are not guarded."""
        if op not in WRITES:
            return args, None
        client = str(client or "unknown")
        rid = request_id(client, op, args)
        if client not in CLIENTS:
            self.denied(client, op, "verb_not_allowed")
            raise Refused("verb_not_allowed")
        outside = args.get("source") == OUTSIDE
        if args.get("source") not in (None, OUTSIDE, "own"):
            raise Refused("bad_source")
        clean = {k: v for k, v in args.items() if k != "source"}
        row = None
        if op in APPROVED_ONLY or outside:
            now = self.clock()
            try:
                state, row = self.approvals.state(rid, now)
                if state != "approved":
                    if state in ("stale", "used") and row is not None:
                        self.approvals.reset(row, now)  # a fresh Done is needed for each use
                    if state in ("none", "stale", "used"):
                        self.approvals.ask(rid, client, op, _summary(op, clean, outside), now)
            except Exception as error:  # noqa: BLE001 - no approval path means no write
                self.report({"event": "todoist_approval", "outcome": "unavailable", "reason": type(error).__name__})
                raise Refused("approval_unavailable") from None
            if state != "approved":
                self.denied(client, op, "approval_required")
                raise Refused("approval_required", approval_id=rid, state=state)
        return clean, row

    def after(self, client, op, outcome, row=None):
        """Record one write attempt; consume its approval; raise the quiet, hours and rate alerts (alert only)."""
        client, now = str(client or "unknown"), self.clock()
        if row is not None:
            try:
                self.approvals.consume(row)
            except Exception as error:  # noqa: BLE001 - never turns a done write into an error
                self.report({"event": "todoist_approval", "outcome": "consume_failed", "reason": type(error).__name__})
        with self.lock:
            self._append({"t": int(now), "c": client, "op": op, "r": outcome})
            if op not in WRITES:
                return
            previous = self.last_write.get(client)
            self.last_write[client] = now
            self._save()
            if previous is not None and now - previous > QUIET_S:
                self._alert(
                    "quiet",
                    client,
                    int(now // 86400),
                    f"Todoist write by {client} after {int((now - previous) // 3600)} quiet hours",
                    f"{_ct(now)}; last write {_ct(previous)}. Alert only.",
                )
            hour = datetime.fromtimestamp(now, CT).hour
            if hour not in HOURS:
                self._alert(
                    "hours",
                    client,
                    datetime.fromtimestamp(now, CT).strftime("%Y%m%d"),
                    f"Todoist write by {client} outside 6 AM to 11 PM CT",
                    f"{_ct(now)}. Alert only.",
                )
            window = self.recent.setdefault(client, deque())
            window.append(now)
            while window and now - window[0] > RATE_S:
                window.popleft()
            if len(window) > RATE_N:
                self._alert(
                    "rate",
                    client,
                    int(now // 3600),
                    f"Todoist writes by {client}: {len(window)} in 10 minutes",
                    f"{_ct(now)}; threshold {RATE_N}. Alert only for the first 14 days (a hard cap is a later decision).",
                )

    def tick(self):
        """Event-driven weekly receipt: the first call after seven days sends one Agent Inbox item of the week's writes."""
        now = self.clock()
        with self.lock:
            if self.last_receipt is None:
                self.last_receipt = now
                self._save()
                return
            if now - self.last_receipt < RECEIPT_S:
                return
            week = [r for r in self.ledger if self.last_receipt <= r.get("t", 0) <= now and r.get("op") in WRITES]
            counts = {}
            for r in week:
                counts.setdefault(r["c"], {}).setdefault(f"{r['op'].removeprefix('todoist_')} {r['r']}", 0)
                counts[r["c"]][f"{r['op'].removeprefix('todoist_')} {r['r']}"] += 1
            lines = [
                f"{c}: " + ", ".join(f"{k} {n}" for k, n in sorted(v.items())) for c, v in sorted(counts.items())
            ] or ["No Todoist writes this week."]
            self.last_receipt = now
            self._save()
            self.dispatch(
                self.send,
                {
                    "key": f"todoist-receipt-{int(now // RECEIPT_S)}",
                    "subject": f"Todoist writes, week to {_ct(now)}: {len(week)} total",
                    "urgency": "FYI",
                    "workspace": "Personal",
                    "details": "\n".join(lines)[:1400],
                },
                self.report,
            )


def _summary(op, args, outside):
    bits = [op.removeprefix("todoist_")]
    for k in ("task_id", "project_id"):
        if args.get(k):
            bits.append(f"{k} {args[k]}")
    if args.get("content") and op != "todoist_comment_add":
        bits.append("text " + str(args["content"])[:60])
    if outside:
        bits.append("content from outside")
    return ", ".join(bits)


def wire(environ, state, inbox_send, inbox_state):
    """Attach the guard to the Todoist state: only when TODOIST_GUARD is exactly `on` and the Agent Inbox is configured."""
    state["guard"] = None
    if (environ.get("TODOIST_GUARD") or "").strip() != "on" or inbox_state.get("client") is None:
        return
    from lifeos.platform import agent_inbox  # noqa: PLC0415 - installed beside this module

    client = inbox_state["client"]

    def send(item):
        return inbox_send.run("send_to_jim", {**item, "_client": SENDER}, inbox_state)

    try:
        source = agent_inbox.locate(client)
    except Exception:  # noqa: BLE001 - without the inbox there is no approval path, so no guard (fail visible)
        state["guard_error"] = "inbox_not_found"
        return
    folder = (environ.get("STATE_DIRECTORY") or "").split(":")[0] or None
    state["guard"] = Guard(send, InboxApprovals(client, source, send), folder)
