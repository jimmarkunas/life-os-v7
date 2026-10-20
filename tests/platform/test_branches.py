"""D244 (LIFE-81): merged branches are archived (one tag per PR, read back) and deleted only when safe; a weekly read-only report lists stale branches
on the run summary and in V7's one owned callout on the Weekly LIFE OS Review page. GitHub, the archive repository and Notion are stand-ins."""
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from urllib.error import HTTPError

from lifeos.platform import branches, report_region, router
from tests.kit.notion import AgendaRegions

NOW = datetime(2026, 10, 26, 13, 37, tzinfo=timezone.utc)
REPO = "jimmarkunas/life-os-v7"


def event(number=401, branch="claude/x", sha="a" * 40, merged=True, fork=False):
    return {"pull_request": {"number": number, "merged": merged, "head": {"ref": branch, "sha": sha, "repo": {"full_name": "someone/fork" if fork else REPO}},
                             "base": {"repo": {"full_name": REPO}}}}


class GitHub:
    """A stand-in GitHub REST API: one branch head per name, open PRs, and a record of every DELETE."""

    def __init__(self, heads=None, open_prs=()):
        self.heads, self.open_prs, self.deleted, self.calls = dict(heads or {}), list(open_prs), [], []

    def __call__(self, req, timeout=None):
        method, path = req.get_method(), req.full_url[len(branches.API):]
        self.calls.append((method, path))
        if method == "DELETE":
            self.deleted.append(path)
            return io.BytesIO(b"")
        if "/git/ref/heads/" in path:
            name = path.split("/git/ref/heads/", 1)[1].replace("%2F", "/")
            if name not in self.heads:
                raise HTTPError(req.full_url, 404, "x", {}, None)
            return io.BytesIO(json.dumps({"object": {"sha": self.heads[name]}}).encode())
        if path.startswith(f"/repos/{REPO}/pulls?state=open&head="):
            branch = path.split("head=", 1)[1].split("&", 1)[0].replace("%3A", ":").replace("%2F", "/").split(":", 1)[1]
            return io.BytesIO(json.dumps([p for p in self.open_prs if p["head"]["ref"] == branch]).encode())
        raise AssertionError(path)


ENV = {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "gh-token", "V7_ARCHIVE_TOKEN": "archive-token"}


class Archive(unittest.TestCase):
    def go(self, gh, ev=None, pushed=None):
        record = []

        def push(sha, tag, token):
            record.append((sha, tag, token))
            if pushed:
                raise pushed
            return "ARCHIVED"
        out = branches.archive(ev or event(), ENV, opener=gh, push=push)
        return out, record

    def test_a_merged_unchanged_branch_is_archived_under_its_pr_tag_then_deleted(self):
        gh = GitHub(heads={"claude/x": "a" * 40})
        out, record = self.go(gh)
        self.assertEqual(record, [("a" * 40, "v7-archive/claude/x-pr401", "archive-token")])
        self.assertEqual((out["archive"], out["delete"], out["why"]), ("ARCHIVED", True, "MERGED_AND_UNCHANGED"))
        self.assertEqual(gh.deleted, [f"/repos/{REPO}/git/refs/heads/claude%2Fx"])

    def test_a_reused_branch_with_newer_commits_is_archived_and_kept(self):
        gh = GitHub(heads={"claude/x": "b" * 40})
        out, _ = self.go(gh)
        self.assertEqual((out["delete"], out["why"], gh.deleted), (False, "NEWER_COMMITS_AFTER_MERGE", []))

    def test_a_branch_another_open_pr_uses_is_kept(self):
        gh = GitHub(heads={"claude/x": "a" * 40}, open_prs=[{"number": 405, "head": {"ref": "claude/x"}}])
        out, _ = self.go(gh)
        self.assertEqual((out["delete"], out["why"], gh.deleted), (False, "ANOTHER_OPEN_PR_USES_IT", []))

    def test_a_keep_list_branch_is_never_deleted(self):
        gh = GitHub(heads={"claude/life41-edge-fixtures": "a" * 40})
        out, _ = self.go(gh, event(branch="claude/life41-edge-fixtures"))
        self.assertEqual((out["delete"], out["why"], gh.deleted), (False, "KEEP_LIST", []))

    def test_an_unmerged_or_fork_pr_touches_nothing(self):
        for ev in (event(merged=False), event(fork=True)):
            gh = GitHub(heads={"claude/x": "a" * 40})
            out, record = self.go(gh, ev)
            self.assertEqual((out["archive"], record, gh.calls), ("SKIPPED", [], []))

    def test_nothing_is_deleted_unless_the_archive_was_read_back(self):
        gh = GitHub(heads={"claude/x": "a" * 40})
        with self.assertRaises(branches.BranchError):
            self.go(gh, pushed=branches.BranchError("BRANCHES_ARCHIVE_NOT_READ_BACK"))
        self.assertEqual(gh.deleted, [])


class PushTag(unittest.TestCase):
    """The real git path against a local stand-in for life-os-automation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        git = lambda *a, cwd=self.tmp: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout   # noqa: E731
        self.archive = os.path.join(self.tmp, "archive.git")
        git("init", "-q", "--bare", self.archive)
        work = os.path.join(self.tmp, "v7")
        git("init", "-q", work)
        open(os.path.join(work, "f"), "w").write("x")
        git("add", "f", cwd=work)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c", cwd=work)
        self.sha = git("rev-parse", "HEAD", cwd=work).strip()
        git("remote", "add", "origin", work, cwd=work)
        self.work = work

    def push(self, sha, tag):
        here = os.getcwd()
        os.chdir(self.work)
        try:
            with mock.patch.object(branches, "ARCHIVE_REPO", self.archive):
                return branches.push_tag(sha, tag, "archive-token")
        finally:
            os.chdir(here)

    def test_the_tag_is_pushed_and_read_back_and_a_second_run_is_a_no_op(self):
        self.assertEqual(self.push(self.sha, "v7-archive/claude/x-pr1"), "ARCHIVED")
        self.assertEqual(self.push(self.sha, "v7-archive/claude/x-pr1"), "ALREADY_ARCHIVED")

    def test_an_existing_tag_on_another_commit_is_never_overwritten(self):
        self.push(self.sha, "v7-archive/claude/x-pr1")
        with mock.patch.object(branches, "_remote_tag", lambda tag, header: "f" * 40):
            with self.assertRaises(branches.BranchError) as ctx:
                self.push(self.sha, "v7-archive/claude/x-pr1")
        self.assertEqual(str(ctx.exception), "BRANCHES_ARCHIVE_TAG_TAKEN")


class Report(unittest.TestCase):
    BRANCHES = [{"name": "main", "last": NOW}, {"name": "docs/old", "last": NOW - timedelta(days=20)}, {"name": "claude/fresh", "last": NOW - timedelta(days=2)},
                {"name": "claude/life41-edge-fixtures", "last": NOW - timedelta(days=8)}]

    def test_stale_branches_are_listed_oldest_first_with_their_open_pr(self):
        rows = branches.stale(self.BRANCHES, [{"number": 191, "branch": "docs/old"}], NOW)
        self.assertEqual([(r["branch"], r["pr"], r["idle_days"], r["stale"], r["kept"]) for r in rows],
                         [("docs/old", 191, 20, True, False), ("claude/life41-edge-fixtures", None, 8, True, True), ("claude/fresh", None, 2, False, False)])
        text = branches.lines(rows, NOW)
        self.assertEqual(text[0], "Updated Mon Oct 26, 8:37 AM CDT · 3 branches besides main · 1 with an open PR · 2 idle 7+ days")
        self.assertEqual(text[1:], ["PR #191 · docs/old · idle 20 days", "no PR · claude/life41-edge-fixtures · idle 8 days · kept on purpose"])

    def test_nothing_stale_says_so(self):
        self.assertEqual(branches.lines(branches.stale([{"name": "main", "last": NOW}], [], NOW), NOW)[1:],
                         ["Nothing stale. Merged branches are archived and removed automatically."])


class ReviewPage(AgendaRegions):
    """The Weekly LIFE OS Review page: Jim's protocol callout and a database, then (after the first live run) V7's one owned callout."""

    def __init__(self):
        super().__init__()
        self.children = {"review-page": [{"id": "protocol", "type": "callout", "has_children": True, "callout": {}},
                                         {"id": "reviews-db", "type": "child_database", "has_children": False, "child_database": {}}],
                         "protocol": [self._paragraph("Purpose: the weekly control loop")]}
        self.metas = {"protocol": self.children["review-page"][0]}

    def call(self, method, path, body=None):
        clean = path.split("?", 1)[0]
        if method == "PATCH" and clean == "/blocks/review-page/children":               # the one-time install, appended at the end of the page
            self.log.append(("INSTALL", path))
            spec = body["children"][0]
            callout = {"id": "repo-callout", "type": "callout", "has_children": True, "callout": {"rich_text": []}}
            heading = spec["callout"]["children"][0]
            self.children["review-page"].append(callout)
            self.metas["repo-callout"] = callout
            self.children["repo-callout"] = [self._text_block("repo-heading", heading["type"], heading[heading["type"]]["rich_text"][0]["text"]["content"])]
            return {"results": [callout]}
        return super().call(method, path, body)


class OwnedCallout(unittest.TestCase):
    def test_the_first_live_run_installs_the_callout_at_the_end_and_writes_it_and_jims_content_is_untouched(self):
        page = ReviewPage()
        before = [dict(b) for b in page.children["review-page"]], [dict(b) for b in page.children["protocol"]]
        self.assertEqual(branches.write_region(page, "review-page", ["status line", "PR #191 · docs/old · idle 20 days"], live=True), "WRITTEN")
        self.assertEqual([b["id"] for b in page.children["review-page"]], ["protocol", "reviews-db", "repo-callout"])
        self.assertEqual([report_region.plain(b) for b in page.children["repo-callout"]], [router.REPO_REGION, "status line", "PR #191 · docs/old · idle 20 days"])
        self.assertEqual((page.children["review-page"][:2], page.children["protocol"]), before)
        branches.write_region(page, "review-page", ["new status"], live=True)          # the next week rewrites the same callout in place
        self.assertEqual([report_region.plain(b) for b in page.children["repo-callout"]], [router.REPO_REGION, "new status"])
        self.assertEqual(sum(1 for m, _ in page.log if m == "INSTALL"), 1)

    def test_a_dry_run_installs_and_writes_nothing(self):
        page = ReviewPage()
        self.assertEqual(branches.write_region(page, "review-page", ["status"], live=False), "DRY_RUN")
        self.assertEqual([b["id"] for b in page.children["review-page"]], ["protocol", "reviews-db"])

    def test_the_region_is_owned_by_this_module_alone(self):
        self.assertEqual(router.OWNERS[router.REPO_REGION], branches.MODULE)


class JimsPageIsProtected(unittest.TestCase):
    def test_a_write_that_would_change_anything_outside_the_callout_fails_by_name(self):
        page = ReviewPage()
        branches.write_region(page, "review-page", ["status"], live=True)
        real = report_region.replace_text

        def meddling(client, *a, **k):
            out = real(client, *a, **k)
            client.children["protocol"].append(client._paragraph("something changed Jim's protocol"))
            return out
        with mock.patch.object(report_region, "replace_text", meddling):
            with self.assertRaises(branches.BranchError) as ctx:
                branches.write_region(page, "review-page", ["status two"], live=True)
        self.assertEqual(str(ctx.exception), "BRANCHES_PAGE_CHANGED")


class RealClient(unittest.TestCase):
    """Oct 20, 2:13 PM CT: the first live report built the real Notion client wrongly (NOTION_CONFIG_MISSING); every other test used a stand-in."""

    def test_the_report_builds_the_real_notion_client_from_the_cards_token_alone(self):
        from lifeos.platform.notion_client import Client, NotionError
        client = branches.writer({"NOTION_JIRA_TOKEN": "secret-token"})
        self.assertIsInstance(client, Client)
        self.assertEqual(client.token, "secret-token")
        with self.assertRaises(NotionError):
            branches.writer({})                                                       # no token: a named failure, never a client without one


class PushRefusal(unittest.TestCase):
    """Oct 20, 4:45 PM CT: the first real archive push was refused and the run only said ARCHIVE_PUSH_FAILED. Each refusal now has its own code."""

    def test_each_refusal_is_named_and_git_says_nothing_else(self):
        cases = {"remote: refusing to allow a Personal Access Token to create or update workflow `.github/workflows/x.yml` without `workflow` scope":
                 "ARCHIVE_PUSH_NEEDS_WORKFLOWS_PERMISSION",
                 "remote: Permission to jimmarkunas/life-os-automation.git denied to jimmarkunas.\nfatal: unable to access: The requested URL returned error: 403": "ARCHIVE_PUSH_FORBIDDEN",
                 " ! [rejected]        x -> x (already exists)": "ARCHIVE_PUSH_REJECTED",
                 "fatal: something else": "ARCHIVE_PUSH_FAILED", "": "ARCHIVE_PUSH_FAILED"}
        for stderr, code in cases.items():
            self.assertEqual(branches.push_refusal(stderr), code, stderr)
