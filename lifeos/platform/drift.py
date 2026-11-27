"""The deployed-version drift check: the watchdog compares each repo-owned file installed on the Oracle VM with `main`.

Per surface it records the blob sha on `main`, the installed file's hash and its systemd unit with state. DEPLOYED needs
all three known and agreeing (where systemd cannot be asked, an enabled unit counts and the row says
UNIT_STATE_FROM_ENABLED). An unreadable piece is UNKNOWN, never DEPLOYED; a differing hash, a missing file or a unit not
running is DRIFT. The ticker calls `incident_signal` once per run to open or close one INFO issue (`deploy_drift`).
Pure logic: every read is passed in.
"""
import hashlib
from typing import NamedTuple

DEPLOYED, DRIFT, UNKNOWN = "DEPLOYED", "DRIFT", "UNKNOWN"
ACTIVE, ENABLED = "active", "enabled"  # systemd said "active", or (in the ticker sandbox, which cannot reach systemd) the enablement link exists
UNIT_STATE_FROM_ENABLED = "UNIT_STATE_FROM_ENABLED"
MISSING = object()  # the read said "no such file"; an unreadable file is None


EXPECTED_NETWORK, INSTALLED_UNREADABLE, UNIT_UNREADABLE, EXPECTED_UNREADABLE = ("EXPECTED_NETWORK", "INSTALLED_UNREADABLE", "UNIT_UNREADABLE", "EXPECTED_UNREADABLE")


class Unreadable(Exception):
    """A reader raises this with a fixed code (EXPECTED_HTTP_404, EXPECTED_NETWORK, INSTALLED_UNREADABLE, UNIT_UNREADABLE)
    so an UNKNOWN row can say why. Never a message.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Surface(NamedTuple):
    name: str
    repo_path: str
    installed: str
    unit: str


_TICKER_MODULES = ("gate.py", "alerts.py", "alert_registry.py", "alert_settings.py", "alert_store.py", "redact.py", "drift.py", "health.py", "release_proof.py")
SURFACES = (
    Surface("ticker", "lifeos/edge/ticker/ticker.py", "/opt/lifeos-ticker/ticker.py", "lifeos-ticker-watch.timer"),
    *(Surface("ticker/" + m, "lifeos/platform/" + m, "/opt/lifeos-ticker/lifeos/platform/" + m, "lifeos-ticker-watch.timer") for m in _TICKER_MODULES),
    Surface("offsite-backup", "lifeos/edge/backup/offsite_backup.py", "/opt/lifeos-offsite/offsite_backup.py", "lifeos-offsite-backup.timer"),
    Surface("ticker/install-ticker.sh", "lifeos/edge/ticker/oracle/install-ticker.sh", "/opt/lifeos-ticker/install-ticker.sh", "lifeos-ticker-watch.timer"),
    Surface("offsite-installer", "lifeos/edge/backup/oracle/install-offsite.sh", "/opt/lifeos-offsite/install-offsite.sh", "lifeos-offsite-backup.timer"),
    Surface("close/close_release.py", "lifeos/edge/oracle/close_release.py", "/opt/lifeos-close/bin/close_release.py", "lifeos-close-release.timer"),
    Surface("close/install-close.sh", "lifeos/edge/oracle/install-close.sh", "/opt/lifeos-close/bin/install-close.sh", "lifeos-close-release.timer"),
)

# Installers that are deliberately NOT surfaces; a new install-*.sh must be a surface above or be listed here.
# reason.
UNCOVERED_INSTALLERS = {
    "lifeos/edge/runner/install-runner.sh": "The script-pack runner starts no unit, so there is no unit state to compare; its program is pinned by a hash inside its own installer and checked by that installer's verify.",
    "lifeos/edge/concord/oracle/install-gateway.sh": "LIFE-987: run once by hand with sudo (like the LIFE-977 Concord helper installer, which is not a surface either); it prints a smoke test and has its own rollback, and its files are repo files with tests.",
}


def uncovered_installers(scripts, surfaces=SURFACES, allowed=UNCOVERED_INSTALLERS):
    covered = {s.repo_path for s in surfaces} | set(allowed)
    return [path for path in scripts if path not in covered]


def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def classify(expected, installed, unit_state):
    if expected is None or installed is None:
        return UNKNOWN
    if installed is MISSING or blob_sha(installed) != expected:
        return DRIFT
    if unit_state is None:
        return UNKNOWN
    return DEPLOYED if unit_state in (ACTIVE, ENABLED) else DRIFT


def check(expected_for, read, unit_state_for, surfaces=SURFACES):
    """-> [{"surface", "state", "installed_hash", "unit", "unit_state", "why"}], one per surface; a reader that raises reads as
    unknown. `why` is "" when decided, else a fixed code naming the unreadable piece."""
    def safe(why, call, *args):
        try:
            return call(*args)
        except Unreadable as error:
            why.append(error.code)
            return None
        except Exception as error:                                             # noqa: BLE001
            why.append("READER_FAILED:" + type(error).__name__)
            return None
    rows = []
    for s in surfaces:
        why = []
        expected, installed, unit_state = safe(why, expected_for, s.repo_path), safe(why, read, s.installed), safe(why, unit_state_for, s.unit)
        state = classify(expected, installed, unit_state)
        if state == DEPLOYED and unit_state == ENABLED:
            why.append(UNIT_STATE_FROM_ENABLED)
        elif state == UNKNOWN and not why:
            why.append(EXPECTED_UNREADABLE if expected is None else INSTALLED_UNREADABLE if installed is None else UNIT_UNREADABLE)
        rows.append({"surface": s.name, "state": state, "unit": s.unit, "unit_state": unit_state or UNKNOWN, "why": why[0] if why and (state == UNKNOWN or why[0] == UNIT_STATE_FROM_ENABLED) else "",
                     "installed_hash": blob_sha(installed)[:12] if isinstance(installed, bytes) else ("missing" if installed is MISSING else UNKNOWN)})
    return rows


def incident_signal(rows):
    """True when any surface drifted (open), False when every surface is DEPLOYED (recovered), None otherwise."""
    states = {r["state"] for r in rows}
    if DRIFT in states:
        return True
    return False if states == {DEPLOYED} else None


def summary(rows):
    counts = {state: sum(1 for r in rows if r["state"] == state) for state in (DEPLOYED, DRIFT, UNKNOWN)}
    drifted = [r["surface"] for r in rows if r["state"] == DRIFT]
    return counts, drifted


def explain(rows):
    counts, drifted = summary(rows)
    groups = {}
    for r in rows:
        if r["state"] == UNKNOWN:
            groups.setdefault(r.get("why") or UNKNOWN, []).append(r["surface"])
    undecided = [f"{code}:{len(names)}({','.join(names[:3])}{',...' if len(names) > 3 else ''})" for code, names in sorted(groups.items())]
    signal = incident_signal(rows)
    enabled_only = sum(1 for r in rows if r.get("why") == UNIT_STATE_FROM_ENABLED)
    return (f"drift: deployed={counts[DEPLOYED]} drift={counts[DRIFT]} unknown={counts[UNKNOWN]} signal={'open' if signal else 'recovered' if signal is False else 'unchanged'}"
            + (" drifted=" + ",".join(drifted) if drifted else "") + (" undecided=" + ";".join(undecided) if undecided else "")
            + (f" {UNIT_STATE_FROM_ENABLED}={enabled_only} (enabled, not proof the unit is running)" if enabled_only else ""))
