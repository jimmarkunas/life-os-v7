"""The three alert switches in Bitwarden: one reader and one writer shared by the Alert settings screen and the Oracle
ticker.

ALERTS_ENABLED, ALERTS_MUTED_UNTIL and ALERT_SETTINGS live in the Bitwarden project LIFE OS ALERTS, found by fixed
item ids (ids are not secrets). Inputs: a machine-account access token (a systemd credential on Oracle) and the `bws`
program. Outputs: {name: value}, an empty value reading as "". A Bitwarden failure raises AlertStoreError with a fixed
code, never a token, a value or a reply.
"""

import json
import os
import subprocess
import tempfile

IDS = {
    "ALERTS_ENABLED": "efc8fc25-0228-4aa4-a7ee-b503015f8a8c",
    "ALERTS_MUTED_UNTIL": "141b6f81-341b-4277-b2b3-b503015f8bd0",
    "ALERT_SETTINGS": "dc0b1ad9-45a8-4e1f-8868-b503015f8c97",
}
NAMES = tuple(IDS)
TIMEOUT = 20


class AlertStoreError(Exception):
    """A fixed code (ALERT_STORE_UNREADABLE, ALERT_STORE_UNWRITABLE); the message never holds a token, a value or
    Bitwarden's reply.
    """


def _bws(token, args, run, environ):
    """Run one `bws` call with the token in the environment only (never an argument) and a throwaway home; -> stdout
    text.
    """
    token = token.decode() if isinstance(token, bytes) else token
    with tempfile.TemporaryDirectory(prefix="lifeos-alerts-") as home:
        env = {
            "PATH": (environ or os.environ).get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": home,
            "BWS_ACCESS_TOKEN": token,
        }  # no BWS_CONFIG_FILE: bws stops with "Config file doesn't exist" when that names a missing file
        done = run(
            [(environ or os.environ).get("LIFEOS_BWS_BIN", "bws"), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    if done.returncode != 0:
        raise OSError("bws failed")
    return done.stdout


def read(token, run=subprocess.run, environ=None):
    """-> {name: value} for the three switches. A value Bitwarden holds as empty reads as ""; anything unreadable raises
    ALERT_STORE_UNREADABLE.
    """
    out = {}
    for name, item in IDS.items():
        try:
            doc = json.loads(_bws(token, ["secret", "get", item, "-o", "json"], run, environ))
            value = doc.get("value")
            if doc.get("id") != item or doc.get("key", "").rsplit("/", 1)[-1] != name or not isinstance(value, str):
                raise ValueError("wrong item")
        except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
            raise AlertStoreError("ALERT_STORE_UNREADABLE") from None
        out[name] = value
    return out


def write(token, values, run=subprocess.run, environ=None):
    """Save each named value to its item. Unknown names are refused; a Bitwarden failure raises ALERT_STORE_UNWRITABLE
    (the caller reads back before it says saved).
    """
    if any(name not in IDS for name in values):
        raise AlertStoreError("ALERT_STORE_UNWRITABLE")
    for name, value in values.items():
        try:
            _bws(token, ["secret", "edit", IDS[name], "--value", value, "-o", "none"], run, environ)
        except (OSError, subprocess.SubprocessError):
            raise AlertStoreError("ALERT_STORE_UNWRITABLE") from None
