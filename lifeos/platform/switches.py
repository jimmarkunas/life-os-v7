"""Production switches kept in switches.json with their state and bounds, so a change is a reviewed PR.

A repository variable of the same name wins in one direction only: "off" is the kill switch, "on" turns a switch on by
hand; anything else defers to the file. Never covered: paid spend, sending mail, applying to jobs, secrets, deleting
data.
"""

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
        return False
