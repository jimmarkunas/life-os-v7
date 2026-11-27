"""How long ordinary tickets take from first commit to merge, read-only, from GitHub pull requests and Jira times.

  python -m lifeos.sources.devtools.profile_tickets --last 20 [--jira jira.json] [--json]

Per merged PR titled `[LIFE-nnn]`: files and lines changed, minutes from first commit to merge, `pr-tests` runs,
failures before the first green, and (with a Jira file {"LIFE-nnn": {"start": ISO, "done": ISO}}) minutes from
Agent Go to Done. Numbers and issue keys only; it calls `gh` for reads.
"""

import argparse
import datetime as dt
import json
import re
import statistics
import subprocess
import sys

KEY = re.compile(r"^\[(LIFE-\d+)\]")
CI_WORKFLOW = "pr-tests.yml"
GENERATED = ("docs/CODEMAP.md", "docs/codemap.json", "docs/test-index.json", "INDEX.md")


def when(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def minutes(a, b):
    return round((when(b) - when(a)).total_seconds() / 60, 1)


def scope(paths):
    """The packages a PR touched, ignoring generated files: `lifeos/<pkg>/...` and `tests/<pkg>/...` count as <pkg>;
    `.github` as workflows; docs as docs; the rest as other.
    """
    out = set()
    for path in paths:
        if path in GENERATED:
            continue
        parts = path.split("/")
        if parts[0] in ("lifeos", "tests") and len(parts) > 2:
            out.add(parts[1])
        elif parts[0] == ".github":
            out.add("workflows")
        elif parts[0] == "docs":
            out.add("docs")
        else:
            out.add("other:" + parts[0].replace(".py", ""))
    return sorted(out)


def profile(pr, detail, runs, jira=None):
    key = KEY.match(pr["title"]).group(1)
    final = detail["headRefOid"]
    ordered = sorted(runs, key=lambda r: r["createdAt"])
    green = [r for r in ordered if r["headSha"] == final and r["conclusion"] == "success"]
    first_green = min(green, key=lambda r: r["updatedAt"]) if green else None
    failed_before = sum(
        1
        for r in ordered
        if r["conclusion"] == "failure" and (first_green is None or r["createdAt"] < first_green["createdAt"])
    )
    first_commit = min(c["committedDate"] for c in detail["commits"])
    files = [f["path"] for f in detail["files"]]
    row = {
        "pr": pr["number"],
        "key": key,
        "files": pr["changedFiles"],
        "added": pr["additions"],
        "removed": pr["deletions"],
        "commit_to_merge_min": minutes(first_commit, pr["mergedAt"]),
        "ci_runs": len(ordered),
        "cancelled_runs": sum(1 for r in ordered if r["conclusion"] == "cancelled"),
        "failed_before_green": failed_before,
        "first_run_to_green_min": minutes(ordered[0]["createdAt"], first_green["updatedAt"])
        if ordered and first_green
        else None,
        "scope": scope(files),
        "first_commit": first_commit,
        "merged_at": pr["mergedAt"],
    }
    if jira and key in jira and jira[key].get("start") and jira[key].get("done"):
        row["jira_start_to_done_min"] = minutes(jira[key]["start"], jira[key]["done"])
    return row


def summary(rows):
    out = {}
    for name in (
        "files",
        "added",
        "removed",
        "commit_to_merge_min",
        "ci_runs",
        "failed_before_green",
        "first_run_to_green_min",
    ):
        values = sorted(r[name] for r in rows if r.get(name) is not None)
        out[name] = (
            {"n": len(values), "median": statistics.median(values), "worst": values[-1]}
            if values
            else {"n": 0, "median": None, "worst": None}
        )
    tickets = {r["key"]: r["jira_start_to_done_min"] for r in rows if r.get("jira_start_to_done_min") is not None}
    values = sorted(tickets.values())
    out["jira_start_to_done_min_per_ticket"] = (
        {"n": len(values), "median": statistics.median(values), "worst": values[-1]}
        if values
        else {"n": 0, "median": None, "worst": None}
    )
    out["prs_with_a_failed_first_run"] = sum(1 for r in rows if r["failed_before_green"] > 0)
    out["prs_touching_more_than_one_package"] = sum(1 for r in rows if len(r["scope"]) > 1)
    return out


PARTS = ("queue_and_orientation_min", "building_min", "after_merge_to_done_min")


def split(rows, jira):
    """-> {ticket: {queue_and_orientation_min, building_min, after_merge_to_done_min, total_min}} for tickets with
    both Jira times and a PR: Agent Go to first commit, first commit to last merge, last merge to Done.
    """
    by_ticket = {}
    for row in rows:
        by_ticket.setdefault(row["key"], []).append(row)
    out = {}
    for key, prs in by_ticket.items():
        times = (jira or {}).get(key) or {}
        if not (times.get("start") and times.get("done")):
            continue
        first = min(prs, key=lambda r: when(r["first_commit"]))["first_commit"]
        last = max(prs, key=lambda r: when(r["merged_at"]))["merged_at"]
        out[key] = {
            "queue_and_orientation_min": minutes(times["start"], first),
            "building_min": minutes(first, last),
            "after_merge_to_done_min": minutes(last, times["done"]),
            "total_min": minutes(times["start"], times["done"]),
        }
    return out


def split_summary(tickets):
    if not tickets:
        return {"n": 0, "largest": None, "median_min": {}, "share_pct": {}, "sum_min": {}}
    sums = {part: round(sum(t[part] for t in tickets.values()), 1) for part in PARTS}
    total = sum(sums.values()) or 1
    return {
        "n": len(tickets),
        "largest": max(PARTS, key=lambda part: sums[part]),
        "sum_min": sums,
        "median_min": {part: statistics.median(t[part] for t in tickets.values()) for part in PARTS},
        "share_pct": {part: round(100 * sums[part] / total) for part in PARTS},
    }


def table(rows):
    head = "| PR | Ticket | Files | + / - | Commit to merge (min) | CI runs | Failed before green | First run to green (min) | Jira start to Done (min) | Packages |\n|---|---|---|---|---|---|---|---|---|---|\n"
    body = "".join(
        f"| {r['pr']} | {r['key']} | {r['files']} | +{r['added']} / -{r['removed']} | {r['commit_to_merge_min']} | {r['ci_runs']} | {r['failed_before_green']} | "
        f"{r['first_run_to_green_min'] if r['first_run_to_green_min'] is not None else 'n/a'} | {r.get('jira_start_to_done_min', 'n/a')} | {', '.join(r['scope'])} |\n"
        for r in rows
    )
    return head + body


def gh(args, run=subprocess.run):
    done = run(["gh", *args], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError("gh failed")
    return json.loads(done.stdout or "null")


def collect(last, jira=None, call=gh):
    listing = call(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            "200",
            "--json",
            "number,title,mergedAt,additions,deletions,changedFiles,headRefName",
        ]
    )
    mine = sorted((p for p in listing if KEY.match(p["title"])), key=lambda p: p["mergedAt"], reverse=True)[:last]
    rows = []
    for pr in mine:
        detail = call(["pr", "view", str(pr["number"]), "--json", "commits,files,headRefOid"])
        runs = call(
            [
                "run",
                "list",
                "--branch",
                pr["headRefName"],
                "--workflow",
                CI_WORKFLOW,
                "--limit",
                "100",
                "--json",
                "conclusion,createdAt,updatedAt,headSha",
            ]
        )
        rows.append(profile(pr, detail, runs, jira))
    return rows


def main(argv=None, call=gh, out=sys.stdout):
    parser = argparse.ArgumentParser(description="Read-only: how long the last merged [LIFE-nnn] pull requests took.")
    parser.add_argument("--last", type=int, default=20)
    parser.add_argument("--jira", help="JSON file {key: {start, done}} exported from Jira")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    jira = None
    if args.jira:
        with open(args.jira) as handle:
            jira = json.load(handle)
    rows = collect(args.last, jira, call)
    parts = split(rows, jira)
    if args.json:
        out.write(
            json.dumps(
                {"rows": rows, "summary": summary(rows), "split": parts, "split_summary": split_summary(parts)},
                indent=1,
            )
            + "\n"
        )
    else:
        out.write(
            table(rows)
            + "\n"
            + json.dumps(summary(rows), indent=1)
            + "\n"
            + json.dumps(split_summary(parts), indent=1)
            + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
