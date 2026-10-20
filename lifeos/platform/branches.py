"""LIFE-81 (Jim, Oct 20 2026; D244): branch archiving on merge, and the weekly stale-branch report.

`archive` (the branches workflow, on a merged pull request from this repository): tags the PR's head commit in life-os-automation as
`v7-archive/<branch>-pr<N>` (one tag per PR, so a reused branch name never collides), reads the tag back, then deletes the branch ONLY when it is
safe: its head is still the merged head (nothing pushed after the merge), no other open PR uses it, and it is not on KEEP (branches used by name,
e.g. the fit-edge-test fixtures). Otherwise the branch is archived and kept, with the reason. An existing tag on another commit is never overwritten.

`report` (weekly, Monday morning; or on demand): every branch besides main with its open PR (if any) and days since its last commit; idle 7+ days
is stale. Written to the run's summary, and with --live to V7's one owned callout on the Weekly LIFE OS Review page (router.REPO_REGION), rewritten
in place and read back. Read-only on GitHub: nothing is closed or deleted; Jim decides.

Fixed codes only; never a token, and nothing from a commit or PR body."""
import base64
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from lifeos.platform import report_region, router

API = "https://api.github.com"
ARCHIVE_REPO = "https://github.com/jimmarkunas/life-os-automation.git"
DEFAULT = "main"
KEEP = {"main", "claude/life41-edge-fixtures"}            # used by name: fit-edge-test runs from the fixtures branch (LIFE-81 comment 10871)
STALE_DAYS = 7
TZ = ZoneInfo("America/Chicago")
MODULE = "v7-repo-hygiene"


class BranchError(RuntimeError):
    """Fixed codes only."""


def _fail(code):
    return BranchError("BRANCHES_" + code)


def gh(path, token, method="GET", opener=urlopen):
    """One GitHub REST call -> parsed JSON (None for an empty body; None for 404 on a GET). Anything else failing is a fixed code."""
    req = Request(API + path, method=method, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "lifeos-v7-branches"})
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


# --- archive on merge -------------------------------------------------------------------------------------------------------------------------

def plan(branch, merged_head, branch_head, other_open_prs, keep=KEEP):
    """-> {"delete", "why"}. Pure: the one decision the workflow acts on."""
    if branch in keep or branch == DEFAULT:
        return {"delete": False, "why": "KEEP_LIST"}
    if branch_head is None:
        return {"delete": False, "why": "BRANCH_ALREADY_GONE"}
    if branch_head != merged_head:
        return {"delete": False, "why": "NEWER_COMMITS_AFTER_MERGE"}
    if other_open_prs:
        return {"delete": False, "why": "ANOTHER_OPEN_PR_USES_IT"}
    return {"delete": True, "why": "MERGED_AND_UNCHANGED"}


def tag_name(branch, number):
    return f"v7-archive/{branch}-pr{number}"


def _git(*args, header=None):
    cmd = ["git", *(["-c", f"http.extraHeader={header}"] if header else []), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _remote_tag(tag, header):
    out = _git("ls-remote", ARCHIVE_REPO, f"refs/tags/{tag}", header=header)
    if out.returncode != 0:
        raise _fail("ARCHIVE_UNREADABLE")
    line = out.stdout.strip().split("\n")[0] if out.stdout.strip() else ""
    return line.split()[0] if line else None


def push_tag(sha, tag, token):
    """Tag `sha` in life-os-automation and read it back. Idempotent for the same commit; another commit already there is never overwritten."""
    header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    found = _remote_tag(tag, header)
    if found == sha:
        return "ALREADY_ARCHIVED"
    if found:
        raise _fail("ARCHIVE_TAG_TAKEN")
    if _git("fetch", "--quiet", "--no-tags", "origin", sha).returncode != 0:              # the PR head, by id (it stays reachable through refs/pull)
        raise _fail("HEAD_NOT_FETCHED")
    if _git("push", "--quiet", ARCHIVE_REPO, f"{sha}:refs/tags/{tag}", header=header).returncode != 0:
        raise _fail("ARCHIVE_PUSH_FAILED")
    if _remote_tag(tag, header) != sha:
        raise _fail("ARCHIVE_NOT_READ_BACK")
    return "ARCHIVED"


def archive(event, environ=os.environ, opener=urlopen, push=None):
    """One merged pull request -> {"pr", "branch", "tag", "archive", "delete", "why"}."""
    pr, repo = event["pull_request"], environ["GITHUB_REPOSITORY"]
    if not pr.get("merged") or pr["head"]["repo"]["full_name"] != pr["base"]["repo"]["full_name"]:
        return {"pr": pr["number"], "archive": "SKIPPED", "delete": False, "why": "NOT_MERGED_OR_FORK"}
    token, branch, sha, number = environ["GITHUB_TOKEN"], pr["head"]["ref"], pr["head"]["sha"], pr["number"]
    tag = tag_name(branch, number)
    archived = (push or push_tag)(sha, tag, environ["V7_ARCHIVE_TOKEN"])                # archived and read back before anything is deleted
    ref = gh(f"/repos/{repo}/git/ref/heads/{quote(branch, safe='')}", token, opener=opener)
    head = ((ref or {}).get("object") or {}).get("sha")
    owner = repo.split("/")[0]
    others = [p for p in pages(f"/repos/{repo}/pulls?state=open&head={quote(owner + ':' + branch, safe='')}", token, opener=opener) if p.get("number") != number]
    decision = plan(branch, sha, head, others)
    if decision["delete"]:
        gh(f"/repos/{repo}/git/refs/heads/{quote(branch, safe='')}", token, method="DELETE", opener=opener)
    return {"pr": number, "branch": branch, "tag": tag, "archive": archived, "delete": decision["delete"], "why": decision["why"]}


# --- weekly report -----------------------------------------------------------------------------------------------------------------------------

def stale(branches, open_prs, now, days=STALE_DAYS, keep=KEEP):
    """branches: [{"name", "last": datetime}]; open_prs: [{"number", "branch"}]. -> rows (oldest first): {"branch", "pr", "idle_days", "stale", "kept"}. Pure."""
    by_branch = {}
    for p in open_prs:
        by_branch.setdefault(p["branch"], p["number"])
    rows = []
    for b in branches:
        if b["name"] == DEFAULT:
            continue
        idle = max(0, (now - b["last"]).days)
        rows.append({"branch": b["name"], "pr": by_branch.get(b["name"]), "idle_days": idle, "stale": idle >= days, "kept": b["name"] in keep})
    return sorted(rows, key=lambda r: -r["idle_days"])


def lines(rows, now):
    """The callout's text: one status line, then one line per stale branch. Branch names and PR numbers only."""
    stale_rows = [r for r in rows if r["stale"]]
    head = (f"Updated {now.astimezone(TZ).strftime('%a %b %-d, %-I:%M %p %Z')} · {len(rows)} branches besides main · "
            f"{sum(1 for r in rows if r['pr'])} with an open PR · {len(stale_rows)} idle {STALE_DAYS}+ days")
    body = [f"{('PR #' + str(r['pr'])) if r['pr'] else 'no PR'} · {r['branch']} · idle {r['idle_days']} days" + (" · kept on purpose" if r["kept"] else "")
            for r in stale_rows]
    return [head] + (body or ["Nothing stale. Merged branches are archived and removed automatically."])


def read_repo(repo, token, opener=urlopen):
    branches = []
    for b in pages(f"/repos/{repo}/branches", token, opener=opener):
        commit = gh(f"/repos/{repo}/commits/{b['commit']['sha']}", token, opener=opener) or {}
        when = (((commit.get("commit") or {}).get("committer") or {}).get("date")) or ""
        branches.append({"name": b["name"], "last": datetime.fromisoformat(when.replace("Z", "+00:00"))})
    prs = [{"number": p["number"], "branch": p["head"]["ref"]} for p in pages(f"/repos/{repo}/pulls?state=open", token, opener=opener)]
    return branches, prs


def find_region(client, page_id):
    """-> the one V7-owned callout on the page (first child: the region heading), or None. Two are refused."""
    found = []
    for block in report_region.children(client, page_id, _fail):
        if block.get("type") != "callout":
            continue
        kids = report_region.children(client, block["id"], _fail)
        if kids and kids[0].get("type") in report_region.HEADINGS and report_region.plain(kids[0]).strip() == router.REPO_REGION:
            found.append(block["id"])
    if len(found) > 1:
        raise _fail("REGION_AMBIGUOUS")
    return found[0] if found else None


def install_region(client, page_id):
    """Append the empty owned callout at the end of the page (first live run only). -> its id."""
    callout = {"object": "block", "type": "callout", "callout": {"rich_text": [], "icon": {"type": "emoji", "emoji": "🧹"}, "color": "gray_background",
               "children": [{"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": router.REPO_REGION}}]}}]}}
    result = client.call("PATCH", f"/blocks/{quote(page_id, safe='')}/children", {"children": [callout]})
    made = [b for b in (result or {}).get("results", []) if b.get("type") == "callout"]
    if not made:
        raise _fail("REGION_INSTALL_FAILED")
    return made[0]["id"]


def write_region(client, page_id, text_lines, live):
    """Rewrite the owned callout's text in place and read it back (report_region.replace_text). -> "WRITTEN" | "DRY_RUN"."""
    block = find_region(client, page_id)
    if block is None:
        if not live:
            return "DRY_RUN"
        block = install_region(client, page_id)
    blocks = [{"object": "block", "type": "paragraph" if i == 0 else "bulleted_list_item",
               ("paragraph" if i == 0 else "bulleted_list_item"): {"rich_text": [{"type": "text", "text": {"content": t[:1900]}}]}} for i, t in enumerate(text_lines)]
    before = report_region.page_digest(client, page_id, [block], _fail)                  # every other block on Jim's page, by content
    report_region.replace_text(client, block, router.REPO_REGION, MODULE, blocks, {router.REPO_REGION: block}, _fail, "REPO", live)
    if report_region.page_digest(client, page_id, [block], _fail) != before:
        raise _fail("PAGE_CHANGED")                                                       # anything outside the owned callout moved: fail by name
    return "WRITTEN" if live else "DRY_RUN"


def report(live, environ=os.environ, now=None, opener=urlopen, client=None):
    now = now or datetime.now(timezone.utc)
    branches, prs = read_repo(environ["GITHUB_REPOSITORY"], environ["GITHUB_TOKEN"], opener=opener)
    rows = stale(branches, prs, now)
    text = lines(rows, now)
    summary = environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## Repository hygiene\n\n" + "\n".join(f"- {t}" for t in text) + "\n")
    page = report_region.clean_id(environ.get("WEEKLY_REVIEW_PAGE_ID") or "")
    if not page:
        return {"rows": len(rows), "stale": sum(r["stale"] for r in rows), "notion": "NOT_CONFIGURED"}
    if client is None:
        from lifeos.platform.notion_client import Client                                  # noqa: PLC0415 - only a Notion run needs it
        client = Client({**environ, "NOTION_API_TOKEN": environ.get("NOTION_JIRA_TOKEN", "")})
    return {"rows": len(rows), "stale": sum(r["stale"] for r in rows), "notion": write_region(client, page, text, live)}


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
            f.write(f"Archived PR #{out.get('pr')} as `{out.get('tag')}` ({out.get('archive')}); branch {'deleted' if out.get('delete') else 'kept'} ({out.get('why')})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
