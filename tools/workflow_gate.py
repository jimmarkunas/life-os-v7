"""LIFE-779: the standing pre-approval gate for workflow changes (Jim, Nov 16 2026, 5:37 PM CT: "Approved" to "any workflow change with no new secret and no widened permissions is pre-approved; it still stops for credentials, deleting data and spend").

    python tools/workflow_gate.py [--base origin/main] [--head HEAD]

Compares every changed `.github/workflows/*.yml` between the two commits. Exit 0 and one `ok` line: nothing to ask, merge on same-head green CI with no label and no typed yes. Exit 5: at least one line `[waiting on] Jim: <the one thing>`; ask once, record one `[approvals]` line, and
re-run with LIFEOS_JIM_APPROVED set to that line (tools/preflight.sh does this for you). A change stops only for: a wider `permissions` scope, a `secrets.` reference reaching a job that had none of that name, a new Bitwarden load or credential reaching a job, a deletion of GitHub values or
production data, or paid-model spend. A new job inherits the workflow's own permissions, so only what it adds is judged."""
import difflib
import json
import os
import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEVEL = {"none": 0, "read": 1, "write": 2}
SECRET_REF = re.compile(r"secrets\.([A-Za-z0-9_]+)")
BWS_OUTPUT = re.compile(r"steps\.bws[A-Za-z0-9_]*\.outputs\.([A-Za-z0-9_]+)")
BWS_LOAD = re.compile(r"^\s*[0-9a-fA-F-]{36}\s*>\s*([A-Za-z0-9_]+)\s*$", re.M)
DELETION = re.compile(r"gh\s+(?:secret|variable)\s+delete|\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b|\bTRUNCATE\s+TABLE\b|\bDELETE\s+FROM\b|(?:-X|--method)\s+DELETE\b", re.I)
SPEND = re.compile(r"api\.anthropic\.com|api\.openai\.com|\bclaude\s+(?:-p|--print)\b|\bcodex\s+exec\b|ANTHROPIC_API_KEY|OPENAI_API_KEY|V7_CLOUD_FIT_(?:BACKLOG_BUDGET|BACKLOG_MAX|PARALLEL|SCHEDULED)")


def code_lines(text):
    """The text without comment-only lines and trailing ` # comment`, so a word in a comment never counts."""
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(re.sub(r"\s#\s.*$", "", line))
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


def widened(base_wf, head_wf, name, head_job):
    """True when the job (head) holds a scope level its base counterpart did not. No block anywhere at base is the repository default: only a new `write` counts then."""
    base_job = (base_wf.get("jobs") or {}).get(name)
    base_perm = effective(base_wf, base_job) if base_job is not None else scopes(base_wf.get("permissions"))
    head_perm = effective(head_wf, head_job)
    if head_perm is None:
        return False
    for scope in set(head_perm) | {"*"}:
        now, before = level(head_perm, scope), level(base_perm, scope)
        if now is None:
            continue
        if before is None and now == 2:
            return True
        if before is not None and now > before:
            return True
    return False


def job_text(job):
    return json.dumps(job, sort_keys=True, default=str)


def reasons_for(path, base_text, head_text):
    """Every reason one workflow file changed in a way Jim must answer for; [] means the standing pre-approval applies."""
    base_wf, head_wf = parse(base_text), parse(head_text)
    base_jobs, head_jobs = base_wf.get("jobs") or {}, head_wf.get("jobs") or {}
    out = []
    for name, job in head_jobs.items():
        before = job_text(base_jobs.get(name)) if name in base_jobs else ""
        now = job_text(job)
        if widened(base_wf, head_wf, name, job):
            out.append("wider permissions (%s: %s)" % (path, name))
        new_secrets = sorted(set(SECRET_REF.findall(now)) - set(SECRET_REF.findall(before)))
        inherits = job.get("secrets") == "inherit" and (base_jobs.get(name) or {}).get("secrets") != "inherit"
        if new_secrets or inherits:
            out.append("new secret (%s: %s: %s)" % (path, name, ", ".join(new_secrets) or "secrets: inherit"))
        loads = sorted(set(BWS_LOAD.findall(_block_text(job))) - set(BWS_LOAD.findall(_block_text(base_jobs.get(name)))))
        reach = sorted(set(BWS_OUTPUT.findall(now)) - set(BWS_OUTPUT.findall(before)))
        if loads or reach:
            out.append("new credential (%s: %s: %s)" % (path, name, ", ".join(sorted(set(loads + reach)))))
    added = [line for line in difflib.unified_diff(code_lines(base_text).splitlines(), code_lines(head_text).splitlines(), lineterm="", n=0) if line.startswith("+") and not line.startswith("+++")]
    added_text = "\n".join(added)
    if DELETION.search(added_text):
        out.append("deleting GitHub values or production data (%s)" % path)
    if SPEND.search(added_text):
        out.append("paid-model spend (%s)" % path)
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


def changed_workflows(base, head, root=ROOT):
    out = git("diff", "--name-only", "--diff-filter=ACMRD", base, head, "--", ".github/workflows", root=root).stdout
    return [line for line in out.splitlines() if line.endswith((".yml", ".yaml"))]


def show(ref, path, root=ROOT):
    done = git("show", "%s:%s" % (ref, path), root=root)
    return done.stdout if done.returncode == 0 else ""


def verdict(base, head, root=ROOT):
    reasons = []
    for path in changed_workflows(base, head, root):
        reasons += reasons_for(path, show(base, path, root), show(head, path, root))
    return reasons


def main(argv=(), stdout=sys.stdout, environ=None):
    environ = os.environ if environ is None else environ
    arg = lambda name, default: argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default
    reasons = verdict(arg("--base", "origin/main"), arg("--head", "HEAD"), ROOT)
    if not reasons:
        stdout.write("workflow gate: ok (no secret added, no permission widened; pre-approved, no label, no typed yes)\n")
        return 0
    approved = environ.get("LIFEOS_JIM_APPROVED", "").strip()
    if approved:
        stdout.write("workflow gate: Jim approved (%s); reasons: %s\n" % (approved, "; ".join(reasons)))
        return 0
    for reason in reasons:
        stdout.write("[waiting on] Jim: %s\n" % reason)
    stdout.write("workflow gate: ask Jim once, record one [approvals] line, then LIFEOS_JIM_APPROVED='<that line>' sh tools/preflight.sh\n")
    return 5


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
