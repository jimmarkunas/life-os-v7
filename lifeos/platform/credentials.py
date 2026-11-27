"""The list of provider credentials LIFE OS uses, and what is known about each one's expiry.

`credential_list.py` holds NAMES, providers, purpose, where each is read and a date when known; never a value.
expiry_source: provider_reported | manual | none ("No expiry exposed": the provider gives no date) | no_expiry ("No
fixed expiry": nothing expires, for a reason in `source_label`). lifeos/jobs/alerts.py turns every entry with a date
into the `expiry_*` alert (PAGE, 7 days ahead). The Alert settings screen reads this list on Oracle and never Notion,
so a Notion problem cannot silence a warning.
"""
import copy
import datetime as dt
from pathlib import Path

from lifeos.platform.credential_list import CREDENTIALS, NOT_ENVIRONMENT, PATTERNS, PROVIDED

SOURCES = {"provider_reported": "Provider reported", "manual": "Manual", "none": "No expiry exposed", "no_expiry": "No fixed expiry"}
WARN_AHEAD_DAYS = 7
REQUIRED = ("key", "label", "provider", "used_for", "read_in", "expiry_source", "expires", "how_read", "renew")
OPTIONAL = ("source_label",)


def load():
    """Every entry with the keys most entries omit filled in: no expiry source, no date, nothing to renew."""
    return [{"expiry_source": "none", "expires": None, "renew": "", **copy.deepcopy(entry)} for entry in CREDENTIALS]


def config(root=Path(__file__).resolve().parents[1]):
    """The environment-variable name registry (names, kind, owner, purpose; never a value): each row is a line `NAME kind
    purpose` in its owner package's lifeos/<package>/config.txt (`kind:owner` names an owner that is not a package);
    the groups that are not rows stay in credential_list.py.
    """
    rows = []
    for path in sorted(root.glob("*/config.txt")):
        for line in path.read_text().splitlines():
            name, kind, purpose = line.split(" ", 2)
            kind, _, owner = kind.partition(":")
            rows.append({"name": name, "kind": kind, "owner": owner or path.parent.name, "purpose": purpose})
    return copy.deepcopy({"provided": PROVIDED, "names": sorted(rows, key=lambda row: row["name"]), "patterns": PATTERNS, "not_environment": NOT_ENVIRONMENT})


def days_left(entry, today):
    if not entry.get("expires"):
        return None
    return (dt.date.fromisoformat(entry["expires"]) - today).days


def status(entry, today):
    days = days_left(entry, today)
    if days is None:
        return "No fixed expiry" if entry.get("expiry_source") == "no_expiry" else "Unknown"
    return "Expired" if days < 0 else "Warning" if days <= WARN_AHEAD_DAYS else "OK"


def rows(today, credentials=None):
    out = []
    for entry in (load() if credentials is None else credentials):
        out.append({"key": entry["key"], "credential": entry["label"], "provider": entry["provider"], "used_for": entry["used_for"], "read_in": entry["read_in"],
                    "source": entry.get("source_label") or SOURCES[entry["expiry_source"]], "expires": entry["expires"], "days_left": days_left(entry, today), "status": status(entry, today),
                    "warn_ahead_days": WARN_AHEAD_DAYS})
    return out


def expiries(credentials=None):
    """The entries that have a date, in the shape lifeos/jobs/alerts.py `detect` reads: {key: {label, expires, renew}}.
    """
    return {e["key"]: {"label": e["label"], "expires": e["expires"], "renew": e["renew"]} for e in (load() if credentials is None else credentials) if e.get("expires")}
