"""Platform alerts: one place that decides what is worth telling a person, once, and how (D53). Pure logic plus two sinks; no OS knowledge.

An Alert is a counts-only fact with a stable key. `reconcile` compares the alerts that are active now with the stored state and returns what to
open, keep, resolve and notify: an alert notifies when it opens, again as a reminder while it stays open (6 h for PAGE, 24 h for INFO), and once
when it resolves. Detectors live in each OS; sinks are small adapters (phone push through ntfy, a GitHub issue). Text is redacted before it leaves.

CLI for jobs with no database (the report job, the watchdog):  python -m lifeos.platform.alerts push --severity page --title T --body B"""
from dataclasses import dataclass
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

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


def ntfy(topic, title, body, severity, post=None):
    """Phone push through ntfy (https://ntfy.sh): the topic name is the secret. Returns True when accepted. Never raises."""
    if not topic:
        return False
    request = urllib.request.Request(f"https://ntfy.sh/{topic}", data=(body or title).encode(), method="POST", headers={
        "Title": title.encode("ascii", "replace").decode(), "Priority": PRIORITY.get(severity, "default"), "Tags": TAGS.get(severity, "")})
    try:
        with (post or urllib.request.urlopen)(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception:                                                                        # noqa: BLE001 - alerting must never stop a pipeline
        return False


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
    ok = ntfy(os.environ.get("NTFY_TOPIC", ""), redact.redact(args.title), redact.redact(args.body), args.severity)
    print(json.dumps({"push": "sent" if ok else "not_sent"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
