"""Platform alerts: one place that decides what is worth telling a person, once, and how. Pure logic plus two sinks.

`reconcile` compares the active alerts (counts-only facts with a stable key) with the stored state and returns what to
open, keep, resolve and notify: a notification on open, a reminder while open (6 h PAGE, 24 h INFO) and one on
resolve. `ntfy` is the ONE gateway to the phone (the Telegram and WhatsApp copies follow it); it honours the mute
switch and caps a process at SEND_CAP pushes. Text is redacted before it leaves.

CLI for jobs with no database; exits 1 when the push was not accepted:
    python -m lifeos.platform.alerts push --severity page --title T --body B
"""
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from dataclasses import dataclass
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

from lifeos.platform import alert_registry, alert_settings, redact

PAGE, INFO = "page", "info"
REMIND_HOURS = {PAGE: 6, INFO: 24}
PRIORITY = {PAGE: "high", INFO: "low"}
TAGS = {PAGE: "rotating_light", INFO: "information_source"}


@dataclass(frozen=True)
class Alert:
    key: str
    severity: str
    title: str
    detail: str = ""
    action: str = ""


@dataclass
class Plan:
    opened: list
    kept: list
    resolved: list
    notify: list


def reconcile(active: list[Alert], state: dict[str, dict[str, Any]], now: dt.datetime) -> Plan:
    """active: list[Alert]; state: {key: {"severity", "last_notified": datetime|None}} for open alerts."""
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


def render(kind: str, item: Any) -> tuple[str, str, str]:
    if kind == "resolved":
        return redact.redact(f"Resolved: {item}"), "", INFO
    prefix = {"open": "", "remind": "Still open: "}[kind]
    body = " ".join(part for part in (item.detail, f"Next: {item.action}" if item.action else "") if part)
    return redact.redact(prefix + item.title), redact.redact(body), alert_registry.severity(item.key, item.severity)


def hibernated() -> bool:
    return not alert_settings.enabled()


MUTE_VARIABLE = "ALERTS_MUTED_UNTIL"  # silences every channel until a time (ISO 8601; no zone means Central)
SEND_CAP = 5  # pushes one process may send; the next sends one "alerts paused: flood" notice and the rest are held
_flood = {"sent": 0, "paused": False}


def muted(environ: Mapping[str, str] | None = None, now: dt.datetime | None = None) -> bool:
    """True while ALERTS_MUTED_UNTIL names a time that has not passed. Unset or unreadable means NOT muted: a typo must
    never hide a real page.
    """
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


def reset_flood() -> None:
    _flood.update(sent=0, paused=False)


COPY_TAGS = {"telegram": "ch-telegram", "whatsapp": "ch-whatsapp"}  # the Oracle relay forwards a copy only when its tag is on the push


def _post(topic: str, title: str, body: str, severity: str, post: Callable[..., Any] | None = None, channels: Iterable[str] | None = None) -> bool:
    """`channels` (None = all) says which of ntfy / telegram / whatsapp this push is for. Without the phone it goes out
    at the lowest priority, so it reaches the relay but never wakes the phone.
    """
    tags = [t for t in (TAGS.get(severity, ""),) if t] + [COPY_TAGS[c] for c in (channels or ()) if c in COPY_TAGS]
    priority = "min" if channels is not None and "ntfy" not in channels else PRIORITY.get(severity, "default")
    request = urllib.request.Request(f"https://ntfy.sh/{topic}", data=(body or title).encode(), method="POST", headers={
        "Title": title.encode("ascii", "replace").decode(), "Priority": priority, "Tags": ",".join(tags)})
    try:
        with (post or urllib.request.urlopen)(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception:                                                                        # noqa: BLE001 - alerting must never stop a pipeline
        return False


def ntfy(topic: str, title: str, body: str, severity: str, post: Callable[..., Any] | None = None, channels: Iterable[str] | None = None) -> bool:
    """The one gateway to the phone (ntfy). -> True when accepted; never raises. Nothing is sent (False) while alerts are
    off or muted or past SEND_CAP; the first push past the cap sends ONE "alerts paused: flood" notice instead.
    """
    if hibernated() or not topic or muted():
        return False
    if _flood["sent"] >= SEND_CAP:
        if not _flood["paused"]:
            _flood["paused"] = True
            _post(topic, "Alerts paused: flood", f"More than {SEND_CAP} alerts in one run. The rest were held back; the run's log has the counts.", INFO, post)
        return False
    _flood["sent"] += 1
    return _post(topic, title, body, severity, post, channels)


def push(key: str, title: str, body: str, topic: str | None = None, post: Callable[..., Any] | None = None) -> bool:
    """Send one registered alert: severity comes from the registry and an unlisted key is refused (False). Switch, mute,
    cap and saved channels apply as for `ntfy`. `topic` defaults to NTFY_TOPIC.
    """
    if not isinstance(key, str) or not key:
        return False
    spec = alert_registry.lookup(key)
    chosen = alert_settings.channels(key)
    if spec is None or not chosen:
        return False
    return ntfy((topic if topic is not None else os.environ.get("NTFY_TOPIC", "")).strip(), redact.redact(title), redact.redact(body), spec.severity, post, chosen)


def deliver(plan: Plan, topic: str, send: Callable[..., bool] | None = None, failed_keys: set[str] | None = None) -> dict[str, int]:
    """Send the plan's notifications -> counts. `send` is a test fake. `failed_keys` is filled with each failed push's
    key so the caller records every other one as delivered.
    """
    sent = failed = skipped = 0
    failed_keys = set() if failed_keys is None else failed_keys
    for kind, item in plan.notify:
        title, body, severity = render(kind, item)
        key = item if isinstance(item, str) else item.key
        chosen = alert_settings.channels(key)
        if send is None and alert_registry.lookup(key) is None:
            failed += 1
            continue
        if send is None and not chosen:
            skipped += 1  # every channel is off: handled, not failed, so not retried
            continue
        if (send(topic, title, body, severity) if send else ntfy(topic, title, body, severity, channels=chosen)):
            sent += 1
        else:
            failed += 1
            failed_keys.add(key)
    return {"sent": sent, "failed": failed, **({"skipped": skipped} if skipped else {})}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["push"])
    parser.add_argument("--key", required=True, help="the registered alert this push is (lifeos/platform/alert_registry.py); its severity comes from there")
    parser.add_argument("--severity", choices=[PAGE, INFO], default=None, help="only for --key test_push")
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    args = parser.parse_args(argv)
    spec = alert_registry.lookup(args.key)
    if spec is None:
        print(json.dumps({"push": "unregistered_key"}))
        return 2  # an unlisted key is a defect in the caller (checked first so a typo shows even while alerts are off)
    if hibernated():
        print(json.dumps({"push": "hibernated"}))
        return 0
    if muted():
        print(json.dumps({"push": "muted"}))
        return 0
    severity = args.severity if args.key == "test_push" and args.severity else spec.severity
    chosen = alert_settings.channels(args.key)
    if not chosen:
        print(json.dumps({"push": "no_channel"}))
        return 0
    ok = ntfy(os.environ.get("NTFY_TOPIC", ""), redact.redact(args.title), redact.redact(args.body), severity, channels=chosen)
    print(json.dumps({"push": "sent" if ok else "not_sent"}))
    return 0 if ok else 1  # a push the provider did not accept turns the step red


if __name__ == "__main__":
    sys.exit(main())
