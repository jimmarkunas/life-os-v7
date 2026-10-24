"""Production switches in the repository (Jim, Oct 24 2026 standing grant, LIFE-3 comment 11095).

Each switch lives in switches.json with its state and the bounds it runs within, so a change is a reviewed PR under the merge rule, never an unseen setting.
A repository variable of the same name still wins in one direction only: "off" is Jim's kill switch, whatever the file says; "on" turns a switch on by hand.
Anything else (unset, empty, a typo) defers to the file. Never covered: paid spend, sending mail, applying to jobs, secrets, deleting data."""
import json
from pathlib import Path

FILE = Path(__file__).with_name("switches.json")


def state(name, environ):
    """True when the switch is on: the repository variable's "off" or "on" first, else the file's state."""
    value = (environ.get(name) or "").strip().lower()
    if value in ("off", "on"):
        return value == "on"
    try:
        return (json.loads(FILE.read_text()).get(name) or {}).get("state") == "on"
    except (OSError, ValueError):
        return False                                                   # an unreadable file is off, never on
