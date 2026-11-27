"""LIFE-1076: one screen for a failed CI run, so an agent does not read a 2,000-line log.

    python tools/ci_failure_summary.py RUN_ID            summary of a GitHub Actions run (needs `gh`, read only)
    python tools/ci_failure_summary.py --log FILE [--sha SHA] [--run RUN_ID]
                                                         the same from a saved `gh run view RUN --log-failed` file

It prints the head SHA, each failed job and step, each failed test id with the file and line of its failing frame and the
assertion line, and for a step that is not a unittest run (ruff, mypy, a workflow step) its `##[error]` lines. The
per-module `shard-seconds {...}` records are dropped. It reads only; nothing in CI is hidden or truncated, and the full log
stays at `gh run view RUN --log-failed`. No secret and no CI permission: it uses the token `gh` already has.
"""

import argparse
import pathlib
import re
import subprocess
import sys

MAX_LINES = 28  # the ticket's bar is one screen, under 30 lines
WIDTH = 150
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\^\[\[[0-9;]*[A-Za-z]")
STAMP = re.compile(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ?")
HEADER = re.compile(r"^(FAIL|ERROR): (\S+) \((.+)\)\s*$")
FRAME = re.compile(r'File "([^"]+)", line (\d+)')
BAR = re.compile(r"^(=|-){20,}$")
ERROR_LINE = re.compile(r"^(?:[\w.]+\.)?\w*(?:Error|Exception|Failure|Interrupt)\b.*$")
STEP_NOISE = re.compile(r"^##\[error\]Process completed with exit code \d+\.?$")
SOURCE_DIRS = ("tests/", "lifeos/", "tools/")


def parse_log(text):
    """[(job, step, [body lines])] from `gh run view --log-failed` text (`job<TAB>step<TAB>timestamp body`); a line
    without that shape (a log saved from a single step) goes to one unnamed job.
    """
    order, found = [], {}
    for raw in text.splitlines():
        parts = raw.split("\t", 2)
        if len(parts) == 3:
            job, step, body = parts
        else:
            job, step, body = "(log)", "", raw
        key = (job.strip(), step.strip())
        if key not in found:
            found[key] = []
            order.append(key)
        found[key].append(STAMP.sub("", ANSI.sub("", body)).rstrip())
    return [(job, step, found[(job, step)]) for job, step in order]


def _short(path):
    for marker in SOURCE_DIRS:
        at = path.rfind("/" + marker)
        if at >= 0:
            return path[at + 1 :]
        if path.startswith(marker):
            return path
    return path


def failed_tests(lines):
    """[(test id, 'file:line', assertion line)] for each unittest FAIL/ERROR block, in log order."""
    out = []
    for at, line in enumerate(lines):
        match = HEADER.match(line)
        if not match:
            continue
        block = []
        for nxt in lines[at + 1 :]:
            if BAR.match(nxt) and block and any(x.strip() for x in block):
                break
            block.append(nxt)
        frames = [m for x in block for m in FRAME.findall(x)]
        ours = [f for f in frames if _short(f[0]).startswith(SOURCE_DIRS)]
        # the failing frame: the first one in the test file; the deepest one of ours when none is a test; else any
        in_tests = [f for f in ours if _short(f[0]).startswith("tests/")]
        where = in_tests[0] if in_tests else (ours[-1] if ours else (frames[-1] if frames else ("", "")))
        said = next((x.strip() for x in block if ERROR_LINE.match(x.strip()) and not x.startswith(" ")), "")
        if not said:
            said = next((x.strip() for x in reversed(block) if x.strip() and not BAR.match(x)), "")
        test_id = match.group(3) if "." in match.group(3) and " " not in match.group(3) else match.group(2)
        out.append((test_id, f"{_short(where[0])}:{where[1]}" if where[0] else "", said))
    return out


def error_lines(lines, limit=3):
    """The `##[error]`, mypy and ruff lines of a step that is not a unittest run."""
    keep = []
    for line in lines:
        text = line.strip()
        if STEP_NOISE.match(text) or not text:
            continue
        if text.startswith("##[error]") or ": error:" in text or text.startswith("Would reformat") or "Error:" in text:
            if text not in keep:
                keep.append(text)
    return keep[:limit] or [x.strip() for x in lines if x.strip()][-2:]


def clip(text, width=WIDTH):
    return text if len(text) <= width else text[: width - 3] + "..."


def summarize(text, sha="", run="", url=""):
    """The summary as a list of lines, at most MAX_LINES."""
    head = "CI failure summary" + (f" for run {run}" if run else "")
    out = [head, f"head sha: {sha or 'unknown'}"]
    seen = {}
    body = []
    steps = parse_log(text)
    jobs = []
    for job, step, lines in steps:
        lines = [x for x in lines if not x.startswith("shard-seconds ")]
        if job not in jobs:
            jobs.append(job)
        tests = failed_tests(lines)
        if tests:
            sig = tuple(t[0] for t in tests)
            if sig in seen:
                body.append(f"{job}: {len(tests)} failed, same as {seen[sig]}")
                continue
            seen[sig] = job
            body.append(clip(f"{job} / {step or 'step'}", 70) + f": {len(tests)} failed test(s)")
            for test_id, where, said in tests:
                body.append(f"  {test_id}")
                body.append(clip(f"    {where}  {said}".rstrip()))
        else:
            bad = error_lines(lines)
            if bad:
                body.append(clip(f"{job} / {step or 'step'}", 70) + ": failed")
                body.extend(clip("    " + x) for x in bad)
    out.append("failed jobs: " + (", ".join(jobs) if jobs else "none found in this log"))
    room = MAX_LINES - len(out) - 1
    if len(body) > room:
        hidden = len(body) - (room - 1)
        body = body[: room - 1] + [f"  ... {hidden} more line(s) in the full log"]
    out.extend(body)
    out.append("full log: gh run view " + (run or "RUN_ID") + " --log-failed" + (f"   {url}" if url else ""))
    return out


def gh(*args):
    done = subprocess.run(["gh", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit("ci_failure_summary: gh " + " ".join(args[:3]) + " failed: " + done.stderr.strip()[:200])
    return done.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run", nargs="?", help="GitHub Actions run id")
    parser.add_argument("--log", help="a saved `gh run view RUN --log-failed` file")
    parser.add_argument("--sha", default="")
    args = parser.parse_args(argv)
    if args.log:
        sha, run, url = args.sha, args.run or "", ""
        text = pathlib.Path(args.log).read_text(errors="replace")
    elif args.run:
        import json

        info = json.loads(gh("run", "view", args.run, "--json", "headSha,url"))
        sha, run, url = info.get("headSha", ""), args.run, info.get("url", "")
        text = gh("run", "view", args.run, "--log-failed")
    else:
        parser.error("give a run id or --log FILE")
    print("\n".join(summarize(text, sha[:12], run, url)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
