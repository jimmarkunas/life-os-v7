"""Branch archiving, branch deletes, and the weekly stale-branch report.

`archive` (on a closed pull request from this repository): tags the PR's head commit in THIS repository as
`archive/<branch>-pr<N>`, reads the tag back, then deletes the branch ONLY when its head is still the PR's head, no other
open PR uses it, and it is not on KEEP; otherwise it is archived and kept, with the reason. A merged PR is deleted as
always; a PR closed unmerged is deleted only while the repo switch is on (see `deletes_on`), otherwise nothing is written.
`report` (weekly): every branch besides main with its open PR and days idle (7+ is stale), written to the run's summary
and, with --live, to the one owned callout on the Weekly LIFE OS Review page. It also sweeps branches with no open PR idle
14+ days: tag `archive/<branch>-nopr`, read back, delete; with the switch off it only lists what it WOULD delete.
Fixed codes only, counts only in logs.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from lifeos.platform import ct_stamp, report_region, router

API = "https://api.github.com"
DEFAULT = "main"
KEEP = {"main", "claude/life41-edge-fixtures"}
STALE_DAYS = 7
IDLE_DELETE_DAYS = 14
# The repo switch for deleting unmerged and abandoned branches (LIFE-1071): the file's text is exactly "on" to delete;
# anything else, or a missing file, is off (dry run: the report lists what it would delete and nothing is written).
SWITCH_FILE = Path(__file__).resolve().parents[2] / ".github" / "branch-deletes"
TZ = ZoneInfo("America/Chicago")
MODULE = "v7-repo-hygiene"


def deletes_on(path=None):
    try:
        return (path or SWITCH_FILE).read_text().strip().lower() == "on"
    except OSError:
        return False


class BranchError(RuntimeError):
    """Fixed codes only."""


def _fail(code):
    return BranchError("BRANCHES_" + code)


def gh(path, token, method="GET", opener=urlopen, body=None):
    """One GitHub REST call -> parsed JSON (None for an empty body or a 404 on a GET); any other failure is a fixed code."""
    req = Request(
        API + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lifeos-v7-branches",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with opener(req, timeout=30) as reply:
            body = reply.read()
        return json.loads(body) if body else None
    except HTTPError as error:
        if error.code == 404 and method == "GET":
            return None
        raise _fail(f"GITHUB_HTTP_{error.code}") from None
    except (URLError, OSError, ValueError):
        raise _fail("GITHUB_UNREACHABLE") from None


def pages(path, token, opener=urlopen):
    out, page = [], 1
    while True:
        got = gh(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}", token, opener=opener) or []
        out += got
        if len(got) < 100:
            return out
        page += 1


def plan(branch, pr_head, branch_head, other_open_prs, keep=KEEP, merged=True):
    if branch in keep or branch == DEFAULT:
        return {"delete": False, "why": "KEEP_LIST"}
    if branch_head is None:
        return {"delete": False, "why": "BRANCH_ALREADY_GONE"}
    if branch_head != pr_head:
        return {"delete": False, "why": "NEWER_COMMITS_AFTER_MERGE" if merged else "NEWER_COMMITS_AFTER_CLOSE"}
    if other_open_prs:
        return {"delete": False, "why": "ANOTHER_OPEN_PR_USES_IT"}
    return {"delete": True, "why": "MERGED_AND_UNCHANGED" if merged else "CLOSED_AND_UNCHANGED"}


def tag_name(branch, number=None):
    return f"archive/{branch}-" + (f"pr{number}" if number else "nopr")


def ref_sha(repo, token, ref, opener=urlopen):
    found = gh(f"/repos/{repo}/git/ref/{ref}", token, opener=opener)
    return ((found or {}).get("object") or {}).get("sha")


def branch_ref(branch):
    return "heads/" + quote(branch, safe="")


def open_prs_for(repo, token, branch, opener=urlopen):
    owner = repo.split("/")[0]
    return pages(f"/repos/{repo}/pulls?state=open&head={quote(owner + ':' + branch, safe='')}", token, opener=opener)


def push_tag(repo, token, sha, tag, opener=urlopen):
    """Tag `sha` in this repository as `tag` (a lightweight tag, the job's own token) and read it back."""
    ref = "tags/" + quote(tag, safe="/")
    found = ref_sha(repo, token, ref, opener)
    if found == sha:
        return "ALREADY_ARCHIVED"
    if found:
        raise _fail("ARCHIVE_TAG_TAKEN")
    gh(f"/repos/{repo}/git/refs", token, method="POST", opener=opener, body={"ref": "refs/" + ref, "sha": sha})
    if ref_sha(repo, token, ref, opener) != sha:
        raise _fail("ARCHIVE_NOT_READ_BACK")
    return "ARCHIVED"


def archive(event, environ=os.environ, opener=urlopen, push=None, deletes=None):
    pr, repo = event["pull_request"], environ["GITHUB_REPOSITORY"]
    if pr["head"]["repo"]["full_name"] != pr["base"]["repo"]["full_name"]:
        return {"pr": pr["number"], "archive": "SKIPPED", "delete": False, "why": "FORK"}
    token, branch, sha, number = environ["GITHUB_TOKEN"], pr["head"]["ref"], pr["head"]["sha"], pr["number"]
    merged = bool(pr.get("merged"))
    allowed = merged or (deletes_on() if deletes is None else deletes)
    tag = tag_name(branch, number)
    push = push or (lambda s, t: push_tag(repo, token, s, t, opener))
    archived = push(sha, tag) if allowed else "DRY_RUN"  # archived and read back before anything is deleted
    head = ref_sha(repo, token, branch_ref(branch), opener)
    others = [p for p in open_prs_for(repo, token, branch, opener) if p.get("number") != number]
    decision = plan(branch, sha, head, others, merged=merged)
    if decision["delete"] and allowed:
        gh(f"/repos/{repo}/git/refs/{branch_ref(branch)}", token, method="DELETE", opener=opener)
    why = "DELETES_OFF_WOULD_DELETE" if decision["delete"] and not allowed else decision["why"]
    return {
        "pr": number,
        "branch": branch,
        "tag": tag,
        "archive": archived,
        "delete": decision["delete"] and allowed,
        "why": why,
    }


def sweep(branches, open_prs, now, repo, token, live, opener=urlopen, push=None, days=IDLE_DELETE_DAYS, keep=KEEP):
    """Branches with no open PR idle `days`+ days (not main, not KEEP): tag, read back, delete when `live`. A branch whose
    archive fails or whose head moved is kept. -> (counts, names): counts only ever reach a log; names go on the summary.
    """
    open_names = {p["branch"] for p in open_prs}
    push = push or (lambda s, t: push_tag(repo, token, s, t, opener))
    names, counts = [], {"candidates": 0, "deleted": 0, "kept": 0, "failed": 0}
    for b in sorted(branches, key=lambda x: x["name"]):
        if b["name"] in keep or b["name"] == DEFAULT or b["name"] in open_names or (now - b["last"]).days < days:
            continue
        counts["candidates"] += 1
        names.append(b["name"])
        if not live:
            continue
        try:
            push(b["sha"], tag_name(b["name"]))
            if ref_sha(repo, token, branch_ref(b["name"]), opener) != b["sha"] or open_prs_for(
                repo, token, b["name"], opener
            ):
                counts["kept"] += 1
                continue
            gh(f"/repos/{repo}/git/refs/{branch_ref(b['name'])}", token, method="DELETE", opener=opener)
            counts["deleted"] += 1
        except BranchError:
            counts["failed"] += 1
    return counts, names


def stale(branches, open_prs, now, days=STALE_DAYS, keep=KEEP):
    """branches: [{"name", "last": datetime}]; open_prs: [{"number", "branch"}]. -> rows, oldest first: {"branch", "pr",
    "idle_days", "stale", "kept"}.
    """
    by_branch = {}
    for p in open_prs:
        by_branch.setdefault(p["branch"], p["number"])
    rows = []
    for b in branches:
        if b["name"] == DEFAULT:
            continue
        idle = max(0, (now - b["last"]).days)
        rows.append(
            {
                "branch": b["name"],
                "pr": by_branch.get(b["name"]),
                "idle_days": idle,
                "stale": idle >= days,
                "kept": b["name"] in keep,
            }
        )
    return sorted(rows, key=lambda r: -r["idle_days"])


def lines(rows, now):
    stale_rows = [r for r in rows if r["stale"]]
    head = (
        f"{now.astimezone(TZ):%a}, {ct_stamp.when(now)} · {len(rows)} branches besides main · "
        f"{sum(1 for r in rows if r['pr'])} with an open PR · {len(stale_rows)} idle {STALE_DAYS}+ days"
    )
    body = [
        f"{('PR #' + str(r['pr'])) if r['pr'] else 'no PR'} · {r['branch']} · idle {r['idle_days']} days"
        + (" · kept on purpose" if r["kept"] else "")
        for r in stale_rows
    ]
    return [head] + (body or ["Nothing stale. Merged branches are archived and removed automatically."])


def read_repo(repo, token, opener=urlopen):
    branches = []
    for b in pages(f"/repos/{repo}/branches", token, opener=opener):
        commit = gh(f"/repos/{repo}/commits/{b['commit']['sha']}", token, opener=opener) or {}
        when = (((commit.get("commit") or {}).get("committer") or {}).get("date")) or ""
        last = datetime.fromisoformat(when.replace("Z", "+00:00"))
        branches.append({"name": b["name"], "sha": b["commit"]["sha"], "last": last})
    prs = [
        {"number": p["number"], "branch": p["head"]["ref"]}
        for p in pages(f"/repos/{repo}/pulls?state=open", token, opener=opener)
    ]
    return branches, prs


def find_region(client, page_id):
    found = []
    for block in report_region.children(client, page_id, _fail):
        if block.get("type") != "callout":
            continue
        kids = report_region.children(client, block["id"], _fail)
        if (
            kids
            and kids[0].get("type") in report_region.HEADINGS
            and report_region.plain(kids[0]).strip() == router.REPO_REGION
        ):
            found.append(block["id"])
    if len(found) > 1:
        raise _fail("REGION_AMBIGUOUS")
    return found[0] if found else None


def install_region(client, page_id):
    callout = {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [],
            "icon": {"type": "emoji", "emoji": "🧹"},
            "color": "gray_background",
            "children": [
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": router.REPO_REGION}}]},
                }
            ],
        },
    }
    result = client.call("PATCH", f"/blocks/{quote(page_id, safe='')}/children", {"children": [callout]})
    made = [b for b in (result or {}).get("results", []) if b.get("type") == "callout"]
    if not made:
        raise _fail("REGION_INSTALL_FAILED")
    return made[0]["id"]


def write_region(client, page_id, text_lines, live):
    """Rewrite the owned callout's text in place and read it back. -> "WRITTEN" | "DRY_RUN"."""
    block = find_region(client, page_id)
    if block is None:
        if not live:
            return "DRY_RUN"
        block = install_region(client, page_id)
    blocks = [
        {
            "object": "block",
            "type": "paragraph" if i == 0 else "bulleted_list_item",
            ("paragraph" if i == 0 else "bulleted_list_item"): {
                "rich_text": [{"type": "text", "text": {"content": t[:1900]}}]
            },
        }
        for i, t in enumerate(text_lines)
    ]
    before = report_region.page_digest(client, page_id, [block], _fail)
    report_region.replace_text(
        client, block, router.REPO_REGION, MODULE, blocks, {router.REPO_REGION: block}, _fail, "REPO", live
    )
    if report_region.page_digest(client, page_id, [block], _fail) != before:
        raise _fail("PAGE_CHANGED")  # anything outside the owned callout moved
    return "WRITTEN" if live else "DRY_RUN"


def writer(environ):
    from lifeos.platform.notion_client import Client  # noqa: PLC0415 - only a Notion run needs it

    return Client(
        {
            "NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused",
        }
    )


def sweep_lines(counts, names, live):
    state = "ON" if live else "OFF (dry run)"
    if not counts["candidates"]:
        return [f"Branch deletes {state}: no branch idle {IDLE_DELETE_DAYS}+ days without an open PR."]
    if not live:
        head = (
            f"Branch deletes {state}: {counts['candidates']} branches idle {IDLE_DELETE_DAYS}+ days with no open PR "
            "WOULD be archived and deleted. Nothing was deleted."
        )
        return [head] + [f"would delete · {n}" for n in names]
    return [
        f"Branch deletes {state}: {counts['candidates']} idle {IDLE_DELETE_DAYS}+ days with no open PR · "
        f"{counts['deleted']} archived and deleted · {counts['kept']} kept · {counts['failed']} failed"
    ]


def report(live, environ=os.environ, now=None, opener=urlopen, client=None, deletes=None, push=None):
    now = now or datetime.now(timezone.utc)
    repo, token = environ["GITHUB_REPOSITORY"], environ["GITHUB_TOKEN"]
    branches, prs = read_repo(repo, token, opener=opener)
    rows = stale(branches, prs, now)
    deleting = deletes_on() if deletes is None else deletes
    counts, names = sweep(branches, prs, now, repo, token, deleting, opener=opener, push=push)
    text = lines(rows, now) + sweep_lines(counts, names, deleting)
    summary = environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Repository hygiene\n\n" + "\n".join(f"- {t}" for t in text) + "\n")
    out = {"rows": len(rows), "stale": sum(r["stale"] for r in rows), "deletes": "ON" if deleting else "OFF", **counts}
    page = report_region.clean_id(environ.get("WEEKLY_REVIEW_PAGE_ID") or "")
    if not page:
        return {**out, "notion": "NOT_CONFIGURED"}
    client = client or writer(environ)
    return {**out, "notion": write_region(client, page, text, live)}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == ["archive"]:
            with open(os.environ["GITHUB_EVENT_PATH"]) as f:
                out = archive(json.load(f))
        elif argv[:1] == ["report"] and set(argv[1:]) <= {"--live"}:
            out = report("--live" in argv)
        else:
            print("usage: python -m lifeos.platform.branches archive | report [--live]", file=sys.stderr)
            return 2
    except BranchError as error:
        print(f"branches: {error}")
        return 1
    print("branches: " + json.dumps(out, sort_keys=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and argv == ["archive"]:
        with open(summary, "a") as f:
            f.write(
                f"Archived PR #{out.get('pr')} as `{out.get('tag')}` ({out.get('archive')}); branch {'deleted' if out.get('delete') else 'kept'} ({out.get('why')})\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
