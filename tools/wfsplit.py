"""LIFE-1098 step D: the big workflows are small callers plus one reusable (`workflow_call`) workflow per job.

The parts under `.github/workflows-src/<workflow>/` still describe the pipeline as ONE logical workflow (the jobs, their `needs` and `if`, their steps), so every contract test and every reader of the pipeline sees the whole of it. This module cuts that logical text into the files GitHub runs:

  split(text, plan)   {file name: text}: the caller (`domains.yml` / `hourly.yml`: the triggers, the concurrency group, the inline jobs and one `uses:` job per cut job) and one `<prefix><job>.yml` per cut job
  flatten(files)      the inverse: the logical document again, from the generated files (the proof that each old job exists in exactly one new place, with the same runner, timeout, permissions and steps)

What moves where. A cut job keeps `runs-on`, `timeout-minutes`, `permissions`, `outputs`, `env` and `steps` in its own file; its `needs`, `if` and `concurrency` stay on the caller's job (the only places those keys are allowed on a job that calls a workflow); its `permissions` are written on both, identical, so the called job never holds more than the caller granted.
A called workflow does not see the caller's `inputs`, `needs` or secrets, so each is passed by name and only what the job reads: `inputs.x` keeps its name (declared on `workflow_call`, handed over as `${{ inputs.x || <empty value> }}`, so the dispatch inputs mean the same thing inside), `needs.<job>.outputs.<name>` and `needs.<job>.result` become the string inputs `<job>_<name>` and `<job>_result`, and exactly the secrets the job names are declared and passed (never `secrets: inherit`). The workflow-level `env` is copied into each called workflow (the caller's does not reach it); the workflow-level `concurrency` and `run-name` stay on the caller.
"""

import pathlib
import re

import yaml

import wfsrc

ROOT = pathlib.Path(__file__).resolve().parents[1]

KEY = re.compile(r"^    ([A-Za-z0-9_-]+):(.*)$")
JOB_KEY = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
INPUT = re.compile(r"\binputs\.([A-Za-z_][A-Za-z0-9_]*)")
NEED_OUT = re.compile(r"\bneeds\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_]+)")
NEED_RESULT = re.compile(r"\bneeds\.([A-Za-z0-9_-]+)\.result\b")
SECRET = re.compile(r"\bsecrets\.([A-Za-z0-9_]+)")
CALLER_KEYS = ("name", "needs", "if", "concurrency")  # stay on the caller; `permissions` is on both
EMPTY = {"boolean": "false", "string": "''"}


def cut(text):
    """-> (header text up to and with `jobs:`, [(job, [(key, chunk text)])]) from the plain logical workflow text."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if line == "jobs:")
    jobs, current = [], None
    for line in lines[start + 1 :]:
        if JOB_KEY.match(line):
            current = (JOB_KEY.match(line).group(1), [])
            jobs.append(current)
            continue
        if current is None:
            continue
        found = KEY.match(line)
        if found:
            current[1].append([found.group(1), [line]])
        elif current[1]:
            current[1][-1][1].append(line)
        else:
            current[1].append(["", [line]])
    out = []
    for name, chunks in jobs:
        while chunks and chunks[-1][1] and not chunks[-1][1][-1].strip():
            chunks[-1][1].pop()  # the blank lines between jobs belong to neither
        out.append((name, [(key, "\n".join(chunk)) for key, chunk in chunks if chunk]))
    return "\n".join(lines[: start + 1]), out


def header_env(header):
    """The workflow-level `env:` block of the header as text ('' when there is none)."""
    lines = header.split("\n")
    for i, line in enumerate(lines):
        if line == "env:":
            end = i + 1
            while end < len(lines) and (lines[end].startswith(" ") or not lines[end].strip()):
                end += 1
            return "\n".join(lines[i:end]).rstrip()
    return ""


def dispatch_inputs(header):
    doc = yaml.safe_load(header + "\n  _: {}")  # `jobs:` needs a body to parse
    return (((doc.get("on") or doc.get(True) or {}).get("workflow_dispatch") or {}).get("inputs")) or {}


def body_of(chunks):
    return "\n".join(chunk for key, chunk in chunks if key not in CALLER_KEYS and key != "permissions")


def rewrite_needs(chunk):
    """-> (text with `needs.x.outputs.y` / `needs.x.result` as inputs, {input name: expression it carries})"""
    passed = {}

    def out(m):
        name = f"{m.group(1).replace('-', '_')}_{m.group(2)}"
        passed[name] = f"needs.{m.group(1)}.outputs.{m.group(2)}"
        return f"inputs.{name}"

    def result(m):
        name = f"{m.group(1).replace('-', '_')}_result"
        passed[name] = f"needs.{m.group(1)}.result"
        return f"inputs.{name}"

    return NEED_RESULT.sub(result, NEED_OUT.sub(out, chunk)), passed


def indent(text, by):
    return "\n".join((" " * by + line) if line.strip() else line for line in text.split("\n"))


def called_file(job, chunks, header, declared, prefix):
    kept = [(k, c) for k, c in chunks if k not in CALLER_KEYS]
    body, passed = rewrite_needs("\n".join(c for _, c in kept))
    used = sorted(set(INPUT.findall(body)) - set(passed))
    secrets = sorted(set(SECRET.findall(body)))
    doc = yaml.safe_load("jobs:\n  " + job + ":\n" + body)["jobs"][job]
    lines = ["name: " + prefix + job.replace("_", "-"), "on:", "  workflow_call:"]
    inputs = []
    for name in used:
        if name not in declared:
            continue
        kind = "boolean" if declared[name].get("type") == "boolean" else "string"
        default = declared[name].get("default", False if kind == "boolean" else "")
        default = str(default).lower() if kind == "boolean" else '"' + str(default) + '"'
        inputs += [f"      {name}:", f"        type: {kind}", f"        default: {default}"]
    for name in sorted(passed):
        inputs += [f"      {name}:", "        type: string", '        default: ""']
    if inputs:
        lines += ["    inputs:"] + inputs
    if secrets:
        lines += ["    secrets:"] + [x for s in secrets for x in (f"      {s}:", "        required: false")]
    outputs = sorted((doc.get("outputs") or {}))
    if outputs:
        lines += ["    outputs:"] + [
            x for o in outputs for x in (f"      {o}:", "        value: ${{ jobs." + job + ".outputs." + o + " }}")
        ]
    lines += ["permissions:", "  contents: read"]
    env = header_env(header)
    if env:
        lines.append(env)
    lines += ["jobs:", f"  {job}:"]
    perm = [c for k, c in chunks if k == "permissions"]
    text = "\n".join(lines) + "\n" + "\n".join(perm + [body]) + "\n"
    return text, used, passed, secrets


def caller_job(job, chunks, file, used, passed, secrets, declared):
    out = [f"  {job}:"]
    out += [c for k, c in chunks if k in ("name", "needs", "if", "concurrency", "permissions")]
    out.append(f"    uses: ./.github/workflows/{file}")
    withs = []
    for name in used:
        if name not in declared:
            continue
        spec = declared[name]
        empty = EMPTY["boolean"] if spec.get("type") == "boolean" else "'" + str(spec.get("default", "")) + "'"
        withs.append(f"      {name}: ${{{{ inputs.{name} || {empty} }}}}")
    withs += [f"      {name}: ${{{{ {expr} }}}}" for name, expr in sorted(passed.items())]
    if withs:
        out += ["    with:"] + withs
    if secrets:
        out += ["    secrets:"] + [f"      {s}: ${{{{ secrets.{s} }}}}" for s in secrets]
    return "\n".join(out)


ORDER = {"none": 0, "read": 1, "write": 2}


def union_permissions(texts):
    """-> `permissions:` block text: the highest level of every scope any of the jobs' `permissions` blocks names (contents: read when none names any)."""
    top = {"contents": "read"}
    for text in texts:
        for scope, level in re.findall(r"^\s+([a-z-]+): (read|write|none)\s*$", text, re.M):
            if ORDER[level] > ORDER.get(top.get(scope, "none"), 0):
                top[scope] = level
    return "\n".join(["    permissions:"] + [f"      {k}: {v}" for k, v in sorted(top.items())])


def passes(names, declared):
    """-> (workflow_call input lines, the `with:` lines handing a caller's dispatch inputs over)"""
    inputs, withs = [], []
    for name in sorted(names):
        spec = declared[name]
        kind = "boolean" if spec.get("type") == "boolean" else "string"
        default = str(spec.get("default", False if kind == "boolean" else "")).lower() if kind == "boolean" else '"' + str(spec.get("default", "")) + '"'
        inputs += [f"      {name}:", f"        type: {kind}", f"        default: {default}"]
        empty = EMPTY["boolean"] if kind == "boolean" else "'" + str(spec.get("default", "")) + "'"
        withs.append(f"      {name}: ${{{{ inputs.{name} || {empty} }}}}")
    return inputs, withs


def split(text, prefix, inline=(), orchestrator=None, pack=wfsrc.compress):
    """-> {file name: text} for the logical workflow `text`: every job not in `inline` is cut out to `<prefix><job>.yml` (`_` written as `-`). With `orchestrator` (a file name) the caller holds only the triggers and one job `run` that calls that file, which holds every job (so a workflow with many jobs keeps its caller file small)."""
    header, jobs = cut(text)
    declared = dispatch_inputs(header)
    files, callers = {}, []
    for job, chunks in jobs:
        if job in inline:
            callers.append("\n".join([f"  {job}:"] + [c for _, c in chunks]))
            continue
        name = f"{prefix}{job.replace('_', '-')}.yml"
        body, used, passed, secrets = called_file(job, chunks, header, declared, prefix)
        files[name] = pack(body)
        callers.append(caller_job(job, chunks, name, used, passed, secrets, declared))
    jobs_text = "\n".join(callers) + "\n"
    if not orchestrator:
        files["<caller>"] = pack(header + "\n" + jobs_text)
        return files
    used = sorted(set(INPUT.findall(jobs_text)) & set(declared))
    secrets = sorted(set(SECRET.findall(jobs_text)))
    inputs, withs = passes(used, declared)
    lines = ["name: " + orchestrator.removesuffix(".yml"), "on:", "  workflow_call:", "    inputs:"] + inputs
    lines += ["    secrets:"] + [x for s in secrets for x in (f"      {s}:", "        required: false")]
    lines += ["permissions:", "  contents: read"]
    env = header_env(header)
    if env:
        lines.append(env)
    files[orchestrator] = pack("\n".join(lines) + "\njobs:\n" + jobs_text)
    perms = union_permissions(re.findall(r"^    permissions:\n(?:      .*\n?)+", jobs_text, re.M))
    top = ["  run:", perms, f"    uses: ./.github/workflows/{orchestrator}", "    with:"] + withs
    top += ["    secrets:"] + [f"      {s}: ${{{{ secrets.{s} }}}}" for s in secrets]
    files["<caller>"] = pack(header + "\n" + "\n".join(top) + "\n")
    return files


def flatten(files, caller):
    """-> the logical workflow dict again, from the generated caller text and the called files {file name: text}."""
    doc = yaml.safe_load(wfsrc.expand_aliases(files[caller]))
    if list(doc["jobs"]) == ["run"] and doc["jobs"]["run"].get("uses"):
        inner = yaml.safe_load(wfsrc.expand_aliases(files[doc["jobs"]["run"]["uses"].rsplit("/", 1)[1]]))
        doc = {**doc, "jobs": inner["jobs"]}
    jobs = {}
    for name, job in doc["jobs"].items():
        uses = job.get("uses")
        if not uses:
            jobs[name] = job
            continue
        called = yaml.safe_load(wfsrc.expand_aliases(files[uses.rsplit("/", 1)[1]]))
        inner = dict(called["jobs"][name])
        back = {}
        for key, value in (job.get("with") or {}).items():
            found = re.fullmatch(r"\$\{\{ (needs\..+?) \}\}", str(value))
            if found:
                back[key] = found.group(1)
        merged = {k: v for k, v in job.items() if k in ("needs", "if", "concurrency", "name")}
        merged.update(inner)
        if "permissions" in job:
            merged["permissions"] = job["permissions"]
        text = yaml.safe_dump(merged, sort_keys=False, width=10**6)
        for key, expr in back.items():
            text = re.sub(rf"\binputs\.{re.escape(key)}\b", expr, text)
        jobs[name] = yaml.safe_load(text)
    out = dict(doc)
    out["jobs"] = jobs
    return out


def logical_text(name):
    """-> the logical workflow `name` ("domains" or "hourly") as one plain text: the parts under `.github/workflows-src/<name>` joined in file-name order, without comments."""
    parts = sorted((ROOT / ".github/workflows-src" / name).glob("*.yml"))
    return wfsrc.strip_comments("".join(p.read_text() for p in parts))


def logical_compressed(name):
    """-> the same with repeats written as anchors, which is what the single generated file held before the split."""
    return wfsrc.compress(logical_text(name))
