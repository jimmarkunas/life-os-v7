"""LIFE-528 step 0: the deployed-version drift check. A GitHub merge does not update the Oracle VM, so the watchdog compares each repo-owned deployed file with `main`.

For every surface it records: the source blob on `main` (git's own sha1 of the file), the hash of the installed file, the systemd unit that activates it and that unit's state.
DEPLOYED needs all three to agree and be known: installed hash equals main, and the unit is active. A piece that cannot be read (GitHub, the file, systemd) is UNKNOWN, never DEPLOYED.
A hash that differs, a file that is gone, or a unit that is not running is DRIFT. Counts, names of repo files and fixed words only: no file content, no credential.

The ticker's watch command calls `incident_signal` once per run and opens or closes one quiet issue through the existing incident helper (alert key `deploy_drift`, INFO, phone only).
Pure logic: every read is passed in, so the tests inject all of them."""
import hashlib
from typing import NamedTuple

DEPLOYED, DRIFT, UNKNOWN = "DEPLOYED", "DRIFT", "UNKNOWN"
MISSING = object()          # the read said "no such file"; an unreadable file is None instead


class Surface(NamedTuple):
    name: str
    repo_path: str
    installed: str
    unit: str


_TICKER_MODULES = ("gate.py", "alerts.py", "alert_registry.py", "alert_settings.py", "redact.py", "drift.py")      # the MODULES list in install-ticker.sh
SURFACES = (
    Surface("ticker", "lifeos/edge/ticker/ticker.py", "/opt/lifeos-ticker/ticker.py", "lifeos-ticker-watch.timer"),
    *(Surface("ticker/" + m, "lifeos/platform/" + m, "/opt/lifeos-ticker/lifeos/platform/" + m, "lifeos-ticker-watch.timer") for m in _TICKER_MODULES),
    Surface("offsite-backup", "lifeos/edge/backup/offsite_backup.py", "/opt/lifeos-offsite/offsite_backup.py", "lifeos-offsite-backup.timer"),
)


def blob_sha(data):
    """git's object id for a file: sha1 of "blob <size>\\0" + content, what the GitHub contents API reports as `sha`."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def classify(expected, installed, unit_state):
    """expected: main's blob sha or None; installed: bytes, MISSING or None (unreadable); unit_state: 'active' | other word | None (unreadable)."""
    if expected is None or installed is None:
        return UNKNOWN
    if installed is MISSING or blob_sha(installed) != expected:
        return DRIFT
    if unit_state is None:
        return UNKNOWN
    return DEPLOYED if unit_state == "active" else DRIFT


def check(expected_for, read, unit_state_for, surfaces=SURFACES):
    """-> [{"surface", "state", "installed_hash", "unit", "unit_state"}], one per surface. The three callables do the reading; any of them raising reads as unknown."""
    rows = []
    for s in surfaces:
        def safe(call, *args):
            try:
                return call(*args)
            except Exception:                                                  # noqa: BLE001 - an unreadable piece is UNKNOWN, never a crash and never DEPLOYED
                return None
        expected, installed, unit_state = safe(expected_for, s.repo_path), safe(read, s.installed), safe(unit_state_for, s.unit)
        rows.append({"surface": s.name, "state": classify(expected, installed, unit_state), "unit": s.unit, "unit_state": unit_state or UNKNOWN,
                     "installed_hash": blob_sha(installed)[:12] if isinstance(installed, bytes) else ("missing" if installed is MISSING else UNKNOWN)})
    return rows


def incident_signal(rows):
    """True when any surface drifted (open), False when every surface is DEPLOYED (recovered), None otherwise (an UNKNOWN never opens or closes anything)."""
    states = {r["state"] for r in rows}
    if DRIFT in states:
        return True
    return False if states == {DEPLOYED} else None


def summary(rows):
    counts = {state: sum(1 for r in rows if r["state"] == state) for state in (DEPLOYED, DRIFT, UNKNOWN)}
    drifted = [r["surface"] for r in rows if r["state"] == DRIFT]
    return counts, drifted
