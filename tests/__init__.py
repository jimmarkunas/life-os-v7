"""Unit tests fake Gmail, so the shared Gmail gate (lifeos/platform/gmail_gate.py) is switched off for them; its own tests build an enabled one."""

import os

os.environ.setdefault("LIFEOS_GMAIL_GATE", "off")
