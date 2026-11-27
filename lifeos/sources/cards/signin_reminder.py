"""The sign-in renewal reminder.

Three days before an OpenClaw model sign-in (Usage meter, Cloud Fit) expires, or once it has lapsed, one Agent
Inbox item is sent per profile per expiry. The key is per profile and per expiry, so the hourly repeat updates the
same row. Counts and fixed words only; a failure never reaches the card.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp

CT = ZoneInfo("America/Chicago")
LEAD = timedelta(days=3)
PROFILES = {"meter": ("Usage meter", "usage meter"), "fit": ("Cloud Fit", "cloud fit")}


def plan(signins, now):
    """-> the inbox items due now. `signins`: {"meter"|"fit": {"state": "OK"|"EXPIRED"|..., "expires": ISO or None}};
    `now`: an aware datetime.
    """
    items = []
    for key, (name, phrase) in PROFILES.items():
        entry = (signins or {}).get(key)
        if not entry:
            continue
        try:
            at = datetime.fromisoformat(entry["expires"]).astimezone(CT) if entry.get("expires") else None
        except ValueError:
            at = None
        lapsed = entry.get("state") in ("EXPIRED", "COOLDOWN")
        if at is not None and not lapsed and at - now > LEAD:
            continue
        if at is None and not lapsed:
            continue
        stamp = at.strftime("%Y%m%d%H%M") if at else "lapsed"
        deadline = f"expires {ct_stamp.stamp(at)}" if at and not lapsed else "has lapsed" if lapsed else "is due"
        subject = f"Renew the {name} sign-in: it {deadline} (about 2 minutes)"
        details = "\n".join(
            (
                f"> Deadline: the {name} sign-in {deadline}. About 2 minutes. Only you can sign in; no password goes through any agent.",
                "",
                "Step | Where | Who",
                "1. Reply with the phrase below | Reply on this item, or tell Claude chat | You",
                "2. OC starts the sign-in on Oracle and sends you a link and a short code | Agent Inbox | OC",
                "3. Open the link yourself, enter the code, sign in with your ChatGPT account | Your browser | You",
                "4. OC reads the new expiry back and marks this item Done | Daily Report, System table | OC",
                "",
                f"Reply **start {phrase} renewal**. When it is done OC answers **{phrase} renewed**.",
                "",
                "Details: LIFE-993; the sign-ins are OpenClaw profiles on Oracle.",
            )
        )
        due = (at - timedelta(hours=12)) if at and not lapsed else now
        items.append(
            {
                "from": "claude-code",
                "subject": subject[:160],
                "urgency": "Now" if lapsed or (at and at - now <= timedelta(days=1)) else "Today",
                "workspace": "Personal",
                "due": due.isoformat(),
                "link": "https://jimmarkunas.atlassian.net/browse/LIFE-993",
                "details": details,
                "key": f"signin-{key}-{stamp}",
            }
        )
    return items


def send_all(items, environ, deliver=None):
    """-> {"sent": n, "failed": n}; one fixed reason word counted per failure."""
    if deliver is None:
        from lifeos.inbox import send_to_jim

        deliver = lambda item: send_to_jim.deliver(item, environ=environ)  # noqa: E731
    sent = failed = 0
    for item in items:
        try:
            result = deliver(item)
        except Exception:  # noqa: BLE001  never reaches the card
            result = {"ok": False}
        sent += int(bool(result.get("ok")))
        failed += int(not result.get("ok"))
    return {"sent": sent, "failed": failed}
