"""LIFE-528 outcome 2 (Jim, Nov 10 2026, comments 12547 and 12548): the list of provider credentials LIFE OS uses, and what is known about each one's expiry.

`credential_list.py` holds NAMES, providers, what each is used for, where it is read, and a date when one is known. Never a value, a secret id or a Bitwarden item id.
expiry_source: provider_reported (the date came from the provider's own evidence) | manual (a person entered it, or the provider can report it but nothing reads it yet) |
none ("No expiry exposed": the provider gives no date, so this screen cannot warn and says so).

Warnings ride the existing path: lifeos/jobs/alerts.py turns every entry that has a date into the `expiry_*` alert (PAGE, 7 days ahead) through the alert gateway. The Alert settings screen
reads this list on Oracle; it never reads Notion, so a Notion problem cannot silence a warning. The Token Expiry database in Notion is a read-only mirror of `rows()`."""
import copy
import datetime as dt

from lifeos.platform.credential_list import CONFIG, CREDENTIALS

SOURCES = {"provider_reported": "Provider reported", "manual": "Manual", "none": "No expiry exposed"}
WARN_AHEAD_DAYS = 7
REQUIRED = ("key", "label", "provider", "used_for", "read_in", "expiry_source", "expires", "how_read", "renew")


def load():
    return copy.deepcopy(CREDENTIALS)


def config():
    """LIFE-560: the environment-variable name registry (names, kind, owner, purpose; never a value)."""
    return copy.deepcopy(CONFIG)


def days_left(entry, today):
    if not entry.get("expires"):
        return None
    return (dt.date.fromisoformat(entry["expires"]) - today).days


def status(entry, today):
    """OK | Warning | Expired | Unknown (no date to check)."""
    days = days_left(entry, today)
    if days is None:
        return "Unknown"
    return "Expired" if days < 0 else "Warning" if days <= WARN_AHEAD_DAYS else "OK"


def rows(today, credentials=None):
    """One row per credential for a screen or a mirror: names, providers, dates and a status only."""
    out = []
    for entry in (load() if credentials is None else credentials):
        out.append({"key": entry["key"], "credential": entry["label"], "provider": entry["provider"], "used_for": entry["used_for"], "read_in": entry["read_in"],
                    "source": SOURCES[entry["expiry_source"]], "expires": entry["expires"], "days_left": days_left(entry, today), "status": status(entry, today),
                    "warn_ahead_days": WARN_AHEAD_DAYS})
    return out


def expiries(credentials=None):
    """The entries that have a date, in the shape lifeos/jobs/alerts.py `detect` reads: {key: {label, expires, renew}}."""
    return {e["key"]: {"label": e["label"], "expires": e["expires"], "renew": e["renew"]} for e in (load() if credentials is None else credentials) if e.get("expires")}
