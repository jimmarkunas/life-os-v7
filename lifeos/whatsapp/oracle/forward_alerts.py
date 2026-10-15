#!/usr/bin/python3 -I
"""COMM-1.1 (LIFE-28, Jim's approval Oct 15 2026): copy every LIFE OS phone alert into Jim's WhatsApp self-chat and his Telegram DM with the
LIFE OS bot, alongside ntfy. WhatsApp self-chat messages raise no phone notification (they come from Jim's own account); the bot's do.

It polls ntfy every POLL seconds for messages after the last forwarded one (a long-lived stream is buffered by urllib and never yields).
V7 already decides which events alert (Action Required / Time Critical, deduplication, quiet hours) and already redacts them to counts and fixed
words before posting to ntfy. This always-on user service on Oracle follows the same ntfy topic and sends each alert, unchanged, to the ONE
allowlisted recipient of each OpenClaw channel (WhatsApp: Jim's number = self-chat; Telegram: Jim's user id = his chat with the bot) with
`openclaw message send`. Each channel keeps its own position, so one channel's failure never repeats or drops an alert on the other. It adds no alert, decision or schedule of its own.

The topic is the only credential: resolved from Bitwarden at start through the existing resolver (`lifeos/github-actions/NTFY_TOPIC`), kept in memory.
Only the last forwarded ntfy message id is written (0600), so a restart resumes with `since=<id>`: nothing is lost or sent twice. A missing secret,
a recipient list that is not exactly one number, or a failed send is retried later; nothing else is ever messaged. Stdlib only; no output."""
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


def send(to, body, run=subprocess.run, channel="whatsapp"):
    done = run([OPENCLAW, "message", "send", "--channel", channel, "--target", to, "-m", body], capture_output=True, text=True, timeout=90)
    return done.returncode == 0


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


def follow(name, to, opener=urllib.request.urlopen, sender=send, channel="whatsapp"):
    """One poll for one channel: every message after its last forwarded one, oldest first. A failed send stops here so the next poll retries it."""
    since = last_id(channel) or "10m"
    with opener(f"https://ntfy.sh/{name}/json?poll=1&since={since}", timeout=30) as stream:
        lines = [line.decode("utf-8", "replace") for line in stream]
        for e in events(lines):
            if not sender(to, text(e)):
                return
            remember(e["id"], channel)


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
