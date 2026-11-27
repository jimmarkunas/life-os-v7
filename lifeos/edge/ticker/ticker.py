"""The Oracle ticker: two systemd timers on lifeos-edge-01.

`tick` POSTs workflow_dispatch for hourly.yml (tick=true) and dispatches nothing (TICK_SKIPPED) when another tick
started within the workflow gate's gap. `watch` runs the watchdog's incident checks (lifeos.platform.gate.watch)
plus quiet incidents for the Hermes disk, a stalled Close release, install drift and the Close release proof; the
alert switches are read each run from Bitwarden via the `bwsalerts` credential (an unreadable store stops the run
with a fixed code). `dry` checks credentials and variables and does nothing else. Credentials come only from
systemd: `github` and `ntfy`. Output is one JSON line (command, fixed code, HTTP status); no token, topic or job
data. Stdlib only."""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# installed: /opt/lifeos-ticker/lifeos; in the repo: the checkout root
sys.path.insert(0, str(HERE if (HERE / "lifeos").is_dir() else HERE.parents[2]))

REPO = "jimmarkunas/life-os-v7"
API = "https://api.github.com"
SYSTEMD_DIR = "/etc/systemd/system"
VARIABLES = ("ALERTS_ENABLED", "ALERTS_MUTED_UNTIL", "ALERT_SETTINGS")
OK = {"TICK_DISPATCHED", "TICK_SKIPPED", "WATCH_DONE", "TICKER_DRY_OK"}
DISK_PATH, DISK_LIMIT = "/var/lib/hermeseval", 80
DISK_LABEL, DISK_TITLE = "hermes-disk-full", "Hermes disk is 80% full or more"
STALL_FILE = "/etc/lifeos-close/auto.stalled"
STALL_LABEL, STALL_TITLE = "deploy-stalled", "Close service release has not deployed for over 2 hours"
PROOF_FILE = "/etc/lifeos-close/auto.proof-unreadable"
PROOF_LABEL, PROOF_TITLE = "release-pause-unreadable", "Close release timer cannot read the release-paused flag"
DRIFT_LABEL, DRIFT_TITLE = "deploy-drift", "Installed files on the Oracle VM differ from main"
TODOIST_LABEL, TODOIST_TITLE = "todoist-token", "The Todoist token refresher needs attention"
TODOIST_OK = {"not_consented", "fresh", "refreshed"}
# world-readable; the first line is `commit <40 hex>`
RELEASE_MANIFEST, RELEASE_PREVIOUS = "/opt/lifeos-close/current/MANIFEST", "/opt/lifeos-close/previous/MANIFEST"
# token, permissions, workflow name or inputs are wrong: a person must act
REFUSED = (401, 403, 404, 422)


def credential(name, environ=None):
    folder = (environ if environ is not None else os.environ).get("CREDENTIALS_DIRECTORY")
    try:
        value = Path(folder, name).read_text().strip() if folder else ""
    except OSError:
        value = ""
    return value or None


def _request(path, token, method="GET", body=None):
    return urllib.request.Request(
        f"{API}/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lifeos-ticker",
        },
    )


def _status(request, opener):
    try:
        with opener(request, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        return error.code, None
    except Exception:  # noqa: BLE001
        return None, None


def too_soon(token, opener=urllib.request.urlopen, now=None):
    """True when the workflow gate would cancel this tick (another started within GATE_MIN_GAP_MINUTES); a failed read
    is False.
    """
    import datetime as dt  # noqa: PLC0415
    from lifeos.platform import gate  # noqa: PLC0415

    status, body = _status(_request(f"repos/{REPO}/actions/workflows/hourly.yml/runs?per_page=15", token), opener)
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("workflow_runs"), list):
        return False
    status, variable = _status(_request(f"repos/{REPO}/actions/variables/GATE_MIN_GAP_MINUTES", token), opener)
    raw = str(variable.get("value") or "") if status == 200 and isinstance(variable, dict) else ""
    if status not in (200, 404):
        return False
    try:
        return (
            gate.decide(
                body["workflow_runs"],
                now or dt.datetime.now(dt.timezone.utc),
                None,
                gate.min_gap({"GATE_MIN_GAP_MINUTES": raw}),
            )
            == "skip"
        )
    except Exception:  # noqa: BLE001
        return False


def tick(token, opener=urllib.request.urlopen):
    if too_soon(token, opener):
        return "TICK_SKIPPED", 200
    status, _ = _status(
        _request(
            f"repos/{REPO}/actions/workflows/hourly.yml/dispatches",
            token,
            "POST",
            {"ref": "main", "inputs": {"tick": "true"}},
        ),
        opener,
    )
    if status is None:
        return "TICK_NETWORK", None
    if status == 204:
        return "TICK_DISPATCHED", status
    return ("TICK_REFUSED" if status in REFUSED else "TICK_GITHUB_ERROR"), status


def variables(store):
    """-> ({name: value}, 200) or (None, None). The three alert switches come from Bitwarden through `store`; failure
    never exposes a value.
    """
    try:
        return {name: str(value) for name, value in store().items() if name in VARIABLES}, 200
    except Exception:  # noqa: BLE001
        return None, None


def disk_percent(path=DISK_PATH, usage=shutil.disk_usage):
    try:
        total = usage(path)
        return int(100 * total.used / (total.used + total.free))
    except (OSError, ZeroDivisionError):
        return None


def disk_incident(path=DISK_PATH, limit=DISK_LIMIT, usage=shutil.disk_usage, sync=None):
    percent = disk_percent(path, usage)
    if percent is None:
        return None
    if sync is None:
        from lifeos.platform import gate  # noqa: PLC0415

        sync = gate.sync_incident
    return sync(
        DISK_LABEL,
        DISK_TITLE,
        f"The Hermes disk image on lifeos-edge-01 is {percent}% full (alarm at {limit}%). Free space or grow the image before it fills; "
        "LIFE-301 has the plan. This issue closes itself when usage drops below the limit.",
        percent >= limit,
        ("Hermes disk 80% full", f"The Hermes disk image is {percent}% full.", "info", "hermes_disk_full"),
        (
            "Hermes disk back under 80%",
            "The Hermes disk image is below the limit again.",
            "info",
            "hermes_disk_full_recovered",
        ),
    )


def stall_incident(path=STALL_FILE, exists=os.path.exists, sync=None):
    """One quiet issue + push while the Close release timer reports no green main head for 2 hours; closes when `path`
    is gone.
    """
    if sync is None:
        from lifeos.platform import gate  # noqa: PLC0415

        sync = gate.sync_incident
    stalled = bool(exists(path))
    sync(
        STALL_LABEL,
        STALL_TITLE,
        "The Close service release timer on lifeos-edge-01 has found no green main head for over 2 hours, so no screen change is being deployed. Check the pr-tests run "
        "on main's head commit (red or missing). Nothing is broken on the VM; the live tree stays as it is. This issue closes itself when a green head deploys.",
        stalled,
        (
            "Close release stalled",
            "No green main head for over 2 hours; screen changes are not deploying. Open the deploy-stalled issue.",
            "info",
            "deploy_stalled",
        ),
        ("Close release moving again", "A green main head deployed.", "info", "deploy_stalled_recovered"),
    )
    return stalled


def proof_unreadable_incident(path=PROOF_FILE, exists=os.path.exists, sync=None):
    """One page, closed when the marker is gone, when the release timer wrote `path` because it could not read the
    release-paused flag.
    """
    if sync is None:
        from lifeos.platform import gate  # noqa: PLC0415

        sync = gate.sync_incident
    unreadable = bool(exists(path))
    sync(
        PROOF_LABEL,
        PROOF_TITLE,
        "The Close release timer on lifeos-edge-01 could not read the release-paused flag on GitHub (a network error, a rate limit or an answer it does not understand), so it is "
        "deploying nothing: an unknown state never deploys. Nothing is broken on the VM; the live tree stays as it is. You do nothing: this lifts by itself on the next timer run (every 15 minutes) "
        "that can read the flag, and this issue then closes itself. If it stays open for more than an hour, check that GitHub is reachable from lifeos-edge-01.",
        unreadable,
        (
            "Close releases not deploying",
            "The release timer cannot read the release-paused flag, so nothing deploys. It lifts by itself when GitHub is readable again.",
            "page",
            "release_pause_unreadable",
        ),
        (
            "Close releases can deploy again",
            "The release timer can read the release-paused flag again.",
            "info",
            "release_pause_unreadable_recovered",
        ),
        remind=False,
    )
    return unreadable


def read_unit_state(unit, run=None, listdir=None, base=SYSTEMD_DIR):
    """A unit's state for the drift check: systemd's answer, else its enablement link (proves "enabled", not
    "running").
    """
    run, listdir = run or subprocess.run, listdir or os.listdir
    answer = ""
    try:
        answer = run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        answer = ""
    if answer:
        return answer
    target = "timers.target.wants" if unit.endswith(".timer") else "multi-user.target.wants"
    try:
        return drift_module().ENABLED if unit in listdir(f"{base}/{target}") else "disabled"
    except OSError as error:
        raise drift_module().Unreadable(drift_module().UNIT_UNREADABLE) from error


def drift_module():
    from lifeos.platform import drift  # noqa: PLC0415

    return drift


def drift_incident(token, opener=urllib.request.urlopen, sync=None, read=None, unit_state=None, rows=None):
    from lifeos.platform import drift  # noqa: PLC0415

    def expected_for(path):
        status, body = _status(_request(f"repos/{REPO}/contents/{path}?ref=main", token), opener)
        if status == 200 and isinstance(body, dict) and body.get("sha"):
            return body["sha"]
        raise drift.Unreadable(drift.EXPECTED_NETWORK if status is None else f"EXPECTED_HTTP_{status}")

    def read_file(path):
        try:
            return Path(path).read_bytes()
        except FileNotFoundError:
            return drift.MISSING
        except OSError as error:
            raise drift.Unreadable(drift.INSTALLED_UNREADABLE) from error

    def active(unit):
        return read_unit_state(unit)

    rows = rows if rows is not None else drift.check(expected_for, read or read_file, unit_state or active)
    signal = drift.incident_signal(rows)
    print(drift.explain(rows))
    if signal is not None:
        counts, drifted = drift.summary(rows)
        if sync is None:
            from lifeos.platform import gate  # noqa: PLC0415

            sync = gate.sync_incident
        sync(
            DRIFT_LABEL,
            DRIFT_TITLE,
            "A file installed on lifeos-edge-01 no longer matches main, or its unit is not running. Differing: "
            + ", ".join(drifted or ["none"])
            + ". A merge does not update the VM; show Jim the files and the install command, and install only with his yes. This issue closes itself when every surface matches.",
            signal,
            (
                "Oracle files differ from main",
                f"{len(drifted)} installed surface(s) differ from main. Open the deploy-drift issue.",
                "info",
                "deploy_drift",
            ),
            ("Oracle files match main", "Every checked surface matches main again.", "info", "deploy_drift_recovered"),
        )
    return rows


def release_proof(token, opener=urllib.request.urlopen, api=None, read=None, sync=None, logs=None, now=None):
    """One dry proof run per Close release (platform/release_proof.py); pages when a proof fails. Never raises."""
    import datetime as dt  # noqa: PLC0415
    from lifeos.platform import gate, release_proof as proof  # noqa: PLC0415

    api = api or gate._api
    now = now or dt.datetime.now(dt.timezone.utc)

    def manifest(path):
        try:
            first = (Path(path).read_text() if read is None else read(path)).splitlines()[0].split()
            return first[1] if len(first) == 2 and first[0] == "commit" and proof.SHA.fullmatch(first[1]) else None
        except (OSError, IndexError):
            return None

    def failing_code(run_id):
        try:
            return proof.failing_code_in((logs or gate._logs)(run_id, 30_000_000))
        except Exception:  # noqa: BLE001
            return ""

    try:
        deployed, previous = manifest(RELEASE_MANIFEST), manifest(RELEASE_PREVIOUS)
        records = api(f"issues?state=open&labels={proof.RECORD_LABEL}&per_page=1")
        entries = proof.parse_record(records[0].get("body")) if records else []
        listed = (
            api(f"actions/workflows/{proof.PROOF_WORKFLOW}/runs?event=workflow_dispatch&per_page=30").get(
                "workflow_runs"
            )
            or []
        )
        runs = [r for r in listed if r.get("display_title") == proof.PROOF_TITLE]
        paused = bool(api(f"issues?state=open&labels={proof.FLAG_LABEL}&per_page=1"))
    except Exception:  # noqa: BLE001
        return proof.UNREADABLE
    if deployed is None:
        return proof.UNREADABLE
    decision = proof.decide(now, deployed, previous, entries, runs, paused, failing_code)
    entries = list(decision.entries)
    if decision.dispatch is not None:
        try:
            api(
                f"actions/workflows/{proof.PROOF_WORKFLOW}/dispatches",
                "POST",
                {"ref": "main", "inputs": proof.PROOF_INPUTS},
            )
        except Exception:  # noqa: BLE001
            entries = [e for e in entries if e is not decision.dispatch]
    body = proof.render_record(entries)
    try:
        if records:
            if records[0].get("body") != body:
                api(f"issues/{records[0]['number']}", "PATCH", {"body": body})
        elif entries:
            api("labels", "POST", {"name": proof.RECORD_LABEL, "color": "0e8a16"})
            api("issues", "POST", {"title": proof.RECORD_TITLE, "body": body, "labels": [proof.RECORD_LABEL]})
    except Exception:  # noqa: BLE001
        pass
    if decision.flag is not None:
        sync = sync or gate.sync_incident
        failed = decision.failed or next((e for e in entries if e.status == proof.FAIL), None)
        title, text = (
            proof.failure_text(failed)
            if failed
            else ("Release proof failed", "A proof run failed after a Close release.")
        )
        good_title, good_text = (
            proof.recovered_text(decision.recovered)
            if decision.recovered
            else ("Release proof passed", "A proof run passed; Close releases resume.")
        )
        try:
            sync(
                proof.FLAG_LABEL,
                title,
                text,
                decision.flag,
                (title, text, "page", proof.FAILED_KEY),
                (good_title, good_text, "info", proof.RECOVERED_KEY),
            )
        except Exception:  # noqa: BLE001
            return "flag_not_synced"
    return (
        "dispatched"
        if decision.dispatch is not None
        else "failed"
        if decision.failed
        else "recovered"
        if decision.recovered
        else "ok"
    )


def todoist_incident(results, sync=None):
    """One quiet issue + push while either Todoist app's refresher reports anything but ok; closes itself when both are ok.
    The body holds fixed words only (`read:reauth_required`), never a token or a value."""
    if sync is None:
        from lifeos.platform import gate  # noqa: PLC0415

        sync = gate.sync_incident
    bad = sorted(f"{app}:{code}" for app, code in results.items() if code not in TODOIST_OK)
    sync(
        TODOIST_LABEL,
        TODOIST_TITLE,
        "The Todoist token refresher on lifeos-edge-01 reports: "
        + (", ".join(bad) or "ok")
        + ". reauth_required means Todoist "
        "revoked the grant and Jim must consent again; vault_unwritable or access_unwritable means Bitwarden could not be written "
        "(a recovery file keeps the new token); scope_mismatch means the consented scope is not the app's. This issue closes "
        "itself when both apps are ok.",
        bool(bad),
        (
            "Todoist token needs attention",
            "The Todoist token refresher reports a problem. Open the todoist-token issue.",
            "info",
            "todoist_token",
        ),
        ("Todoist token ok again", "Both Todoist apps refresh normally.", "info", "todoist_token_recovered"),
    )
    return bad


def todoist_check(bws, environ=None, opener=urllib.request.urlopen, run=subprocess.run, sync=None):
    from lifeos.platform import todoist_refresh  # noqa: PLC0415

    results = todoist_refresh.refresh_all(bws, environ, opener=opener, run=run)
    todoist_incident(results, sync)
    return results


def watch(token, topic, opener=urllib.request.urlopen, run=None, environ=None, disk=None, store=None, todoist=None):
    found, status = variables(store)
    if found is None:
        return "TICKER_VARIABLES_UNREADABLE", status
    environ = environ if environ is not None else os.environ
    environ.update(found)
    environ.update({"GITHUB_TOKEN": token, "GITHUB_REPOSITORY": REPO, "NTFY_TOPIC": topic})
    if run is None:
        from lifeos.platform import gate  # noqa: PLC0415

        run = gate.watch
        disk = disk or disk_incident
    try:
        run(checkin=False)
    except Exception:  # noqa: BLE001
        return "TICKER_WATCH_FAILED", None
    if disk:
        try:
            disk()
        except Exception as error:  # noqa: BLE001
            print(f"ticker: disk check failed ({type(error).__name__})")
        try:
            drift_incident(token, opener)
        except Exception as error:  # noqa: BLE001
            print(f"ticker: drift check failed ({type(error).__name__})")
        try:
            stall_incident()
        except Exception as error:  # noqa: BLE001
            print(f"ticker: stall check failed ({type(error).__name__})")
        try:
            proof_unreadable_incident()
        except Exception as error:  # noqa: BLE001
            print(f"ticker: proof flag check failed ({type(error).__name__})")
        try:
            release_proof(token, opener)
        except Exception as error:  # noqa: BLE001
            print(f"ticker: release proof check failed ({type(error).__name__})")
        if todoist:
            try:
                results = todoist()
                print("ticker: todoist " + " ".join(f"{a}={c}" for a, c in sorted(results.items())))
            except Exception as error:  # noqa: BLE001
                print(f"ticker: todoist refresh failed ({type(error).__name__})")
    return "WATCH_DONE", None


def main(argv=None, opener=urllib.request.urlopen, run=None, environ=None):
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else ""
    if command not in ("tick", "watch", "dry"):
        print("usage: ticker.py tick | watch | dry")
        return 2
    environ = environ if environ is not None else os.environ
    token, topic = credential("github", environ), credential("ntfy", environ)
    # switches live in Bitwarden only: no credential, no watch or dry
    bws = credential("bwsalerts", environ)
    store = None
    if bws:
        from lifeos.platform import alert_store  # noqa: PLC0415

        store = lambda: alert_store.read(bws, environ=environ)  # noqa: E731
    if not token or (command != "tick" and not (topic and store)):
        code, status = "TICKER_NO_CREDENTIAL", None
    elif command == "tick":
        code, status = tick(token, opener)
    elif command == "watch":
        code, status = watch(
            token,
            topic,
            opener,
            run,
            environ,
            store=store,
            todoist=(lambda: todoist_check(bws, environ)) if bws else None,
        )
    else:
        found, status = variables(store)
        code = "TICKER_DRY_OK" if found is not None else "TICKER_VARIABLES_UNREADABLE"
    print(json.dumps({"ticker": command, "code": code, "status": status}))
    return 0 if code in OK else 1


if __name__ == "__main__":
    sys.exit(main())
