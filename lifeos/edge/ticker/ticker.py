"""LIFE-330: the Oracle ticker. Two systemd timers on lifeos-edge-01 replace cron-job.org (D31) and add a watchdog that does not depend on GitHub's own schedule.

  tick   start the hourly pipeline: POST workflow_dispatch for hourly.yml with tick=true (exactly what cron-job.org sent). LIFE-568: when another tick started
         within the gap (the workflow gate's own rule) it dispatches nothing (TICK_SKIPPED), so no duplicate run is created or cancelled. The gate in the workflow
         remains the backstop for a second timer during the hand-over.
  watch  run the watchdog's incident checks (lifeos.platform.gate.watch) here, incidents only: the daily check-in stays with the GitHub watchdog so it is
         never sent twice. Jim's alert switches (ALERTS_ENABLED, ALERTS_MUTED_UNTIL, ALERT_SETTINGS) are read from the repository variables on every run,
         so the Alert settings page stays the one place they are set; when they cannot be read the run stops (it never guesses "on" or "off").
         LIFE-301: it also raises one quiet incident (phone only) when the Hermes disk image is 80% full or more, and closes it when it drops below.
  dry    check that the credentials are present and the variables readable; dispatches nothing and pushes nothing.

Credentials come only from systemd (LoadCredentialEncrypted): `github` (fine-grained token for this repository: Actions read/write, Issues read/write,
Variables read) and `ntfy` (the phone topic). Output is one JSON line: the command, a fixed code and an HTTP status. No token, topic or job data is printed.
Stdlib only."""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE if (HERE / "lifeos").is_dir() else HERE.parents[2]))        # installed: /opt/lifeos-ticker/lifeos; in the repo: the checkout root

REPO = "jimmarkunas/life-os-v7"
API = "https://api.github.com"
VARIABLES = ("ALERTS_ENABLED", "ALERTS_MUTED_UNTIL", "ALERT_SETTINGS")
OK = {"TICK_DISPATCHED", "TICK_SKIPPED", "WATCH_DONE", "TICKER_DRY_OK"}
DISK_PATH, DISK_LIMIT = "/var/lib/hermeseval", 80        # LIFE-301: the Hermes loop-mounted image (10 GB, 34% used on Nov 7 2026)
DISK_LABEL, DISK_TITLE = "hermes-disk-full", "Hermes disk is 80% full or more"
STALL_FILE = "/etc/lifeos-close/auto.stalled"            # LIFE-562: written by the Close release timer after 2 hours without a green main head
STALL_LABEL, STALL_TITLE = "deploy-stalled", "Close service release has not deployed for over 2 hours"
DRIFT_LABEL, DRIFT_TITLE = "deploy-drift", "Installed files on the Oracle VM differ from main"
REFUSED = (401, 403, 404, 422)          # the token, its permissions, the workflow name or the inputs are wrong: a person must act


def credential(name, environ=None):
    """The systemd credential `name`, stripped; None when it is missing or empty."""
    folder = (environ if environ is not None else os.environ).get("CREDENTIALS_DIRECTORY")
    try:
        value = Path(folder, name).read_text().strip() if folder else ""
    except OSError:
        value = ""
    return value or None


def _request(path, token, method="GET", body=None):
    return urllib.request.Request(f"{API}/{path}", method=method, data=json.dumps(body).encode() if body is not None else None, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lifeos-ticker"})


def _status(request, opener):
    """-> (HTTP status, parsed body or None); (None, None) when the network failed."""
    try:
        with opener(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        return error.code, None
    except Exception:                                               # noqa: BLE001 - a fixed code, never the exception text
        return None, None


def too_soon(token, opener=urllib.request.urlopen, now=None):
    """LIFE-568: True when the workflow's own gate would cancel this tick (another tick started within the gap), so the run is never created. Same rule, same module
    (lifeos.platform.gate.decide, gap from the GATE_MIN_GAP_MINUTES repository variable). Any doubt (a read failed, no list) is False: the dispatch goes ahead and the
    workflow's gate stays the backstop."""
    import datetime as dt                                          # noqa: PLC0415
    from lifeos.platform import gate                               # noqa: PLC0415
    status, body = _status(_request(f"repos/{REPO}/actions/workflows/hourly.yml/runs?per_page=15", token), opener)
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("workflow_runs"), list):
        return False
    status, variable = _status(_request(f"repos/{REPO}/actions/variables/GATE_MIN_GAP_MINUTES", token), opener)
    raw = str(variable.get("value") or "") if status == 200 and isinstance(variable, dict) else ""            # 404 = unset = the default gap
    if status not in (200, 404):
        return False
    try:
        return gate.decide(body["workflow_runs"], now or dt.datetime.now(dt.timezone.utc), None, gate.min_gap({"GATE_MIN_GAP_MINUTES": raw})) == "skip"
    except Exception:                                              # noqa: BLE001 - never block a tick on a parsing doubt
        return False


def tick(token, opener=urllib.request.urlopen):
    """-> (code, status)."""
    if too_soon(token, opener):
        return "TICK_SKIPPED", 200
    status, _ = _status(_request(f"repos/{REPO}/actions/workflows/hourly.yml/dispatches", token, "POST", {"ref": "main", "inputs": {"tick": "true"}}), opener)
    if status is None:
        return "TICK_NETWORK", None
    if status == 204:
        return "TICK_DISPATCHED", status
    return ("TICK_REFUSED" if status in REFUSED else "TICK_GITHUB_ERROR"), status


def variables(token, opener=urllib.request.urlopen):
    """-> ({name: value}, None) or (None, status). An unset variable (404) reads as "" exactly as it does inside Actions; anything else unreadable stops."""
    found = {}
    for name in VARIABLES:
        status, body = _status(_request(f"repos/{REPO}/actions/variables/{name}", token), opener)
        if status == 404:
            found[name] = ""
        elif status == 200 and isinstance(body, dict):
            found[name] = str(body.get("value") or "")
        else:
            return None, status
    return found, 200


def disk_percent(path=DISK_PATH, usage=shutil.disk_usage):
    """-> whole percent used (like df: used / (used + available)), or None when the mount cannot be read."""
    try:
        total = usage(path)
        return int(100 * total.used / (total.used + total.free))
    except (OSError, ZeroDivisionError):
        return None


def disk_incident(path=DISK_PATH, limit=DISK_LIMIT, usage=shutil.disk_usage, sync=None):
    """LIFE-301: one GitHub issue + one quiet push while the Hermes disk is at or over `limit` percent; closes when it is back under. An unreadable mount leaves it unchanged."""
    percent = disk_percent(path, usage)
    if percent is None:
        return None
    if sync is None:
        from lifeos.platform import gate                           # noqa: PLC0415
        sync = gate.sync_incident
    return sync(DISK_LABEL, DISK_TITLE, f"The Hermes disk image on lifeos-edge-01 is {percent}% full (alarm at {limit}%). Free space or grow the image before it fills; "
                "LIFE-301 has the plan. This issue closes itself when usage drops below the limit.", percent >= limit,
                ("Hermes disk 80% full", f"The Hermes disk image is {percent}% full.", "info", "hermes_disk_full"),
                ("Hermes disk back under 80%", "The Hermes disk image is below the limit again.", "info", "hermes_disk_full_recovered"))


def stall_incident(path=STALL_FILE, exists=os.path.exists, sync=None):
    """LIFE-562: one quiet issue + one phone push while the Close release timer has found no green main head for over 2 hours (it writes `path`); closes when the marker is gone."""
    if sync is None:
        from lifeos.platform import gate                           # noqa: PLC0415
        sync = gate.sync_incident
    stalled = bool(exists(path))
    sync(STALL_LABEL, STALL_TITLE, "The Close service release timer on lifeos-edge-01 has found no green main head for over 2 hours, so no screen change is being deployed. Check the pr-tests run "
         "on main's head commit (red or missing). Nothing is broken on the VM; the live tree stays as it is. This issue closes itself when a green head deploys.", stalled,
         ("Close release stalled", "No green main head for over 2 hours; screen changes are not deploying. Open the deploy-stalled issue.", "info", "deploy_stalled"),
         ("Close release moving again", "A green main head deployed.", "info", "deploy_stalled_recovered"))
    return stalled


def drift_incident(token, opener=urllib.request.urlopen, sync=None, read=None, unit_state=None, rows=None):
    """LIFE-528: one GitHub issue + one quiet push while any repo-owned file installed on this VM differs from `main` (or its unit is not running); closes when every surface is
    DEPLOYED. A surface that cannot be read is UNKNOWN and changes nothing. Returns the check rows (counts and surface names only)."""
    from lifeos.platform import drift                              # noqa: PLC0415 - only the watch command needs it

    def expected_for(path):
        status, body = _status(_request(f"repos/{REPO}/contents/{path}?ref=main", token), opener)
        return body.get("sha") if status == 200 and isinstance(body, dict) else None

    def read_file(path):
        try:
            return Path(path).read_bytes()
        except FileNotFoundError:
            return drift.MISSING
        except OSError:
            return None

    def active(unit):
        done = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=15)
        return done.stdout.strip() or None

    rows = rows if rows is not None else drift.check(expected_for, read or read_file, unit_state or active)
    signal = drift.incident_signal(rows)
    if signal is not None:
        counts, drifted = drift.summary(rows)
        if sync is None:
            from lifeos.platform import gate                       # noqa: PLC0415
            sync = gate.sync_incident
        sync(DRIFT_LABEL, DRIFT_TITLE, "A file installed on lifeos-edge-01 no longer matches main, or its unit is not running. Differing: " + ", ".join(drifted or ["none"]) +
             ". A merge does not update the VM; show Jim the files and the install command, and install only with his yes. This issue closes itself when every surface matches.", signal,
             ("Oracle files differ from main", f"{len(drifted)} installed surface(s) differ from main. Open the deploy-drift issue.", "info", "deploy_drift"),
             ("Oracle files match main", "Every checked surface matches main again.", "info", "deploy_drift_recovered"))
    return rows


def watch(token, topic, opener=urllib.request.urlopen, run=None, environ=None, disk=None):
    """-> (code, status). Sets the switches, the token and the topic for this process only, then runs the watchdog's incident checks."""
    found, status = variables(token, opener)
    if found is None:
        return "TICKER_VARIABLES_UNREADABLE", status
    environ = environ if environ is not None else os.environ
    environ.update(found)
    environ.update({"GITHUB_TOKEN": token, "GITHUB_REPOSITORY": REPO, "NTFY_TOPIC": topic})
    if run is None:
        from lifeos.platform import gate                           # noqa: PLC0415 - only the watch command needs it
        run = gate.watch
        disk = disk or disk_incident
    try:
        run(checkin=False)
    except Exception:                                               # noqa: BLE001 - GitHub unreadable for the run list: a fixed code, the next run tries again
        return "TICKER_WATCH_FAILED", None
    if disk:
        try:
            disk()
        except Exception:                                           # noqa: BLE001 - the disk check never fails the watchdog; the next run tries again
            pass
        try:
            drift_incident(token, opener)                           # LIFE-528: inside the existing watchdog, no new scheduler
        except Exception:                                           # noqa: BLE001 - the drift check never fails the watchdog either
            pass
        try:
            stall_incident()                                        # LIFE-562: same watchdog; the marker is world-readable under /etc/lifeos-close
        except Exception:                                           # noqa: BLE001 - never fails the watchdog
            pass
    return "WATCH_DONE", None


def main(argv=None, opener=urllib.request.urlopen, run=None, environ=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    if command not in ("tick", "watch", "dry"):
        print("usage: ticker.py tick | watch | dry")
        return 2
    environ = environ if environ is not None else os.environ
    token, topic = credential("github", environ), credential("ntfy", environ)
    if not token or (command != "tick" and not topic):
        code, status = "TICKER_NO_CREDENTIAL", None
    elif command == "tick":
        code, status = tick(token, opener)
    elif command == "watch":
        code, status = watch(token, topic, opener, run, environ)
    else:
        found, status = variables(token, opener)
        code = "TICKER_DRY_OK" if found is not None else "TICKER_VARIABLES_UNREADABLE"
    print(json.dumps({"ticker": command, "code": code, "status": status}))
    return 0 if code in OK else 1


if __name__ == "__main__":
    sys.exit(main())
