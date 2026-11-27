"""closed branches are archived (one tag per PR, read back) and deleted only when safe; idle branches are swept"""

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from lifeos.platform import branches, report_region, router
from tests.kit.notion import AgendaRegions

NOW = datetime(2026, 10, 26, 13, 37, tzinfo=timezone.utc)
REPO = "jimmarkunas/life-os-v7"


def event(number=401, branch="claude/x", sha="a" * 40, merged=True, fork=False):
    return {
        "pull_request": {
            "number": number,
            "merged": merged,
            "head": {"ref": branch, "sha": sha, "repo": {"full_name": "someone/fork" if fork else REPO}},
            "base": {"repo": {"full_name": REPO}},
        }
    }


class GitHub:
    """A stand-in GitHub REST API: one branch head per name, tags, open PRs, and a record of every write."""

    def __init__(self, heads=None, open_prs=(), tags=None, drop_tag_writes=False):
        self.heads, self.open_prs, self.tags = dict(heads or {}), list(open_prs), dict(tags or {})
        self.deleted, self.created, self.calls, self.drop_tag_writes = [], [], [], drop_tag_writes

    def __call__(self, req, timeout=None):
        method, path = req.get_method(), req.full_url[len(branches.API) :]
        self.calls.append((method, path))
        if method == "DELETE":
            self.deleted.append(path)
            name = path.split("/git/refs/heads/", 1)[1].replace("%2F", "/")
            self.heads.pop(name, None)
            return io.BytesIO(b"")
        if method == "POST" and path == f"/repos/{REPO}/git/refs":
            body = json.loads(req.data)
            self.created.append((body["ref"], body["sha"]))
            if not self.drop_tag_writes:
                self.tags[body["ref"].split("refs/tags/", 1)[1]] = body["sha"]
            return io.BytesIO(json.dumps({"ref": body["ref"]}).encode())
        if "/git/ref/tags/" in path:
            name = path.split("/git/ref/tags/", 1)[1]
            if name not in self.tags:
                raise HTTPError(req.full_url, 404, "x", {}, None)
            return io.BytesIO(json.dumps({"object": {"sha": self.tags[name]}}).encode())
        if "/git/ref/heads/" in path:
            name = path.split("/git/ref/heads/", 1)[1].replace("%2F", "/")
            if name not in self.heads:
                raise HTTPError(req.full_url, 404, "x", {}, None)
            return io.BytesIO(json.dumps({"object": {"sha": self.heads[name]}}).encode())
        if path.startswith(f"/repos/{REPO}/pulls?state=open&head="):
            branch = path.split("head=", 1)[1].split("&", 1)[0].replace("%3A", ":").replace("%2F", "/").split(":", 1)[1]
            return io.BytesIO(json.dumps([p for p in self.open_prs if p["head"]["ref"] == branch]).encode())
        raise AssertionError(path)


ENV = {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "gh-token"}


class Archive(unittest.TestCase):
    def go(self, gh, ev=None, deletes=False, pushed=None):
        record = []

        def push(sha, tag):
            record.append((sha, tag))
            if pushed:
                raise pushed
            return "ARCHIVED"

        out = branches.archive(ev or event(), ENV, opener=gh, push=push, deletes=deletes)
        return out, record

    def test_a_merged_unchanged_branch_is_archived_under_its_pr_tag_then_deleted(self):
        gh = GitHub(heads={"claude/x": "a" * 40})
        out, record = self.go(gh)
        self.assertEqual(record, [("a" * 40, "archive/claude/x-pr401")])
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
        for merged in (True, False):
            gh = GitHub(heads={"claude/life41-edge-fixtures": "a" * 40})
            out, _ = self.go(gh, event(branch="claude/life41-edge-fixtures", merged=merged), deletes=True)
            self.assertEqual((out["delete"], out["why"], gh.deleted), (False, "KEEP_LIST", []))

    def test_a_fork_pr_touches_nothing(self):
        gh = GitHub(heads={"claude/x": "a" * 40})
        out, record = self.go(gh, event(fork=True), deletes=True)
        self.assertEqual((out["archive"], record, gh.calls), ("SKIPPED", [], []))

    def test_nothing_is_deleted_unless_the_archive_was_read_back(self):
        gh = GitHub(heads={"claude/x": "a" * 40})
        with self.assertRaises(branches.BranchError):
            self.go(gh, pushed=branches.BranchError("BRANCHES_ARCHIVE_NOT_READ_BACK"))
        self.assertEqual(gh.deleted, [])

    def test_a_pr_closed_unmerged_is_archived_then_deleted_when_the_switch_is_on(self):
        gh = GitHub(heads={"claude/x": "a" * 40})
        out, record = self.go(gh, event(merged=False), deletes=True)
        self.assertEqual(record, [("a" * 40, "archive/claude/x-pr401")])
        self.assertEqual((out["archive"], out["delete"], out["why"]), ("ARCHIVED", True, "CLOSED_AND_UNCHANGED"))
        self.assertEqual(gh.deleted, [f"/repos/{REPO}/git/refs/heads/claude%2Fx"])

    def test_a_pr_closed_unmerged_with_the_switch_off_writes_nothing(self):
        gh = GitHub(heads={"claude/x": "a" * 40})
        out, record = self.go(gh, event(merged=False), deletes=False)
        self.assertEqual((record, gh.deleted, gh.created), ([], [], []))
        self.assertEqual((out["archive"], out["delete"], out["why"]), ("DRY_RUN", False, "DELETES_OFF_WOULD_DELETE"))

    def test_a_closed_unmerged_branch_with_newer_commits_or_another_open_pr_is_kept(self):
        gh = GitHub(heads={"claude/x": "b" * 40})
        out, _ = self.go(gh, event(merged=False), deletes=True)
        self.assertEqual((out["delete"], out["why"], gh.deleted), (False, "NEWER_COMMITS_AFTER_CLOSE", []))
        gh = GitHub(heads={"claude/x": "a" * 40}, open_prs=[{"number": 405, "head": {"ref": "claude/x"}}])
        out, _ = self.go(gh, event(merged=False), deletes=True)
        self.assertEqual((out["delete"], out["why"], gh.deleted), (False, "ANOTHER_OPEN_PR_USES_IT", []))

    def test_a_closed_unmerged_branch_whose_archive_fails_deletes_nothing(self):
        gh = GitHub(heads={"claude/x": "a" * 40})
        with self.assertRaises(branches.BranchError):
            self.go(
                gh, event(merged=False), deletes=True, pushed=branches.BranchError("BRANCHES_ARCHIVE_NOT_READ_BACK")
            )
        self.assertEqual(gh.deleted, [])


class PushTag(unittest.TestCase):
    """The real tag path against the stand-in API: the tag is in this repository, never another one."""

    def push(self, gh, sha="a" * 40, tag="archive/claude/x-pr1"):
        return branches.push_tag(REPO, "gh-token", sha, tag, opener=gh)

    def test_the_tag_is_created_in_this_repository_and_read_back_and_a_second_run_is_a_no_op(self):
        gh = GitHub()
        self.assertEqual(self.push(gh), "ARCHIVED")
        self.assertEqual(gh.created, [("refs/tags/archive/claude/x-pr1", "a" * 40)])
        self.assertEqual(self.push(gh), "ALREADY_ARCHIVED")
        self.assertEqual(len(gh.created), 1)
        self.assertTrue(all(path.startswith(f"/repos/{REPO}/") for _, path in gh.calls))

    def test_an_existing_tag_on_another_commit_is_never_overwritten(self):
        gh = GitHub(tags={"archive/claude/x-pr1": "f" * 40})
        with self.assertRaises(branches.BranchError) as ctx:
            self.push(gh)
        self.assertEqual((str(ctx.exception), gh.created), ("BRANCHES_ARCHIVE_TAG_TAKEN", []))

    def test_a_tag_that_cannot_be_read_back_is_a_fixed_code(self):
        gh = GitHub(drop_tag_writes=True)
        with self.assertRaises(branches.BranchError) as ctx:
            self.push(gh)
        self.assertEqual(str(ctx.exception), "BRANCHES_ARCHIVE_NOT_READ_BACK")

    def test_the_tag_names_are_archive_slash_branch_with_pr_or_nopr(self):
        self.assertEqual(branches.tag_name("docs/old", 7), "archive/docs/old-pr7")
        self.assertEqual(branches.tag_name("docs/old"), "archive/docs/old-nopr")


class Sweep(unittest.TestCase):
    """The weekly sweep: no open PR and idle 14+ days, archived and read back before any delete."""

    def branch(self, name, days, sha=None):
        return {"name": name, "sha": sha or "c" * 40, "last": NOW - timedelta(days=days)}

    def run_sweep(self, gh, rows, prs=(), live=True, push=None):
        return branches.sweep(rows, list(prs), NOW, REPO, "gh-token", live, opener=gh, push=push)

    def gh(self, *names, **kw):
        return GitHub(heads={n: "c" * 40 for n in names}, **kw)

    def test_a_no_pr_branch_idle_14_days_is_tagged_nopr_read_back_and_deleted(self):
        gh = self.gh("docs/old")
        counts, names = self.run_sweep(gh, [self.branch("docs/old", 14)])
        self.assertEqual((names, counts["deleted"]), (["docs/old"], 1))
        self.assertEqual(gh.created, [("refs/tags/archive/docs/old-nopr", "c" * 40)])
        self.assertEqual(gh.deleted, [f"/repos/{REPO}/git/refs/heads/docs%2Fold"])

    def test_branches_idle_7_to_13_days_are_report_only(self):
        gh = self.gh("docs/new")
        counts, names = self.run_sweep(gh, [self.branch("docs/new", 13), self.branch("docs/new", 7)])
        self.assertEqual((counts["candidates"], names, gh.calls), (0, [], []))

    def test_a_branch_with_an_open_pr_is_never_deleted_however_idle(self):
        gh = self.gh("docs/old")
        counts, _ = self.run_sweep(gh, [self.branch("docs/old", 90)], prs=[{"number": 5, "branch": "docs/old"}])
        self.assertEqual((counts["candidates"], gh.deleted, gh.created), (0, [], []))

    def test_main_and_the_keep_list_are_never_deleted(self):
        gh = self.gh("main", "claude/life41-edge-fixtures")
        rows = [self.branch("main", 90), self.branch("claude/life41-edge-fixtures", 90)]
        counts, _ = self.run_sweep(gh, rows)
        self.assertEqual((counts["candidates"], gh.deleted, gh.created), (0, [], []))

    def test_a_dry_run_lists_what_it_would_delete_and_writes_nothing(self):
        gh = self.gh("docs/old")
        counts, names = self.run_sweep(gh, [self.branch("docs/old", 30)], live=False)
        self.assertEqual((counts["candidates"], counts["deleted"], names), (1, 0, ["docs/old"]))
        self.assertEqual((gh.calls, gh.deleted, gh.created), ([], [], []))
        self.assertIn("WOULD be archived and deleted", branches.sweep_lines(counts, names, False)[0])
        self.assertEqual(branches.sweep_lines(counts, names, False)[1:], ["would delete · docs/old"])

    def test_a_failed_read_back_deletes_nothing_and_the_rest_carry_on(self):
        gh = self.gh("docs/a", "docs/b", drop_tag_writes=True)
        counts, _ = self.run_sweep(gh, [self.branch("docs/a", 20), self.branch("docs/b", 20)])
        self.assertEqual((counts["failed"], counts["deleted"], gh.deleted), (2, 0, []))

    def test_a_branch_that_moved_or_gained_a_pr_since_the_listing_is_kept(self):
        gh = GitHub(heads={"docs/moved": "d" * 40, "docs/pr": "c" * 40})
        gh.open_prs = [{"number": 9, "head": {"ref": "docs/pr"}}]
        rows = [self.branch("docs/moved", 20), self.branch("docs/pr", 20)]
        counts, _ = self.run_sweep(gh, rows)
        self.assertEqual((counts["kept"], counts["deleted"], gh.deleted), (2, 0, []))

    def test_a_switch_file_that_says_on_turns_deletes_on_and_anything_else_is_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "switch"
            self.assertFalse(branches.deletes_on(path))
            for text, expected in (("on\n", True), ("ON", True), ("off\n", False), ("", False), ("true", False)):
                path.write_text(text)
                self.assertEqual(branches.deletes_on(path), expected, text)
        self.assertEqual((Path(branches.SWITCH_FILE).read_text().strip(), branches.deletes_on()), ("off", False))

    def test_the_report_counts_only_and_writes_nothing_while_the_switch_is_off(self):
        gh = self.gh("docs/old")
        with mock.patch.object(branches, "read_repo", lambda *a, **k: ([self.branch("docs/old", 30)], [])):
            out = branches.report(False, dict(ENV), now=NOW, opener=gh, deletes=False)
        self.assertEqual(
            (out["deletes"], out["candidates"], out["deleted"], gh.deleted, gh.created), ("OFF", 1, 0, [], [])
        )
        self.assertNotIn("docs/old", json.dumps(out))


class Report(unittest.TestCase):
    BRANCHES = [
        {"name": "main", "last": NOW},
        {"name": "docs/old", "last": NOW - timedelta(days=20)},
        {"name": "claude/fresh", "last": NOW - timedelta(days=2)},
        {"name": "claude/life41-edge-fixtures", "last": NOW - timedelta(days=8)},
    ]

    def test_stale_branches_are_listed_oldest_first_with_their_open_pr(self):
        rows = branches.stale(self.BRANCHES, [{"number": 191, "branch": "docs/old"}], NOW)
        self.assertEqual(
            [(r["branch"], r["pr"], r["idle_days"], r["stale"], r["kept"]) for r in rows],
            [
                ("docs/old", 191, 20, True, False),
                ("claude/life41-edge-fixtures", None, 8, True, True),
                ("claude/fresh", None, 2, False, False),
            ],
        )
        text = branches.lines(rows, NOW)
        self.assertEqual(
            text[0], "Mon, 10/26 · 8:37 AM CT · 3 branches besides main · 1 with an open PR · 2 idle 7+ days"
        )
        self.assertEqual(
            text[1:],
            [
                "PR #191 · docs/old · idle 20 days",
                "no PR · claude/life41-edge-fixtures · idle 8 days · kept on purpose",
            ],
        )

    def test_nothing_stale_says_so(self):
        self.assertEqual(
            branches.lines(branches.stale([{"name": "main", "last": NOW}], [], NOW), NOW)[1:],
            ["Nothing stale. Merged branches are archived and removed automatically."],
        )


class ReviewPage(AgendaRegions):
    """The Weekly LIFE OS Review page: Jim's protocol callout and a database, then (after the first live run) V7's one
    owned callout.
    """

    def __init__(self):
        super().__init__()
        self.children = {
            "review-page": [
                {"id": "protocol", "type": "callout", "has_children": True, "callout": {}},
                {"id": "reviews-db", "type": "child_database", "has_children": False, "child_database": {}},
            ],
            "protocol": [self._paragraph("Purpose: the weekly control loop")],
        }
        self.metas = {"protocol": self.children["review-page"][0]}

    def call(self, method, path, body=None):
        clean = path.split("?", 1)[0]
        if method == "PATCH" and clean == "/blocks/review-page/children":
            self.log.append(("INSTALL", path))
            spec = body["children"][0]
            callout = {"id": "repo-callout", "type": "callout", "has_children": True, "callout": {"rich_text": []}}
            heading = spec["callout"]["children"][0]
            self.children["review-page"].append(callout)
            self.metas["repo-callout"] = callout
            self.children["repo-callout"] = [
                self._text_block(
                    "repo-heading", heading["type"], heading[heading["type"]]["rich_text"][0]["text"]["content"]
                )
            ]
            return {"results": [callout]}
        return super().call(method, path, body)


class OwnedCallout(unittest.TestCase):
    def test_the_first_live_run_installs_the_callout_at_the_end_and_writes_it_and_jims_content_is_untouched(self):
        page = ReviewPage()
        before = [dict(b) for b in page.children["review-page"]], [dict(b) for b in page.children["protocol"]]
        self.assertEqual(
            branches.write_region(page, "review-page", ["status line", "PR #191 · docs/old · idle 20 days"], live=True),
            "WRITTEN",
        )
        self.assertEqual([b["id"] for b in page.children["review-page"]], ["protocol", "reviews-db", "repo-callout"])
        self.assertEqual(
            [report_region.plain(b) for b in page.children["repo-callout"]],
            [router.REPO_REGION, "status line", "PR #191 · docs/old · idle 20 days"],
        )
        self.assertEqual((page.children["review-page"][:2], page.children["protocol"]), before)
        branches.write_region(
            page, "review-page", ["new status"], live=True
        )  # the next week rewrites the same callout in place
        self.assertEqual(
            [report_region.plain(b) for b in page.children["repo-callout"]], [router.REPO_REGION, "new status"]
        )
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
    """every other test used a stand-in."""

    def test_the_report_builds_the_real_notion_client_from_the_cards_token_alone(self):
        from lifeos.platform.notion_client import Client, NotionError

        client = branches.writer({"NOTION_JIRA_TOKEN": "secret-token"})
        self.assertIsInstance(client, Client)
        self.assertEqual(client.token, "secret-token")
        with self.assertRaises(NotionError):
            branches.writer({})
