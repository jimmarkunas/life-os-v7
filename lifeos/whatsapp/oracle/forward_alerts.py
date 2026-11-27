#!/usr/bin/python3 -I
"""Copy every LIFE OS phone alert from ntfy into the WhatsApp self-chat and the Telegram DM with the bot.

Polls ntfy and sends each alert, unchanged, to the single allowlisted recipient of each OpenClaw channel. The
ntfy topic is the only credential: resolved from Bitwarden, kept in memory, re-read every TOPIC_REFRESH seconds.
Stdlib only.

Delivery is classified from `openclaw message send --json`, never the exit code: DELIVERED on verified evidence;
NOT_DELIVERED when provably never sent (retried up to MAX_ATTEMPTS); AMBIGUOUS otherwise (never re-sent). The
attempt is written before the send, so a killed relay treats it as AMBIGUOUS. An unreadable state file holds
that channel. A push goes only to channels it is tagged for, at most RELAY_CAP per channel per clock hour."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".local/state/lifeos-alerts")
LAST = os.path.join(STATE, "last-id")
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
MAX_ATTEMPTS = 3
DIAGNOSTICS, DIAGNOSTICS_KEEP = "deliveries.jsonl", 500
DELIVERED, NOT_DELIVERED, AMBIGUOUS, GAVE_UP, HELD = "DELIVERED", "NOT_DELIVERED", "AMBIGUOUS", "GAVE_UP", "HELD"
CAPPED, PAUSED = "CAPPED", "PAUSED"
RELAY_CAP = 6
PAUSE_TEXT = f"⏸ LIFE OS alerts paused: more than {RELAY_CAP} in an hour. Copies resume next hour; ntfy has them all."
TOPIC_REFRESH = 900
TOPIC_RETRY = 60
QUIET_AFTER = 6 * 3600
TOPIC_CHANGED, QUIET = "TOPIC_CHANGED", "RELAY_QUIET"
ID = re.compile(r"[A-Za-z0-9]{1,32}")


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
    title, body = (event.get("title") or "").strip(), (event.get("message") or "").strip()
    head = f"{ICON.get(event.get('priority'), '🔔')} {title or 'LIFE OS'}"
    return (head + ("\n" + body if body and body != title else ""))[:MAX_TEXT]


def events(lines):
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("event") == "message" and isinstance(e.get("id"), str) and ID.fullmatch(e["id"]):
            yield e


def result_json(stdout):
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
    """-> (outcome, fixed class) from the --json result only; the exit code is kept only in diagnostics."""
    result = result_json(stdout)
    if result is None:
        return AMBIGUOUS, "no_result"  # a thrown error prints only to stderr: unprovable
    if result.get("dryRun") is not False or result.get("action") not in (None, "send"):
        return AMBIGUOUS, "not_a_send_result"
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    status = result.get("deliveryStatus")
    if result.get("sentBeforeError") is True or status == "partial_failed":
        return DELIVERED, "sent_before_error"
    if result.get("ok") is False:
        if status == "suppressed":
            return NOT_DELIVERED, "suppressed_before_send"
        return AMBIGUOUS, "failed_unproven"
    if payload.get("status") == "delivery_queued" or payload.get("delivered") is False:
        return AMBIGUOUS, "gateway_queued"
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
        return AMBIGUOUS, "timeout", None
    except OSError:
        return NOT_DELIVERED, "not_started", None
    return (*classify(done.returncode, done.stdout), done.returncode)


def _position(channel):
    return LAST if channel == "whatsapp" else f"{LAST}-{channel}"


def cursor(channel="whatsapp"):
    """-> ("absent", None) only when no cursor file exists, ("valid", id), or ("invalid", None): the channel is
    held."""
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


def _store(path, text):
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def remember(message_id, channel="whatsapp"):
    _store(_position(channel), message_id)


def _attempts_path(channel):
    return os.path.join(STATE, f"attempts-{channel}")


def attempt_state(channel):
    """-> ("absent", {}), ("valid", {"id", "count", "pending"}) or ("invalid", {}): invalid holds the channel."""
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
    _store(_attempts_path(channel), json.dumps(value))


def diagnose(channel, message_id, outcome, cls, code=None, now=time.time):
    try:
        path = os.path.join(STATE, DIAGNOSTICS)
        try:
            with open(path) as f:
                lines = f.read().splitlines()[-(DIAGNOSTICS_KEEP - 1):]
        except OSError:
            lines = []
        lines.append(json.dumps({"at": int(now()), "channel": channel, "id": message_id, "outcome": outcome, "class": cls, "exit": code}, sort_keys=True))
        _store(path, "\n".join(lines) + "\n")
    except OSError:
        pass


def _rate_path(channel):
    return os.path.join(STATE, f"rate-{channel}")


def rate_state(channel, now=time.time):
    """-> ("ok", {"hour", "count", "paused"}) for this clock hour, or ("invalid", {}): held, never reset."""
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
    _store(_rate_path(channel), json.dumps(value))


def _done(channel, message_id):
    remember(message_id, channel)
    try:
        os.unlink(_attempts_path(channel))
    except OSError:
        pass


def _held(channel, cls):
    try:
        with open(os.path.join(STATE, DIAGNOSTICS)) as f:
            last = [json.loads(x) for x in f.read().splitlines() if x.strip()]
        last = next((x for x in reversed(last) if x.get("channel") == channel), {})
    except (OSError, ValueError):
        last = {}
    if not (last.get("outcome") == HELD and last.get("class") == cls):
        diagnose(channel, None, HELD, cls)


def follow(name, to, opener=urllib.request.urlopen, sender=send, channel="whatsapp", now=time.time):
    """One poll for one channel: every message after its last forwarded one, oldest first. Only a proven
    non-delivery stops here (the next poll retries it)."""
    position, since = cursor(channel)
    if position == "invalid":
        return _held(channel, "invalid_cursor")
    if attempt_state(channel)[0] == "invalid":
        return _held(channel, "invalid_attempts")
    if rate_state(channel, now)[0] == "invalid":
        return _held(channel, "invalid_rate")
    with opener(f"https://ntfy.sh/{name}/json?poll=1&since={since or '10m'}", timeout=30) as stream:
        lines = [line.decode("utf-8", "replace") for line in stream]
        seen = 0
        for e in events(lines):
            seen += 1
            if "ch-" + channel not in (e.get("tags") or []):
                remember(e["id"], channel)
                continue
            state = attempts(channel)
            mine = state if state.get("id") == e["id"] else {}
            if mine.get("pending"):
                # the relay died mid-send last time: it may have been delivered; never replayed
                diagnose(channel, e["id"], AMBIGUOUS, "interrupted")
                _done(channel, e["id"])
                continue
            if mine.get("count", 0) >= MAX_ATTEMPTS:
                diagnose(channel, e["id"], GAVE_UP, "max_attempts")
                _done(channel, e["id"])
                continue
            count = mine.get("count", 0) + 1
            rate = rate_state(channel, now)[1]
            if rate["count"] >= RELAY_CAP:
                if not rate["paused"]:
                    _write_rate(channel, {**rate, "paused": True})
                    got = sender(to, PAUSE_TEXT)
                    diagnose(channel, e["id"], PAUSED, "rate_cap", got[2] if isinstance(got, tuple) else None)
                diagnose(channel, e["id"], CAPPED, "rate_cap")
                _done(channel, e["id"])
                continue
            _write_rate(channel, {**rate, "count": rate["count"] + 1})
            _write_attempts(channel, {"id": e["id"], "count": count, "pending": True})
            got = sender(to, text(e))
            outcome, cls, code = got if isinstance(got, tuple) else ((DELIVERED, "sender_true", 0) if got else (NOT_DELIVERED, "sender_false", 1))
            diagnose(channel, e["id"], outcome, cls, code)
            if outcome == NOT_DELIVERED:
                if count >= MAX_ATTEMPTS:
                    diagnose(channel, e["id"], GAVE_UP, "max_attempts")
                    _done(channel, e["id"])
                    continue
                _write_attempts(channel, {"id": e["id"], "count": count, "pending": False})
                return seen  # keep the order: the next poll retries this alert first
            _done(channel, e["id"])
        return seen


def step(state, now=time.time, resolve=topic, lookup=recipient, poll=follow, send_to=send):
    t = now()
    if state.get("name") is None or t - state.get("read_at", 0) >= TOPIC_REFRESH:
        fresh = resolve()
        if fresh:
            if state.get("name") and fresh != state["name"]:
                diagnose("relay", None, TOPIC_CHANGED, "reread", now=now)
            state["name"], state["read_at"] = fresh, t
        else:
            state["read_at"] = t - TOPIC_REFRESH + TOPIC_RETRY
    seen = 0
    for channel in CHANNELS:
        to = lookup(channel=channel)
        if state.get("name") and to:
            got = poll(state["name"], to, sender=lambda text_, b, c=channel: send_to(text_, b, channel=c), channel=channel)
            seen += got if isinstance(got, int) else 0
    state.setdefault("last_seen", t)
    if seen:
        state["last_seen"] = t
    elif t - max(state["last_seen"], state.get("quiet_logged", 0)) >= QUIET_AFTER:
        diagnose("relay", None, QUIET, "no_messages", now=now)
        state["quiet_logged"] = t


def main():
    os.environ["PATH"] = os.path.dirname(NODE) + ":" + os.environ.get("PATH", "/usr/bin:/bin")
    state, delay = {}, POLL
    while True:
        try:
            step(state)
            delay = POLL
        except Exception:
            state["name"], delay = None, min(delay * 2, 300)
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(main())
