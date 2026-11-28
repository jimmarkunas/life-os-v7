"""The Jobs, career and full-platform clocks of one tick, printed as one `chain:` line.

    python3 -m lifeos.platform.chain_clocks     run by the `chain` job of domains.yml; env CHAIN_TICK_ID, CHAIN_ROLE,
                                                GITHUB_TOKEN, GITHUB_REPOSITORY

The correlation ID is the hourly tick's run ID: the early domains run gets it as `tick_run`, the run after the tick as
`workflow_run.id`, and both gate jobs are named `gate (tick <id>)`. Clocks run from the tick's start: Jobs ends at the
last publish stage that printed ok, career at the last Hiring, Recruiters or Interview job that succeeded, full
platform at the last job of both runs. Status is OK, DEGRADED or UNVERIFIED (missing or running); a clock without a
real end shows no time, never zero. It never fails the run.
"""

import datetime as dt
import json
import os
import re
import sys

from lifeos.platform import ct_stamp, due, gate, jobnames

JOBS_STAGES = ("publish", "publish-fresh")
CAREER_JOBS = ("hiring", "hiring_outcomes", "recruiters", "interview")
BAD = ("failure", "cancelled", "timed_out", "startup_failure", "action_required")
WINDOW_BEFORE = dt.timedelta(minutes=2)
WINDOW_AFTER = dt.timedelta(minutes=15)
STAGE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z\s+stage (\S+): (\w[\w ]*?) [\d.]+s\b", re.M)
NAME = re.compile(r"gate \(tick (\d+)\)")


def _t(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def _clock(origin, end, status, why, stage=None):
    seconds = max(round((end - origin).total_seconds()), 0) if end else None
    return {
        "status": status,
        "at": ct_stamp.stamp(end) if end else None,
        "seconds": seconds,
        "minutes": round(seconds / 60, 1) if seconds is not None else None,
        "why": sorted(set(why)),
        **({"stage": stage} if stage else {}),
    }


def stage_ends(log_text):
    found = {}
    for stamp, name, outcome in STAGE.findall(log_text or ""):
        found[name] = (outcome, _t(stamp + "Z"))
    return found


def jobs_clock(origin, stages):
    """Jobs completion: the last publish stage that printed ok; publish-fresh not ok while publish was: DEGRADED; none
    ok: UNVERIFIED.
    """
    ok = [
        (end, name) for name, (outcome, end) in ((n, stages[n]) for n in JOBS_STAGES if n in stages) if outcome == "ok"
    ]
    why = [
        f"STAGE_{name.upper().replace('-', '_')}_{outcome.upper().replace(' ', '_')}"
        for name, (outcome, _) in ((n, stages[n]) for n in JOBS_STAGES if n in stages)
        if outcome != "ok"
    ]
    if not stages:
        return _clock(origin, None, "UNVERIFIED", ["TICK_STAGES_UNREADABLE"])
    if not ok:
        return _clock(origin, None, "UNVERIFIED" if not why else "DEGRADED", why or ["NO_PUBLISH_STAGE"])
    end, name = max(ok)
    return _clock(origin, end, "DEGRADED" if why else "OK", why, name)


def _jobs(runs):
    rows = []
    for run in runs:
        for job in run["jobs"]:
            name = jobnames.label(job.get("name"))
            if name == "chain" or name.startswith(
                "gate"
            ):  # the receipt's own job and the gates (they end at the start) are not work
                continue
            rows.append(
                (
                    run["id"],
                    name,
                    job.get("status"),
                    job.get("conclusion"),
                    job.get("started_at"),
                    job.get("completed_at"),
                )
            )
    return rows


def career_clock(origin, runs):
    """Career completion: the end of the last relevant job that succeeded; a failed job is DEGRADED, a running or missing
    one UNVERIFIED; `skipped` is neither.
    """
    mine = [r for r in _jobs(runs) if r[1] in CAREER_JOBS]
    why, ends = [], []
    for _, name, status, conclusion, _, completed in mine:
        if status != "completed":
            why.append(f"{name.upper()}_PENDING")
        elif conclusion in BAD:
            why.append(f"{name.upper()}_{conclusion.upper()}")
        elif conclusion == "success" and completed:
            ends.append((_t(completed), name))
    if not mine:
        return _clock(origin, None, "UNVERIFIED", ["CAREER_JOBS_MISSING"])
    pending = any(w.endswith("_PENDING") for w in why)
    if not ends:
        return _clock(origin, None, "UNVERIFIED" if pending or not why else "DEGRADED", why or ["CAREER_NOT_DUE"])
    end, name = max(ends)
    return _clock(origin, None if pending else end, "UNVERIFIED" if pending else "DEGRADED" if why else "OK", why, name)


def platform_clock(origin, tick, runs):
    """Full platform: the end of the last job of the early and after-tick runs, only once both have ended. UNVERIFIED
    while anything is missing or running, DEGRADED when a job failed.
    """
    why = []
    if tick.get("status") != "completed":
        why.append("TICK_RUNNING")
    kinds = {r["event"] for r in runs}
    if "workflow_run" not in kinds:
        why.append("AFTER_TICK_RUN_MISSING")
    if "workflow_dispatch" not in kinds:
        why.append(
            "EARLY_RUN_ABSENT"
        )  # informational when the after-tick run did every job (the early dispatch failed)
    pending, bad, ends = [], [], []
    for _, name, status, conclusion, _, completed in _jobs(runs):
        if status != "completed":
            pending.append(f"{name.upper()}_PENDING")
        elif conclusion in BAD:
            bad.append(f"{name.upper()}_{conclusion.upper()}")
        if status == "completed" and conclusion != "skipped" and completed:
            ends.append((_t(completed), name))
    blocking = [w for w in why if w != "EARLY_RUN_ABSENT"] + pending
    if blocking:
        return _clock(origin, None, "UNVERIFIED", blocking + bad)
    if not ends:
        return _clock(origin, None, "UNVERIFIED", ["NO_DOMAIN_JOB_ENDED"])
    end, name = max(ends)
    return _clock(origin, end, "DEGRADED" if bad else "OK", bad + [w for w in why if w == "EARLY_RUN_ABSENT"], name)


def receipt(tick_id, role, tick, tick_log, runs, run_id=None):
    origin = _t(tick.get("run_started_at") or tick["created_at"])
    return {
        "tick": str(tick_id),
        "role": role,
        "run": str(run_id or ""),
        "origin": ct_stamp.stamp(origin),
        "clocks": {
            "jobs": jobs_clock(origin, stage_ends(tick_log)),
            "career": career_clock(origin, runs),
            "platform": platform_clock(origin, tick, runs),
        },
        "runs": [{"id": str(r["id"]), "event": r["event"], "conclusion": r.get("conclusion")} for r in runs],
    }


def correlated_runs(tick_id, tick, listing, jobs_of):
    start = _t(tick.get("run_started_at") or tick["created_at"]) - WINDOW_BEFORE
    stop = _t(tick["updated_at"]) + WINDOW_AFTER if tick.get("status") == "completed" else None
    found = []
    for run in listing:
        if (
            run.get("display_title") != gate.DOMAINS_TITLE
            or _t(run["created_at"]) < start
            or (stop and _t(run["created_at"]) > stop)
        ):
            continue
        jobs = jobs_of(run["id"])
        labels = [jobnames.label(j.get("name")) for j in jobs]
        if any(NAME.fullmatch(n) and NAME.fullmatch(n).group(1) == str(tick_id) for n in labels):
            found.append({"id": run["id"], "event": run["event"], "conclusion": run.get("conclusion"), "jobs": jobs})
    return found


def line(result):
    return "chain: " + json.dumps(result, sort_keys=True)


def table(result):
    rows = [
        "### Chain clocks for tick " + result["tick"],
        "",
        "| Clock | Status | Ended | Seconds | Minutes |",
        "|---|---|---|---:|---:|",
    ]
    for name, c in result["clocks"].items():
        rows.append(
            f"| {name} | {c['status']} | {c['at'] or '-'} | {c['seconds'] if c['seconds'] is not None else '-'} | {c['minutes'] if c['minutes'] is not None else '-'} |"
        )
    return "\n".join(rows) + "\n"


def main(environ=os.environ, api=gate._api, log_text=due._log_text):
    tick_id = (environ.get("CHAIN_TICK_ID") or "").strip()
    role = environ.get("CHAIN_ROLE") or "early"
    if not tick_id.isdigit():
        print(
            line(
                {
                    "tick": "",
                    "role": role,
                    "clocks": {
                        n: {"status": "UNVERIFIED", "why": ["NO_TICK_ID"]} for n in ("jobs", "career", "platform")
                    },
                }
            )
        )
        return 0
    try:
        tick = api(f"actions/runs/{tick_id}")
        listing = api("actions/workflows/domains.yml/runs?per_page=30").get("workflow_runs", [])
        runs = correlated_runs(
            tick_id, tick, listing, lambda rid: api(f"actions/runs/{rid}/jobs?per_page=100").get("jobs", [])
        )
        try:
            text = log_text(tick_id, environ)
        except Exception as error:  # noqa: BLE001 - the Jobs clock is UNVERIFIED, the others still print
            print(f"chain: tick log unreadable ({type(error).__name__})")
            text = ""
        result = receipt(tick_id, role, tick, text, runs, environ.get("GITHUB_RUN_ID"))
    except Exception as error:  # noqa: BLE001 - a receipt never fails the run: every clock is UNVERIFIED with a fixed code
        print(
            line(
                {
                    "tick": tick_id,
                    "role": role,
                    "clocks": {
                        n: {"status": "UNVERIFIED", "why": ["CHAIN_UNREADABLE_" + type(error).__name__.upper()]}
                        for n in ("jobs", "career", "platform")
                    },
                }
            )
        )
        return 0
    print(line(result), flush=True)
    path = environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as handle:
                handle.write(table(result))
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
