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
now runs with --json and is classified from OpenClaw's own result: DELIVERED (exit 0, a messageId, sentBeforeError, or a send result that is not ok:false),
NOT_DELIVERED (an explicit ok:false with no delivery evidence) or AMBIGUOUS (no result: a crash, timeout or unparseable output). Only NOT_DELIVERED is retried,
at most MAX_ATTEMPTS times per alert per channel; AMBIGUOUS is never re-sent (at most once: a possible loss is recorded, a flood is impossible). The attempt is
written before the send, so a relay killed mid-send finds it on restart and treats it as AMBIGUOUS instead of replaying. Every outcome is appended to a
size-capped 0600 diagnostics file (deliveries.jsonl: time, channel, ntfy id, outcome, exit code, a fixed class), never the alert text."""
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
DELIVERED, NOT_DELIVERED, AMBIGUOUS, GAVE_UP = "DELIVERED", "NOT_DELIVERED", "AMBIGUOUS", "GAVE_UP"


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
        if isinstance(e, dict) and e.get("event") == "message" and isinstance(e.get("id"), str):
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
    """-> (outcome, fixed class). Delivery evidence wins over the exit code: the floods were deliveries that exited non-zero."""
    if returncode == 0:
        return DELIVERED, "exit0"
    result = result_json(stdout)
    if result is None:
        return AMBIGUOUS, "no_result"
    if result.get("messageId") or result.get("sentBeforeError") is True:
        return DELIVERED, "evidence_nonzero_exit"
    if result.get("ok") is False:
        return NOT_DELIVERED, "result_not_ok"
    if result.get("action") == "send" and result.get("dryRun") is False:
        return DELIVERED, "ok_result_nonzero_exit"                       # the send succeeded; something after it (e.g. a plugin stop hook) failed
    return AMBIGUOUS, "unrecognized_result"


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


def last_id(channel="whatsapp"):
    try:
        with open(_position(channel)) as f:
            value = f.read().strip()
        return value if re.fullmatch(r"[A-Za-z0-9]{1,32}", value) else None
    except OSError:
        return None


def remember(message_id, channel="whatsapp"):
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    tmp = _position(channel) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(message_id)
    os.replace(tmp, _position(channel))


def _attempts_path(channel):
    return os.path.join(STATE, f"attempts-{channel}")


def attempts(channel):
    """-> {"id", "count", "pending"} for the alert this channel is working on, or {}."""
    try:
        with open(_attempts_path(channel)) as f:
            value = json.load(f)
        return value if isinstance(value, dict) and isinstance(value.get("id"), str) and type(value.get("count")) is int else {}
    except (OSError, ValueError):
        return {}


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


def _done(channel, message_id):
    remember(message_id, channel)
    try:
        os.unlink(_attempts_path(channel))
    except OSError:
        pass


def follow(name, to, opener=urllib.request.urlopen, sender=send, channel="whatsapp"):
    """One poll for one channel: every message after its last forwarded one, oldest first. Only an explicit non-delivery stops here (the next poll retries
    it, at most MAX_ATTEMPTS in all); a delivery, an ambiguous outcome or an exhausted alert moves the position on, so no alert can be sent forever."""
    since = last_id(channel) or "10m"
    with opener(f"https://ntfy.sh/{name}/json?poll=1&since={since}", timeout=30) as stream:
        lines = [line.decode("utf-8", "replace") for line in stream]
        for e in events(lines):
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
