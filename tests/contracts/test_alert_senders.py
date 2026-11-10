"""LIFE-528 outcome 1: the alert gateway (lifeos/platform/alerts.py) is the only place that sends an alert.

A sender is any code that talks to a notification service: the phone push service (ntfy), the Telegram or WhatsApp bot (`openclaw message send`, the Bot API), Graph, email, Slack,
a macOS notification. Every file in lifeos/ and .github/ that contains one of those patterns must be in ALLOWED below with the reason it may, and every ALLOWED entry must still match, so the
list cannot go stale. A new sender anywhere else fails this test. The full inventory with the decisions is docs/platform/alert-senders.md."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUFFIXES = (".py", ".mjs", ".js", ".sh", ".yml", ".yaml", ".plist", ".service", ".c")
PATTERNS = {
    "ntfy": re.compile(r"ntfy\.sh"),
    "telegram_api": re.compile(r"api\.telegram\.org"),
    "graph": re.compile(r"graph\.facebook\.com"),
    "email": re.compile(r"smtplib|sendmail|sendgrid"),
    "chat_webhook": re.compile(r"hooks\.slack\.com|slack\.com/api|twilio|pushover"),
    "openclaw_send": re.compile(r"""message["',\s]+send|message send"""),
    "mac_notification": re.compile(r"osascript|display notification|terminal-notifier"),
}
ALLOWED = {
    "lifeos/platform/alerts.py": ({"ntfy"}, "THE GATEWAY: the one POST to the phone push service; Telegram and WhatsApp copies follow it (LIFE-73, LIFE-75)."),
    "lifeos/whatsapp/oracle/forward_alerts.py": ({"ntfy", "openclaw_send"}, "THE RELAY: reads the gateway's ntfy topic and copies each alert to Telegram and WhatsApp; it creates no alert of its own."),
    "lifeos/whatsapp/oracle/lifeos-alerts-whatsapp.service": ({"openclaw_send"}, "The relay's systemd unit (describes the relay above)."),
    "lifeos/edge/oracle/openclaw-jira.mjs": ({"openclaw_send"}, "NOT an alert: the single-use approval prompt (with buttons) Jim answers before a Jira write; the phone push service cannot carry buttons. "
                                                               "Kept on the existing OpenClaw channel; routing it through the gateway is Jim's decision (LIFE-528 comment)."),
}


def found_in(text):
    return {name for name, pattern in PATTERNS.items() if pattern.search(text)}


def scan(files):
    """files: {relative path: text} -> {path: kinds} for every file containing a sender pattern."""
    return {path: kinds for path, text in files.items() if (kinds := found_in(text))}


def repo_files():
    out = {}
    for base in ("lifeos", ".github"):
        for path in sorted((ROOT / base).rglob("*")):
            if path.is_file() and path.suffix in SUFFIXES and "__pycache__" not in path.parts:
                out[str(path.relative_to(ROOT))] = path.read_text(errors="ignore")
    return out


def problems(found):
    out = []
    for path, kinds in sorted(found.items()):
        allowed = ALLOWED.get(path)
        if allowed is None:
            out.append(f"{path} sends an alert outside the gateway ({', '.join(sorted(kinds))})")
        elif not kinds <= allowed[0]:
            out.append(f"{path} uses a sender kind it is not allowed ({', '.join(sorted(kinds - allowed[0]))})")
    for path in sorted(set(ALLOWED) - set(found)):
        out.append(f"{path} is allowed but no longer contains a sender: remove it from ALLOWED")
    return out


class OnlyTheGatewaySends(unittest.TestCase):
    def test_nothing_in_the_repo_sends_an_alert_outside_the_allowed_files(self):
        self.assertEqual(problems(scan(repo_files())), [])

    def test_the_only_post_to_the_phone_push_service_is_in_the_gateway(self):
        posts = [p for p, text in repo_files().items() if re.search(r"https://ntfy\.sh/\{[^\n]*", text) and re.search(r'method="POST"', text)]
        self.assertEqual(posts, ["lifeos/platform/alerts.py"])                                        # the relay only READS the topic (a GET poll)

    def test_every_allowed_entry_has_a_reason(self):
        for path, (kinds, reason) in ALLOWED.items():
            self.assertTrue(kinds and len(reason) > 20, path)

    def test_negative_control_a_direct_send_elsewhere_fails_and_a_clean_file_passes(self):
        planted = {"lifeos/jobs/new_thing.py": 'urllib.request.Request(f"https://ntfy.sh/{topic}", data=b"x")',
                   "lifeos/jobs/bot.py": 'requests.post("https://api.telegram.org/bot1/sendMessage")',
                   "lifeos/jobs/mac.py": 'subprocess.run(["osascript", "-e", "display notification"])',
                   "lifeos/jobs/fine.py": "from lifeos.platform import alerts\nalerts.push('quiet_24h', 't', 'b')"}
        flagged = problems(scan(planted))
        self.assertEqual(len([f for f in flagged if "outside the gateway" in f]), 3)
        self.assertFalse([f for f in flagged if "fine.py" in f])

    def test_negative_control_an_allowed_file_may_not_grow_a_new_kind_of_sender(self):
        found = {"lifeos/platform/alerts.py": {"ntfy", "telegram_api"}}
        self.assertTrue([f for f in problems(found) if "not allowed" in f])


if __name__ == "__main__":
    unittest.main()
