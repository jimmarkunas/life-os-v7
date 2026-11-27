"""LIFE-779: the standing pre-approval gate for workflow changes (Jim, Nov 16 2026, 5:37 PM CT: "Approved" to "any workflow change with no new secret and no widened permissions is pre-approved; it still stops for credentials, deleting data and spend").

    python tools/workflow_gate.py [--base origin/main] [--head HEAD]

Compares every changed `.github/workflows/*.yml` between the two commits. Exit 0 and one `ok` line: nothing to ask, merge on same-head green CI with no label and no typed yes. Exit 5: at least one line `[waiting on] Jim: <the one thing>`; ask once, record one `[approvals]` line, and
re-run with LIFEOS_JIM_APPROVED set to that line (tools/preflight.sh does this for you). A change stops only for: a wider `permissions` scope, a `secrets.` reference or Bitwarden load whose NAME no job of that workflow loaded before (a name another job of the same workflow already loads is pre-approved: Jim, Nov 22 2026), a deletion of GitHub values or
production data, or paid-model spend. A new job inherits the workflow's own permissions, so only what it adds is judged.

LIFE-908: an owner approval made in ChatGPT and recorded as a CHECKED item in the claimed ticket's "Jim must do first" section is read from Jira, not asked for again. The ticket is the one named by the branch (`life/LIFE-nnn/...`); its LIVE issue is read through the existing paths
(`lifeos.jira.readiness_record`: the Jira REST settings when set, else the unmodified Atlassian MCP getJiraIssue result saved to the file named by `--jira-read FILE` or LIFEOS_JIRA_READ, at most 15 minutes old, the same handoff as `readiness issue-mcp`). The issue must be In Progress. Only a stop
reason that is ALONE a wider `permissions` block for a workflow path and job named in a checked item, whose added scopes are all `read` and all named in that item, is covered; any other reason (a write, another scope or job, a secret, a credential, a deletion, spend), an unchecked,
ambiguous or other-issue item, a stale or unreadable read, or no read at all keeps exit 5. The environment string LIFEOS_JIM_APPROVED (Jim's typed yes) still works as before and is not needed for this path."""

import difflib
import json
import os
import pathlib
import re
import subprocess
import sys
import time

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import wfsrc  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT  # where `lifeos` is imported from (tests swap ROOT for a scratch repo)
LEVEL = {"none": 0, "read": 1, "write": 2}
MCP_MAX_AGE = 15 * 60  # seconds: an older saved Jira read is not proof (the same limit as readiness issue-mcp)
BRANCH_KEY = re.compile(r"^life/(LIFE-\d+)/")
WORKFLOW_PATH = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml")
JOB_LIST = re.compile(
    r"((?:`[A-Za-z0-9_-]+`\s*(?:,|\band\b|\bor\b)?\s*)+)\s+jobs?\b"
)  # one or more `names` before the word job(s): two names are two jobs, never an approval for one
BACKTICKED = re.compile(r"`([A-Za-z0-9_-]+)`")
SCOPE_PAIR = re.compile(r"`([A-Za-z0-9_-]+):\s*(read|write|none)`")
SECRET_REF = re.compile(r"secrets\.([A-Za-z0-9_]+)")
BWS_OUTPUT = re.compile(r"steps\.bws[A-Za-z0-9_]*\.outputs\.([A-Za-z0-9_]+)")
BWS_LOAD = re.compile(r"^\s*[0-9a-fA-F-]{36}\s*>\s*([A-Za-z0-9_]+)\s*$", re.M)
DELETION = re.compile(
    r"gh\s+(?:secret|variable)\s+delete|\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b|\bTRUNCATE\s+TABLE\b|\bDELETE\s+FROM\b|(?:-X|--method)\s+DELETE\b",
    re.I,
)
SPEND = re.compile(
    r"api\.anthropic\.com|api\.openai\.com|\bclaude\s+(?:-p|--print)\b|\bcodex\s+exec\b|ANTHROPIC_API_KEY|OPENAI_API_KEY|V7_CLOUD_FIT_(?:BACKLOG_BUDGET|BACKLOG_MAX|PARALLEL|SCHEDULED)"
)


def code_lines(text):
    """The text without comment-only lines and trailing ` # comment`, so a word in a comment never counts."""
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(
            re.sub(r"\s+#\s.*$", "", line)
        )  # LIFE-1010: the spaces before the comment go too, so dropping a comment changes no line
    return "\n".join(kept)


def parse(text):
    data = yaml.safe_load(text) if text else None
    return data if isinstance(data, dict) else {}


def scopes(block):
    """permissions block -> {scope: level}; None when the block is absent. `read-all` and `write-all` count as every scope."""
    if block is None:
        return None
    if isinstance(block, str):
        level = "write" if block.startswith("write") else "read" if block.startswith("read") else "none"
        return {"*": level}
    return {str(k): str(v) for k, v in (block or {}).items()}


def effective(workflow, job):
    own = scopes(job.get("permissions")) if isinstance(job, dict) else None
    return own if own is not None else scopes(workflow.get("permissions"))


def level(granted, scope):
    if granted is None:
        return None
    return LEVEL.get(granted.get(scope, granted.get("*", "none")), 0)


def widening(base_wf, head_wf, name, head_job):
    """{scope: level} the job (head) holds beyond its base counterpart ({} when none). No block anywhere at base is the repository default: only a new `write` counts then."""
    base_job = (base_wf.get("jobs") or {}).get(name)
    base_perm = effective(base_wf, base_job) if base_job is not None else scopes(base_wf.get("permissions"))
    head_perm = effective(head_wf, head_job)
    if head_perm is None:
        return {}
    added = {}
    for scope in set(head_perm) | {"*"}:
        now, before = level(head_perm, scope), level(base_perm, scope)
        if now is None:
            continue
        if (before is None and now == 2) or (before is not None and now > before):
            added[scope] = head_perm.get(scope, head_perm.get("*", "none"))
    return added


def widened(base_wf, head_wf, name, head_job):
    """True when the job (head) holds a scope level its base counterpart did not."""
    return bool(widening(base_wf, head_wf, name, head_job))


def job_text(job):
    return json.dumps(job, sort_keys=True, default=str)


def reasons_for(path, base_text, head_text):
    """Every reason one workflow file changed in a way Jim must answer for; [] means the standing pre-approval applies."""
    return [text for text, _ in reasons_detail(path, base_text, head_text)]


def reasons_detail(path, base_text, head_text):
    """[(reason text, detail)]: detail is {"path", "job", "scopes": {scope: level}} for a wider-permissions reason and None for every other reason (LIFE-908 reads it to match a recorded approval)."""
    base_wf, head_wf = parse(base_text), parse(head_text)
    base_jobs, head_jobs = base_wf.get("jobs") or {}, head_wf.get("jobs") or {}
    # Standing approval (Jim, Nov 22 2026, 6:03 PM CT; gate change asked for explicitly at 6:14 PM CT): a secret or
    # Bitwarden name that some job of this same workflow already loads on the base may reach another job of it with no
    # stop. Only a name new to the workflow is a new credential. `secrets: inherit`, permissions, deletion and spend are unchanged.
    base_secrets = set(SECRET_REF.findall(job_text(base_jobs)))
    base_loads = set(BWS_LOAD.findall("\n".join(_block_text(j) for j in base_jobs.values()))) | set(
        BWS_OUTPUT.findall(job_text(base_jobs))
    )
    out = []
    for name, job in head_jobs.items():
        before = job_text(base_jobs.get(name)) if name in base_jobs else ""
        now = job_text(job)
        added = widening(base_wf, head_wf, name, job)
        if added:
            out.append(("wider permissions (%s: %s)" % (path, name), {"path": path, "job": name, "scopes": added}))
        new_secrets = sorted(set(SECRET_REF.findall(now)) - set(SECRET_REF.findall(before)) - base_secrets)
        inherits = job.get("secrets") == "inherit" and (base_jobs.get(name) or {}).get("secrets") != "inherit"
        if new_secrets or inherits:
            out.append(("new secret (%s: %s: %s)" % (path, name, ", ".join(new_secrets) or "secrets: inherit"), None))
        loads = sorted(
            set(BWS_LOAD.findall(_block_text(job)))
            - set(BWS_LOAD.findall(_block_text(base_jobs.get(name))))
            - base_loads
        )
        reach = sorted(set(BWS_OUTPUT.findall(now)) - set(BWS_OUTPUT.findall(before)) - base_loads)
        if loads or reach:
            out.append(("new credential (%s: %s: %s)" % (path, name, ", ".join(sorted(set(loads + reach)))), None))
    added = [
        line
        for line in difflib.unified_diff(
            code_lines(base_text).splitlines(), code_lines(head_text).splitlines(), lineterm="", n=0
        )
        if line.startswith("+") and not line.startswith("+++")
    ]
    added_text = "\n".join(added)
    if DELETION.search(added_text):
        out.append(("deleting GitHub values or production data (%s)" % path, None))
    if SPEND.search(added_text):
        out.append(("paid-model spend (%s)" % path, None))
    return out


def _block_text(job):
    """The load lists inside a job's steps, as plain text for the id > NAME lines."""
    if not isinstance(job, dict):
        return ""
    parts = []
    for step in job.get("steps") or []:
        with_ = step.get("with") if isinstance(step, dict) else None
        if isinstance(with_, dict) and isinstance(with_.get("secrets"), str):
            parts.append(with_["secrets"])
    return "\n".join(parts)


def git(*args, root=ROOT):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


SETS_PATH = ".github/actions/bws-load/sets.yml"
SET_REF = re.compile(r"(?m)^( +)set: ([A-Za-z0-9_-]+)\s*$")


def expand_sets(text, sets_text):
    """LIFE-1013: a `bws-load` step names its credential list with `set: <name>` (the lists live in sets.yml); the gate judges the inline `secrets:` block that stands for it, so adding a name to sets.yml is a new credential like adding it inline."""
    sets = yaml.safe_load(sets_text) if sets_text else None
    sets = sets if isinstance(sets, dict) else {}
    text = wfsrc.expand_aliases(
        text
    )  # LIFE-1013: the generated workflows write repeats as YAML aliases; the gate judges the plain text
    return SET_REF.sub(
        lambda m: (
            f"{m.group(1)}secrets: |\n"
            + "".join(f"{m.group(1)}  {line}\n" for line in str(sets.get(m.group(2), "")).splitlines())
        ),
        text,
    )


def changed_workflows(base, head, root=ROOT):
    out = git(
        "diff", "--name-only", "--diff-filter=ACMRD", base, head, "--", ".github/workflows", SETS_PATH, root=root
    ).stdout
    names = [line for line in out.splitlines() if line.endswith((".yml", ".yaml"))]
    if SETS_PATH in names:  # a changed list can reach every workflow that names it
        listed = (
            git("ls-tree", "--name-only", head, ".github/workflows/", root=root).stdout
            + git("ls-tree", "--name-only", base, ".github/workflows/", root=root).stdout
        )
        names = sorted(
            {n for n in names if n != SETS_PATH} | {n for n in listed.splitlines() if n.endswith((".yml", ".yaml"))}
        )
    return names


def show(ref, path, root=ROOT):
    done = git("show", "%s:%s" % (ref, path), root=root)
    return done.stdout if done.returncode == 0 else ""


def verdict(base, head, root=ROOT):
    return [text for text, _ in verdict_detail(base, head, root)]


def verdict_detail(base, head, root=ROOT):
    found = []
    base_sets, head_sets = show(base, SETS_PATH, root), show(head, SETS_PATH, root)
    for path in changed_workflows(base, head, root):
        found += reasons_detail(
            path, expand_sets(show(base, path, root), base_sets), expand_sets(show(head, path, root), head_sets)
        )
    return found


def approved_items(description):
    """LIFE-908: the owner-approved permission items of a ticket's "Jim must do first" section -> [{"path", "job", "scopes": {scope: "read"}}].
    Only a CHECKED item that names Jim and ChatGPT, exactly one workflow path, exactly one `job`, and at least one `scope: read` counts; an item that names a write or none level, a second path or job, or no job is not an approval for anything."""
    sys.path.insert(0, str(REPO))
    from lifeos.jira.release import (
        BULLET,
        DONE_ITEM,
        JIM_FIRST,
        NONE_ITEM,
        OPEN_ITEM,
        checkbox_lines,
    )  # lazy: the gate itself imports nothing of lifeos

    rows, found = checkbox_lines(description), []
    for index, line in enumerate(rows):
        if not JIM_FIRST.match(line):
            continue
        for item in rows[index + 1 :]:
            item = item.replace("\\", "").strip()
            bare = item.strip("*").strip()
            if not bare or NONE_ITEM.match(bare):
                continue
            if not (DONE_ITEM.match(bare) or OPEN_ITEM.match(bare) or BULLET.match(item)):
                break
            if not DONE_ITEM.match(bare):
                continue
            paths, pairs = set(WORKFLOW_PATH.findall(bare)), SCOPE_PAIR.findall(bare)
            jobs = {name for group in JOB_LIST.findall(bare) for name in BACKTICKED.findall(group)}
            if (
                "Jim" in bare
                and "ChatGPT" in bare
                and len(paths) == 1
                and len(jobs) == 1
                and pairs
                and all(lvl == "read" for _, lvl in pairs)
            ):
                found.append({"path": paths.pop(), "job": jobs.pop(), "scopes": {scope: lvl for scope, lvl in pairs}})
        break
    return found


def covered(detail, items):
    """True when an approved item names this exact path and job and every scope the change adds, each added at `read`."""
    if not detail:
        return False
    return any(
        i["path"] == detail["path"]
        and i["job"] == detail["job"]
        and all(lvl == "read" and i["scopes"].get(scope) == "read" for scope, lvl in detail["scopes"].items())
        for i in items
    )


def jira_issue(key, environ, read_file, now=time.time):
    """-> (raw issue {key, fields}, source label, why not). Reads the LIVE issue by the existing paths; every failure is a fixed code, never a body or a value."""
    sys.path.insert(0, str(REPO))
    from lifeos.jira import readiness_record as rr

    try:
        if read_file:
            path = pathlib.Path(read_file)
            if now() - path.stat().st_mtime > MCP_MAX_AGE:
                return None, "", "JIRA_READ_STALE (the saved Jira read is older than 15 minutes)"
            return (
                rr.from_mcp(json.loads(path.read_text()), key),
                "mcp-read file (session-saved, cannot be proven unmodified)",
                "",
            )
        if all((environ.get(name) or "").strip() for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")):
            return rr.fetch(key, rr.Jira.from_env(environ)), "live Jira read", ""
        return None, "", "JIRA_UNREADABLE (no Jira settings here and no --jira-read FILE)"
    except Exception as exc:  # noqa: BLE001  a fixed code only
        code = str(exc)
        return (
            None,
            "",
            code
            if code.startswith(("INCOMPLETE:", "KEY_MISMATCH", "JIRA_"))
            else "JIRA_UNREADABLE (%s)" % type(exc).__name__,
        )


def jira_verdict(found, environ, branch, read_file, now=time.time):
    """-> (proof line or "", note). The proof line only when EVERY stop reason is a wider-permissions reason covered by a checked item of the branch's own In Progress ticket."""
    key = (BRANCH_KEY.match(branch or "") or [None, ""])[1]
    if not key:
        return "", "no Jira approval read: the branch is not life/LIFE-nnn/..., so no ticket is named"
    if any(detail is None for _, detail in found):
        return "", ""  # another gate reason is in the diff: a recorded permission approval never covers it
    raw, source, why = jira_issue(key, environ, read_file, now)
    if not raw:
        return (
            "",
            "BLOCKED: %s read %s: %s. Read it with the Atlassian MCP, save the unmodified result to a file and rerun with LIFEOS_JIRA_READ=<file> sh tools/preflight.sh"
            % (key, "failed", why),
        )
    status = (raw["fields"].get("status") or {}).get("name") or ""
    if status != "In Progress":
        return "", "%s is %r, not In Progress: no approval is read from a ticket that is not actively claimed" % (
            key,
            status,
        )
    items = approved_items(raw["fields"].get("description"))
    unmatched = [text for text, detail in found if not covered(detail, items)]
    if unmatched:
        return "", "%s's checked Jim must do first items (%d) do not cover: %s" % (
            key,
            len(items),
            "; ".join(unmatched),
        )
    return (
        "Jira-approved (%s, %s, updated %s): %s"
        % (
            key,
            source,
            raw["fields"].get("updated") or "unknown",
            "; ".join(
                "%s: %s: %s" % (d["path"], d["job"], " ".join("%s:%s" % kv for kv in sorted(d["scopes"].items())))
                for _, d in found
            ),
        )
    ), ""


def main(argv=(), stdout=sys.stdout, environ=None, branch=None):
    environ = os.environ if environ is None else environ
    arg = lambda name, default: (
        argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default
    )
    found = verdict_detail(arg("--base", "origin/main"), arg("--head", "HEAD"), ROOT)
    reasons = [text for text, _ in found]
    if not reasons:
        stdout.write(
            "workflow gate: ok (no secret added, no permission widened; pre-approved, no label, no typed yes)\n"
        )
        return 0
    if branch is None:
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    proof, note = jira_verdict(found, environ, branch, arg("--jira-read", environ.get("LIFEOS_JIRA_READ", "")))
    if proof:
        stdout.write("workflow gate: %s\n" % proof)
        return 0
    approved = environ.get("LIFEOS_JIM_APPROVED", "").strip()
    if approved:
        stdout.write("workflow gate: Jim approved (%s); reasons: %s\n" % (approved, "; ".join(reasons)))
        return 0
    for reason in reasons:
        stdout.write("[waiting on] Jim: %s\n" % reason)
    if note:
        stdout.write("workflow gate: %s\n" % note)
    stdout.write(
        "workflow gate: ask Jim once, record one [approvals] line, then LIFEOS_JIM_APPROVED='<that line>' sh tools/preflight.sh\n"
    )
    return 5


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
