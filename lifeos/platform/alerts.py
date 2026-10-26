"""Platform alerts: one place that decides what is worth telling a person, once, and how (D53). Pure logic plus two sinks; no OS knowledge.

An Alert is a counts-only fact with a stable key. `reconcile` compares the alerts that are active now with the stored state and returns what to
open, keep, resolve and notify: an alert notifies when it opens, again as a reminder while it stays open (6 h for PAGE, 24 h for INFO), and once
when it resolves. Detectors live in each OS; sinks are small adapters (phone push through ntfy, a GitHub issue). Text is redacted before it leaves.

LIFE-73: `ntfy` is the ONE gateway to the phone (the Telegram and WhatsApp copies follow ntfy). It honours the mute switch ALERTS_MUTED_UNTIL and caps a process at SEND_CAP pushes,
then sends a single "alerts paused: flood" notice and holds the rest.

"Sent" means the push service answered 2xx: it proves acceptance, not that the phone showed it (that is the phone's notification settings).

CLI for jobs with no database (the report job, the watchdog); exits 1 when the push was not accepted:  python -m lifeos.platform.alerts push --severity page --title T --body B"""
from dataclasses import dataclass
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

from lifeos.platform import redact

PAGE, INFO = "page", "info"
REMIND_HOURS = {PAGE: 6, INFO: 24}
PRIORITY = {PAGE: "high", INFO: "low"}
TAGS = {PAGE: "rotating_light", INFO: "information_source"}


@dataclass(frozen=True)
class Alert:
    key: str                     # stable, e.g. "sources_failing"
    severity: str                # PAGE | INFO
    title: str                   # one line
    detail: str = ""             # counts only: never a title, a URL or a person
    action: str = ""             # the one next step


@dataclass
class Plan:
    opened: list
    kept: list
    resolved: list               # keys
    notify: list                 # (kind, Alert | key) with kind in open | remind | resolved


def reconcile(active, state, now):
    """active: list[Alert]; state: {key: {"severity", "last_notified": datetime|None}} for currently open alerts."""
    plan = Plan([], [], [], [])
    live = {a.key: a for a in active}
    for key, alert in live.items():
        known = state.get(key)
        if not known:
            plan.opened.append(alert)
            plan.notify.append(("open", alert))
            continue
        plan.kept.append(alert)
        last = known.get("last_notified")
        if last is None or now - last >= dt.timedelta(hours=REMIND_HOURS.get(alert.severity, 24)):
            plan.notify.append(("remind", alert))
    for key, known in state.items():
        if key not in live:
            plan.resolved.append(key)
            if known.get("severity") == PAGE:
                plan.notify.append(("resolved", key))
    return plan


def render(kind, item):
    """-> (title, body, severity). Redacted; counts only."""
    if kind == "resolved":
        return redact.redact(f"Resolved: {item}"), "", INFO
    prefix = {"open": "", "remind": "Still open: "}[kind]
    body = " ".join(part for part in (item.detail, f"Next: {item.action}" if item.action else "") if part)
    return redact.redact(prefix + item.title), redact.redact(body), item.severity


HIBERNATED = True        # LIFE-71 (Jim, Oct 18 2026): every LIFE OS phone push is suspended, with no expiry. Only a reviewed commit made on Jim's explicit instruction turns it back on.


MUTE_VARIABLE = "ALERTS_MUTED_UNTIL"       # LIFE-73: the repository variable that silences every channel until a time (ISO 8601; a time with no zone is Central)
SEND_CAP = 5                                # LIFE-73: pushes one process may send; the next one sends a single "alerts paused: flood" notice and the rest are held back
_flood = {"sent": 0, "paused": False}


def muted(environ=None, now=None):
    """True while ALERTS_MUTED_UNTIL names a time that has not passed. Unset, empty, unreadable or past all mean NOT muted: a typo must never hide a real page."""
    raw = ((environ if environ is not None else os.environ).get(MUTE_VARIABLE) or "").strip()
    if not raw:
        return False
    try:
        until = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=ZoneInfo("America/Chicago"))
    return (now or dt.datetime.now(dt.timezone.utc)) < until


def reset_flood():
    """A new process starts at zero; tests call this between cases."""
    _flood.update(sent=0, paused=False)


def _post(topic, title, body, severity, post=None):
    request = urllib.request.Request(f"https://ntfy.sh/{topic}", data=(body or title).encode(), method="POST", headers={
        "Title": title.encode("ascii", "replace").decode(), "Priority": PRIORITY.get(severity, "default"), "Tags": TAGS.get(severity, "")})
    try:
        with (post or urllib.request.urlopen)(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception:                                                                        # noqa: BLE001 - alerting must never stop a pipeline
        return False


def ntfy(topic, title, body, severity, post=None):
    """The one gateway to the phone (LIFE-73): every LIFE OS push goes through here, and the Telegram and WhatsApp copies follow ntfy, so a rule here covers all three.
    Phone push through ntfy (https://ntfy.sh): the topic name is the secret. Returns True when accepted. Never raises. Not sent, and False, while HIBERNATED (LIFE-71),
    while muted (ALERTS_MUTED_UNTIL), or past SEND_CAP in this process; the first push past the cap sends ONE "alerts paused: flood" notice instead."""
    if HIBERNATED or not topic or muted():
        return False
    if _flood["sent"] >= SEND_CAP:
        if not _flood["paused"]:
            _flood["paused"] = True
            _post(topic, "Alerts paused: flood", f"More than {SEND_CAP} alerts in one run. The rest were held back; the run's log has the counts.", INFO, post)
        return False
    _flood["sent"] += 1
    return _post(topic, title, body, severity, post)


def deliver(plan, topic, send=ntfy):
    """Send the plan's notifications; -> counts. INFO opens and reminders still push (low priority, no sound)."""
    sent = failed = 0
    for kind, item in plan.notify:
        title, body, severity = render(kind, item)
        if send(topic, title, body, severity):
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["push"])
    parser.add_argument("--severity", choices=[PAGE, INFO], default=PAGE)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    args = parser.parse_args(argv)
    if HIBERNATED:
        print(json.dumps({"push": "hibernated"}))
        return 0                                              # LIFE-71: a suspended push is not a failed step
    if muted():
        print(json.dumps({"push": "muted"}))
        return 0                                              # LIFE-73: a muted push is not a failed step either
    ok = ntfy(os.environ.get("NTFY_TOPIC", ""), redact.redact(args.title), redact.redact(args.body), args.severity)
    print(json.dumps({"push": "sent" if ok else "not_sent"}))
    return 0 if ok else 1                                     # a push the provider did not accept turns the step red, never a silent success


if __name__ == "__main__":
    sys.exit(main())
