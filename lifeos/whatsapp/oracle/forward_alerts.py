#!/usr/bin/python3 -I
"""COMM-1.1 (LIFE-28, Jim's approval Oct 15 2026): copy every LIFE OS phone alert into Jim's WhatsApp self-chat and his Telegram DM with the
LIFE OS bot, alongside ntfy. WhatsApp self-chat messages raise no phone notification (they come from Jim's own account); the bot's do.

It polls ntfy every POLL seconds for messages after the last forwarded one (a long-lived stream is buffered by urllib and never yields).
V7 already decides which events alert (Action Required / Time Critical, deduplication, quiet hours) and already redacts them to counts and fixed
words before posting to ntfy. This always-on user service on Oracle follows the same ntfy topic and sends each alert, unchanged, to the ONE
allowlisted recipient of each OpenClaw channel (WhatsApp: Jim's number = self-chat; Telegram: Jim's user id = his chat with the bot) with
`openclaw message send`. Each channel keeps its own position, so one channel's failure never repeats or drops an alert on the other. It adds no alert, decision or schedule of its own.

The topic is the only credential: resolved from Bitwarden at start through the existing resolver (`lifeos/github-actions/NTFY_TOPIC`), kept in memory.
Only the last forwarded ntfy message id is written (0600), so a restart resumes with `since=<id>`. A missing secret or a recipient list that is not
exactly one number is retried later; nothing else is ever messaged. Stdlib only; no output.

LIFE-71 (the Oct 17-18 floods): `openclaw message send` sometimes exited non-zero AFTER delivering, and this relay re-sent the same alert every poll. Each send
now runs with --json and is classified from OpenClaw 2026.9.9's own result contract, never from the exit code: DELIVERED only on verified evidence (a
messageId, a "sent"/"delivered" status, or sentBeforeError / partial_failed: something went out); NOT_DELIVERED only when the send provably never reached the
transport (deliveryStatus "suppressed" without sentBeforeError, or the CLI never started); everything else is AMBIGUOUS (a generic ok:false or "failed", a
gateway-queued delivery, no or contradictory result, a timeout). Only NOT_DELIVERED is retried, at most MAX_ATTEMPTS times per alert per channel; AMBIGUOUS is
never re-sent (at most once: a possible loss is recorded, a flood is impossible). The attempt is written before the send, so a relay killed mid-send finds it
on restart and treats it as AMBIGUOUS instead of replaying. Saved state fails closed: an attempt or cursor file that exists but is unreadable or invalid holds
that channel (no poll, no send; one HELD diagnostic) until a person repairs it; only an absent file is a fresh start. Every outcome is appended to a
size-capped 0600 diagnostics file (deliveries.jsonl: time, channel, ntfy id, outcome, exit code, a fixed class), never the alert text.

LIFE-75 (the Alert settings page): the V7 gateway tags each push with the copies it is meant for (`ch-telegram`, `ch-whatsapp`); this relay forwards a copy to a channel only when
that tag is present, so what Jim ticks per alert on the page decides it. An untagged message is skipped silently and the position moves on.

LIFE-73 (the flood cap): each channel forwards at most RELAY_CAP copies per clock hour. The next one sends ONE "paused" notice and the rest of that hour's copies are dropped
(ntfy keeps every original). The count is persisted (rate-<channel>, 0600) and written before the send; an unreadable count file holds the channel instead of lifting the cap."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".local/state/lifeos-alerts")
LAST = os.path.join(STATE, "last-id")                                   # WhatsApp (the first channel); other channels: last-id-<channel>
CHANNELS = ("whatsapp", "telegram")
OPENCLAW = os.path.join(HOME, ".openclaw/tools/node/bin/openclaw")
OPENCLAW_CONFIG = os.path.join(HOME, ".openclaw/openclaw.json")
NODE = os.path.join(HOME, ".openclaw/tools/node/bin/node")
RESOLVER = os.path.join(HOME, ".local/bin/openclaw-bws-resolver.mjs")
BWS_ENV = os.path.join(HOME, ".config/bws/openclaw.env")
SECRET = "lifeos/github-actions/NTFY_TOPIC"
MAX_TEXT = 1500
POLL = 20
ICON = {5: "🚨", 4: "🔔"}
MAX_ATTEMPTS = 3                                                        # LIFE-71: explicit non-delivery is retried at most this often per alert per channel
DIAGNOSTICS, DIAGNOSTICS_KEEP = "deliveries.jsonl", 500
DELIVERED, NOT_DELIVERED, AMBIGUOUS, GAVE_UP, HELD = "DELIVERED", "NOT_DELIVERED", "AMBIGUOUS", "GAVE_UP", "HELD"
CAPPED, PAUSED = "CAPPED", "PAUSED"
RELAY_CAP = 6                                                           # LIFE-73: forwards per channel per clock hour; the next alert sends ONE "paused" notice and the rest of the hour's copies are dropped (ntfy keeps the originals)
PAUSE_TEXT = f"⏸ LIFE OS alerts paused: more than {RELAY_CAP} in an hour. Copies resume next hour; ntfy has them all."
ID = re.compile(r"[A-Za-z0-9]{1,32}")                                  # an ntfy message id, or a Unix time written as a fresh-start cursor


def topic(run=subprocess.run):
    env = dict(os.environ)
    with open(BWS_ENV) as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    out = run([NODE, RESOLVER], input=json.dumps({"protocolVersion": 1, "provider": "bws", "ids": [SECRET]}),
              capture_output=True, text=True, timeout=30, env=env)
    value = ((json.loads(out.stdout or "{}").get("values") or {}).get(SECRET) or "").strip() if out.returncode == 0 else ""
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else None


def recipient(path=OPENCLAW_CONFIG, channel="whatsapp"):
    """The single entry on the channel's OpenClaw allowlist (WhatsApp: E.164 number; Telegram: numeric user id); anything else is None (refuse)."""
    with open(path) as f:
        allow = ((json.load(f).get("channels") or {}).get(channel) or {}).get("allowFrom") or []
    if len(allow) != 1:
        return None
    if channel == "whatsapp":
        digits = re.sub(r"\D", "", str(allow[0]))
        return "+" + digits if 8 <= len(digits) <= 15 else None
    value = str(allow[0])
    return value if re.fullmatch(r"\d{5,15}", value) else None


def text(event):
    """An ntfy message event -> the WhatsApp text: icon by priority, the title, then the body (both already redacted by V7)."""
    title, body = (event.get("title") or "").strip(), (event.get("message") or "").strip()
    head = f"{ICON.get(event.get('priority'), '🔔')} {title or 'LIFE OS'}"
    return (head + ("\n" + body if body and body != title else ""))[:MAX_TEXT]


def events(lines):
    """ntfy's JSON stream -> message events only (open/keepalive skipped, malformed lines ignored)."""
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("event") == "message" and isinstance(e.get("id"), str) and ID.fullmatch(e["id"]):
            yield e


def result_json(stdout):
    """OpenClaw's --json result object, or None when there is none (it may be pretty-printed, and other lines may precede it)."""
    out = (stdout or "").strip()
    for candidate in (out, out[out.find("{"):out.rfind("}") + 1] if "{" in out else ""):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def classify(returncode, stdout):
    """-> (outcome, fixed class), from OpenClaw 2026.9.9's --json result only (buildMessageCliJson / resolveMessageSendOutcome); the exit code proves nothing
    either way (the floods were deliveries that exited 1, and exit 0 can carry an explicit failure). It is kept only in the diagnostics line."""
    result = result_json(stdout)
    if result is None:
        return AMBIGUOUS, "no_result"                                    # a thrown error (pre- or post-send) prints only to stderr: unprovable
    if result.get("dryRun") is not False or result.get("action") not in (None, "send"):
        return AMBIGUOUS, "not_a_send_result"
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    status = result.get("deliveryStatus")
    if result.get("sentBeforeError") is True or status == "partial_failed":
        return DELIVERED, "sent_before_error"                            # something reached the transport: never re-sent
    if result.get("ok") is False:
        if status == "suppressed":
            return NOT_DELIVERED, "suppressed_before_send"               # refused before the transport, with nothing sent
        return AMBIGUOUS, "failed_unproven"                              # "failed" or unknown: it may have reached the transport
    if payload.get("status") == "delivery_queued" or payload.get("delivered") is False:
        return AMBIGUOUS, "gateway_queued"                               # OpenClaw: the gateway owns retry; do not resend
    if isinstance(result.get("messageId"), str) and result["messageId"].strip():
        return DELIVERED, "message_id"
    if status in ("sent", "delivered") or payload.get("deliveryStatus") in ("sent", "delivered"):
        return DELIVERED, "status_sent"
    return AMBIGUOUS, "no_delivery_evidence"


def send(to, body, run=subprocess.run, channel="whatsapp"):
    """-> (outcome, class, exit code). Never raises for a send problem."""
    try:
        done = run([OPENCLAW, "message", "send", "--json", "--channel", channel, "--target", to, "-m", body], capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return AMBIGUOUS, "timeout", None                                  # it may have sent: never retried
    except OSError:
        return NOT_DELIVERED, "not_started", None                          # the CLI never ran: nothing was sent
    return (*classify(done.returncode, done.stdout), done.returncode)


def _position(channel):
    return LAST if channel == "whatsapp" else f"{LAST}-{channel}"


def cursor(channel="whatsapp"):
    """-> ("absent", None) only when no cursor file exists (a first start), ("valid", id), or ("invalid", None) for one that exists but cannot be read or
    is malformed: that channel is held, never restarted from the 10-minute window."""
    try:
        with open(_position(channel)) as f:
            value = f.read().strip()
    except FileNotFoundError:
        return "absent", None
    except (OSError, UnicodeDecodeError):
        return "invalid", None
    return ("valid", value) if ID.fullmatch(value) else ("invalid", None)


def last_id(channel="whatsapp"):
    state, value = cursor(channel)
    return value


def remember(message_id, channel="whatsapp"):
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    tmp = _position(channel) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(message_id)
    os.replace(tmp, _position(channel))


def _attempts_path(channel):
    return os.path.join(STATE, f"attempts-{channel}")


def attempt_state(channel):
    """-> ("absent", {}) when no attempt file exists, ("valid", {"id", "count", "pending"}) or ("invalid", {}) for one that exists but cannot be read or is
    not exactly that shape (an id like ntfy's, a count 1..MAX_ATTEMPTS, a boolean pending). An invalid one holds the channel: it may hide a sent alert."""
    try:
        with open(_attempts_path(channel)) as f:
            value = json.load(f)
    except FileNotFoundError:
        return "absent", {}
    except (OSError, ValueError):
        return "invalid", {}
    ok = (isinstance(value, dict) and set(value) == {"id", "count", "pending"} and isinstance(value["id"], str) and ID.fullmatch(value["id"])
          and type(value["count"]) is int and 1 <= value["count"] <= MAX_ATTEMPTS and type(value["pending"]) is bool)
    return ("valid", value) if ok else ("invalid", {})


def attempts(channel):
    return attempt_state(channel)[1]


def _write_attempts(channel, value):
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    tmp = _attempts_path(channel) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f)
    os.replace(tmp, _attempts_path(channel))


def diagnose(channel, message_id, outcome, cls, code=None, now=time.time):
    """One line per send outcome (no alert text), kept to the last DIAGNOSTICS_KEEP lines. Best effort: it never stops the relay."""
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        path = os.path.join(STATE, DIAGNOSTICS)
        try:
            with open(path) as f:
                lines = f.read().splitlines()[-(DIAGNOSTICS_KEEP - 1):]
        except OSError:
            lines = []
        lines.append(json.dumps({"at": int(now()), "channel": channel, "id": message_id, "outcome": outcome, "class": cls, "exit": code}, sort_keys=True))
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _rate_path(channel):
    return os.path.join(STATE, f"rate-{channel}")


def rate_state(channel, now=time.time):
    """-> ("ok", {"hour", "count", "paused"}) for this clock hour (a missing file or an older hour starts at zero), or ("invalid", {}) for a file that exists but is not
    exactly that shape: the channel is held, never reset, because a reset would lift the cap."""
    hour = int(now() // 3600)
    try:
        with open(_rate_path(channel)) as f:
            value = json.load(f)
    except FileNotFoundError:
        return "ok", {"hour": hour, "count": 0, "paused": False}
    except (OSError, ValueError):
        return "invalid", {}
    good = (isinstance(value, dict) and set(value) == {"hour", "count", "paused"} and type(value["hour"]) is int and type(value["count"]) is int
            and value["count"] >= 0 and type(value["paused"]) is bool)
    if not good:
        return "invalid", {}
    return ("ok", value) if value["hour"] == hour else ("ok", {"hour": hour, "count": 0, "paused": False})


def _write_rate(channel, value):
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    tmp = _rate_path(channel) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f)
    os.replace(tmp, _rate_path(channel))


def _done(channel, message_id):
    remember(message_id, channel)
    try:
        os.unlink(_attempts_path(channel))
    except OSError:
        pass


def _held(channel, cls):
    """One HELD diagnostic per cause (not one every poll): the channel waits for a person; nothing is polled or sent."""
    try:
        with open(os.path.join(STATE, DIAGNOSTICS)) as f:
            last = [json.loads(x) for x in f.read().splitlines() if x.strip()]
        last = next((x for x in reversed(last) if x.get("channel") == channel), {})
    except (OSError, ValueError):
        last = {}
    if not (last.get("outcome") == HELD and last.get("class") == cls):
        diagnose(channel, None, HELD, cls)


def follow(name, to, opener=urllib.request.urlopen, sender=send, channel="whatsapp", now=time.time):
    """One poll for one channel: every message after its last forwarded one, oldest first. Only a proven non-delivery stops here (the next poll retries
    it, at most MAX_ATTEMPTS in all); a delivery, an ambiguous outcome or an exhausted alert moves the position on, so no alert can be sent forever. A cursor
    or attempt file that exists but is unreadable or invalid holds this channel (fail closed); the other channel is unaffected."""
    position, since = cursor(channel)
    if position == "invalid":
        return _held(channel, "invalid_cursor")
    if attempt_state(channel)[0] == "invalid":
        return _held(channel, "invalid_attempts")
    if rate_state(channel, now)[0] == "invalid":
        return _held(channel, "invalid_rate")
    with opener(f"https://ntfy.sh/{name}/json?poll=1&since={since or '10m'}", timeout=30) as stream:
        lines = [line.decode("utf-8", "replace") for line in stream]
        for e in events(lines):
            if "ch-" + channel not in (e.get("tags") or []):                  # LIFE-75: a copy goes to this channel only when the push was tagged for it (the Alert settings page)
                remember(e["id"], channel)
                continue
            state = attempts(channel)
            mine = state if state.get("id") == e["id"] else {}
            if mine.get("pending"):
                diagnose(channel, e["id"], AMBIGUOUS, "interrupted")          # the relay died mid-send last time: it may have been delivered; never replayed
                _done(channel, e["id"])
                continue
            if mine.get("count", 0) >= MAX_ATTEMPTS:
                diagnose(channel, e["id"], GAVE_UP, "max_attempts")
                _done(channel, e["id"])
                continue
            count = mine.get("count", 0) + 1
            rate = rate_state(channel, now)[1]
            if rate["count"] >= RELAY_CAP:                                  # LIFE-73: past the hour's cap this copy is dropped (ntfy keeps it), after ONE notice
                if not rate["paused"]:
                    _write_rate(channel, {**rate, "paused": True})
                    got = sender(to, PAUSE_TEXT)
                    diagnose(channel, e["id"], PAUSED, "rate_cap", got[2] if isinstance(got, tuple) else None)
                diagnose(channel, e["id"], CAPPED, "rate_cap")
                _done(channel, e["id"])
                continue
            _write_rate(channel, {**rate, "count": rate["count"] + 1})     # counted BEFORE the send, like the attempt
            _write_attempts(channel, {"id": e["id"], "count": count, "pending": True})     # written BEFORE the send
            got = sender(to, text(e))
            outcome, cls, code = got if isinstance(got, tuple) else ((DELIVERED, "sender_true", 0) if got else (NOT_DELIVERED, "sender_false", 1))
            diagnose(channel, e["id"], outcome, cls, code)
            if outcome == NOT_DELIVERED:
                if count >= MAX_ATTEMPTS:
                    diagnose(channel, e["id"], GAVE_UP, "max_attempts")
                    _done(channel, e["id"])
                    continue
                _write_attempts(channel, {"id": e["id"], "count": count, "pending": False})
                return                                                   # keep the order: the next poll retries this alert first
            _done(channel, e["id"])                                      # DELIVERED or AMBIGUOUS: never sent again


def main():
    os.environ["PATH"] = os.path.dirname(NODE) + ":" + os.environ.get("PATH", "/usr/bin:/bin")      # openclaw runs on this node
    name, delay = None, POLL
    while True:
        try:
            name = name or topic()
            for channel in CHANNELS:
                to = recipient(channel=channel)
                if name and to:
                    follow(name, to, sender=lambda t, b, c=channel: send(t, b, channel=c), channel=channel)
            delay = POLL
        except Exception:                                                   # network, Bitwarden or OpenClaw unavailable: back off and resume
            name, delay = None, min(delay * 2, 300)
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(main())
