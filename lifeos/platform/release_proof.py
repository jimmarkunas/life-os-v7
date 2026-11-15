"""LIFE-693: a proof run after every deploy.

See docs/platform/release-proof.md. Pure logic: the clock, the files and GitHub are passed in; stdlib only (the Oracle ticker runs its own copy).

A deploy is a new commit in the Close release tree's MANIFEST on lifeos-edge-01 (LIFE-528). The ticker's watch dispatches ONE dry quick proof run of hourly.yml (`PROOF_INPUTS`) and keeps the result in one
GitHub issue (`RECORD_LABEL`). A pass is recorded and nothing else happens. A failure raises ONE PAGE alert (deployed commit, previous good commit, the failing stage's fixed code, the one-line revert) and opens
the pause flag: an open issue labelled `FLAG_LABEL`, which the release timer reads (a protected edit). While it is open a new proof run starts at most every `REPROOF_MINUTES` on main's CURRENT head
(ref main: what the timer would deploy next), so a fix merged to main passes, lifts the pause and gets deployed with nobody deploying by hand. The first pass closes the flag with one recovered push.
No automatic rollback: the alert is the action."""
import dataclasses
import datetime as dt
import io
import re
import zipfile

PROOF_WORKFLOW = "hourly.yml"
PROOF_INPUTS = {"quick": "true", "live": "false"}         # the dispatch inputs of a dry quick proof run (LIFE-76, D208); both already exist, so no workflow file changes
PROOF_TITLE = "manual"                                    # hourly.yml's run-name for every dispatch that is not a tick
RECORD_LABEL, RECORD_TITLE = "release-proof", "Release proof record: each deployed commit and its proof run"
FLAG_LABEL = "release-paused"                             # an open issue with this label pauses the release timer; closing it (or a passing proof run) lifts the pause
FAILED_KEY, RECOVERED_KEY = "release_proof_failed", "release_proof_recovered"      # registry keys (PAGE and INFO)
PASS, FAIL, PENDING, SUPERSEDED = "pass", "fail", "pending", "superseded"
WAIT_MINUTES = 90                                         # a proof run unfinished this long after its dispatch has failed its proof
MATCH_SLACK_SECONDS = 120                                 # a run may be created a little before the dispatch time we noted
REPROOF_MINUTES = 60                                      # while paused: a new proof run at most this often
KEEP = 20                                                 # entries kept in the record
CODE_FAILED, CODE_TIMED_OUT, CODE_NOT_FOUND = "RELEASE_PROOF_FAILED", "RELEASE_PROOF_TIMED_OUT", "RELEASE_PROOF_RUN_NOT_FOUND"
UNREADABLE = "RELEASE_PROOF_UNREADABLE"                   # the record, the runs or the manifest could not be read: nothing changes and nothing pages
SHA = re.compile(r"[0-9a-f]{40}")
STAMP = "%Y-%m-%dT%H:%M:%SZ"
STAGE_FAILED = re.compile(r"\b([A-Z][A-Z0-9_-]{1,40}) FAILED: ([A-Za-z0-9_]{3,80})")      # the stages print `<STAGE> FAILED: <fixed code>` (lifeos/run.py); counts and codes only
JOB_LOG = re.compile(r"\d+_(.+)\.txt")
LINE = re.compile(r"v1 ([0-9a-f]{40}) (pass|fail|pending|superseded) (.*)")
FIELD = re.compile(r"(dispatched|finished|run|previous|code|note)=(\S+)")


@dataclasses.dataclass(frozen=True)
class Entry:
    commit: str                  # the deployed commit this proof is for (40 hex)
    status: str                  # pass | fail | pending | superseded
    dispatched: str              # when the proof run was started (UTC, STAMP)
    finished: str = ""
    run: int = 0                 # the GitHub run id, 0 until it was found
    previous: str = ""           # the previous good commit (40 hex) when one is known
    code: str = ""               # a fixed code (CODE_*, or STAGE/CODE from the run's log) for a failure
    note: str = ""               # "superseded_by_<12 hex>" for a superseded entry


@dataclasses.dataclass(frozen=True)
class Decision:
    entries: tuple               # the new record, newest first
    dispatch: Entry | None       # a proof run to start now (already in `entries` as pending)
    flag: bool | None            # True: the pause flag is (still) open, False: it is lifted, None: unchanged
    failed: Entry | None         # the proof that just failed (the one alert to raise); None when nothing new failed
    recovered: Entry | None      # the proof that just passed while paused (the one recovered push)


def _t(text):
    return dt.datetime.strptime(text, STAMP).replace(tzinfo=dt.timezone.utc) if text else None


def stamp(moment):
    return moment.astimezone(dt.timezone.utc).strftime(STAMP)


def _field(fields, key):
    return "" if fields.get(key, "-") == "-" else fields[key]


def parse_record(body):
    """-> the entries of the record issue's body, newest first. A line that is not one of ours is ignored (a person may add notes to the issue)."""
    found = []
    for line in str(body or "").splitlines():
        match = LINE.fullmatch(line.strip().strip("`"))
        if not match:
            continue
        fields = dict(FIELD.findall(match.group(3)))
        found.append(Entry(match.group(1), match.group(2), _field(fields, "dispatched"), _field(fields, "finished"), int(_field(fields, "run") or 0),
                           _field(fields, "previous"), _field(fields, "code"), _field(fields, "note")))
    return sorted(found, key=lambda e: e.dispatched, reverse=True)[:KEEP]


def render_record(entries):
    """The record issue's body: one line per proof, in the format parse_record reads, plus a plain sentence for a person."""
    lines = ["Written by the Oracle ticker's watch (LIFE-693). One line per deployed commit; the newest is first. docs/platform/release-proof.md says how to read it.", ""]
    for e in sorted(entries, key=lambda e: e.dispatched, reverse=True)[:KEEP]:
        lines.append("`v1 %s %s dispatched=%s finished=%s run=%s previous=%s code=%s note=%s`" % (
            e.commit, e.status, e.dispatched or "-", e.finished or "-", e.run or "-", e.previous or "-", e.code or "-", e.note or "-"))
    return "\n".join(lines)


def failing_code_in(archive_bytes):
    """-> `STAGE/CODE` of the first stage that printed `<STAGE> FAILED: <code>` in a failed run's job logs (a run archive), else ''. Fixed words only: no log line, name or id is kept."""
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in sorted(archive.namelist()):
            if not JOB_LOG.fullmatch(name):
                continue
            with archive.open(name) as handle:
                found = STAGE_FAILED.search(handle.read(5_000_000).decode("utf-8", "replace"))
            if found:
                return f"{found.group(1)}/{found.group(2)}"
    return ""


def _match(entry, runs, taken):
    """The proof run of one pending entry: the run it already names, else the oldest unclaimed manual run created at or after its dispatch."""
    if entry.run:
        return next((r for r in runs if r.get("id") == entry.run), None)
    cutoff = (_t(entry.dispatched) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) - dt.timedelta(seconds=MATCH_SLACK_SECONDS)
    for run in sorted(runs, key=lambda r: r.get("created_at") or ""):
        if run.get("id") not in taken and (_t(run.get("created_at")) or cutoff) >= cutoff:
            return run
    return None


def _resolve(entry, runs, taken, now, code_of):
    """-> the entry after looking at its run: still pending, pass or fail."""
    run = _match(entry, runs, taken)
    if run is not None:
        taken.add(run.get("id"))
        entry = dataclasses.replace(entry, run=int(run.get("id") or 0))
        if run.get("status") == "completed":
            finished = run.get("updated_at") or stamp(now)
            if run.get("conclusion") == "success":
                return dataclasses.replace(entry, status=PASS, finished=finished)
            return dataclasses.replace(entry, status=FAIL, finished=finished, code=code_of(entry.run) or CODE_FAILED)
    started = _t(entry.dispatched)
    if started is not None and now - started >= dt.timedelta(minutes=WAIT_MINUTES):
        return dataclasses.replace(entry, status=FAIL, finished=stamp(now), code=CODE_TIMED_OUT if run is not None else CODE_NOT_FOUND)
    return entry


def decide(now, deployed, previous, entries, runs, paused, code_of=lambda run_id: ""):
    """The whole rule, with every read passed in. `deployed`: the deployed commit (40 hex) or None; `previous`: the previous deployed tree's commit or None; `entries`: the record, any order; `runs`: the manual
    runs of hourly.yml (dicts with id, created_at, updated_at, status, conclusion); `paused`: the pause flag is open; `code_of(run_id)`: the failing STAGE/CODE of a failed run.
    Pass: recorded, no alert. Fail: one alert (only when the flag was not already open), flag open. Pass while paused: flag lifted, one recovered push. Two deploys close together: the first, if its proof had not
    finished, is recorded as superseded by the second, and each deployed commit gets its own entry. Nothing is dispatched when the deployed commit is unknown."""
    entries = sorted(entries, key=lambda e: e.dispatched, reverse=True)
    taken = {e.run for e in entries if e.run and e.status != PENDING}
    resolved, now_failed, now_passed = [], [], []
    for entry in reversed(entries):                                                   # oldest first, so the oldest pending entry claims the oldest run
        if entry.status != PENDING:
            resolved.append(entry)
            continue
        after = _resolve(entry, runs, taken, now, code_of)
        (now_failed if after.status == FAIL else now_passed if after.status == PASS else []).append(after)
        resolved.append(after)
    entries = sorted(resolved, key=lambda e: e.dispatched, reverse=True)
    if deployed and SHA.fullmatch(deployed) and entries and entries[0].commit != deployed and entries[0].status == PENDING:
        entries[0] = dataclasses.replace(entries[0], status=SUPERSEDED, finished=stamp(now), note="superseded_by_" + deployed[:12])
    dispatch = None
    if deployed and SHA.fullmatch(deployed):
        newest = entries[0] if entries else None
        known = any(e.commit == deployed and e.status in (PENDING, PASS, FAIL) for e in entries)
        reproof = bool(paused and newest is not None and newest.status == FAIL and not any(e.status == PENDING for e in entries)
                       and now - (_t(newest.dispatched) or now) >= dt.timedelta(minutes=REPROOF_MINUTES))
        if not known or reproof:
            good = next((e.commit for e in entries if e.status == PASS and e.commit != deployed), "") or (previous if previous and previous != deployed else "")
            dispatch = Entry(deployed, PENDING, stamp(now), previous=good)
            entries = [dispatch] + entries
    failed = now_failed[-1] if now_failed else None
    recovered = now_passed[-1] if (now_passed and paused and not now_failed) else None
    newest = entries[0] if entries else None
    if failed is not None or (paused and newest is not None and newest.status == FAIL):
        flag = True
    elif recovered is not None or (paused and newest is not None and newest.status == PASS):
        flag = False
    else:
        flag = None
    return Decision(tuple(entries[:KEEP]), dispatch, flag, failed if not paused else None, recovered)


def revert_line(entry):
    """The one-line revert for the failure: the commits after the previous good one, up to the deployed one (a revert PR to main). Without a known previous good commit: the deployed commit alone."""
    if entry.previous:
        return f"git revert --no-edit {entry.previous}..{entry.commit}"
    return f"git revert --no-edit {entry.commit}"


def failure_text(entry):
    """-> (title, body) of the PAGE alert and the pause flag issue. Fixed codes, commits and one revert line only."""
    code = entry.code or CODE_FAILED
    good = entry.previous[:12] if entry.previous else "none known"
    title = f"Release proof failed: deployed {entry.commit[:12]}, previous good {good}"
    body = (f"The dry proof run for deployed commit {entry.commit[:12]} failed ({code}). Previous good commit: {good}. Revert: {revert_line(entry)} (open it as a PR to main). "
            "Further Close releases wait until a proof run passes. The proof re-runs at most every hour on main's current head: if a fix is on main it passes and the pause lifts without anyone deploying; "
            "if main still fails it stays paused (no second page); or close this issue on GitHub (one click) if you judged it safe.")
    return title, body


def recovered_text(entry):
    return "Release proof passed", f"A proof run passed for deployed commit {entry.commit[:12]}; Close releases resume."
