"""The run cleanup keeps what must stay, deletes oldest first, and stops where the rules say"""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import run_cleanup as rc  # noqa: E402

NOW = datetime(2026, 12, 13, 7, 17, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=30)
RESET = int(datetime(2026, 12, 13, 8, 0, tzinfo=timezone.utc).timestamp())


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def make(rid, age_hours_past_cutoff, status="completed"):
    """A run created that many hours before the cutoff (negative = after it)."""
    return {"id": rid, "status": status, "created_at": stamp(CUTOFF - timedelta(hours=age_hours_past_cutoff))}


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status, self.headers = status, headers or {}
        self._body = json.dumps(body).encode() if body is not None else b""

    def read(self):
        return self._body


class Github:
    """A stand-in GitHub: the listing pages `runs` newest first; every call is recorded; `script` overrides a DELETE by run id."""

    def __init__(self, runs, remaining=900, script=None, listing_headers=None):
        self.runs, self.remaining, self.script = list(runs), remaining, script or {}
        self.listing_headers = listing_headers or {}
        self.calls = []

    def headers(self, extra=None):
        return {"x-ratelimit-remaining": str(self.remaining), "x-ratelimit-reset": str(RESET), **(extra or {})}

    def __call__(self, req, timeout=None):
        method, url = req.get_method(), req.full_url
        self.calls.append((method, url))
        if method == "GET":
            query = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
            page, size = int(query["page"]), int(query["per_page"])
            ordered = sorted(self.runs, key=lambda r: r["created_at"], reverse=True)
            self.remaining -= 1
            return Resp(
                200,
                {"total_count": len(ordered), "workflow_runs": ordered[(page - 1) * size : page * size]},
                self.headers(self.listing_headers),
            )
        rid = int(url.rsplit("/", 1)[1])
        self.remaining -= 1
        step = self.script.get(rid)
        if step is None:
            return Resp(204, None, self.headers())
        if isinstance(step, tuple):
            raise urllib.error.HTTPError(url, step[0], "x", self.headers(step[1]), None)
        raise step


def go(runs, live=True, protected=(), **kw):
    sleeps, github = [], Github(runs, **kw)
    receipt = rc.run(rc.Client("t", "o/r", github), NOW, live, set(protected), sleep=sleeps.append)
    return receipt, github, sleeps


def deleted(github):
    return [int(u.rsplit("/", 1)[1]) for m, u in github.calls if m == "DELETE"]


class Cutoff(unittest.TestCase):
    def test_strictly_older_than_thirty_days(self):
        runs = [make(1, 1), make(2, 0), make(3, -1)]  # 1 h before the cutoff, exactly at it, 1 h after it
        keep, left = rc.validate(runs, CUTOFF, set())
        self.assertEqual([r["id"] for r in keep], [1])
        self.assertEqual(left["newer"], 2)

    def test_cutoff_is_thirty_days_before_the_run(self):
        self.assertEqual(rc.cutoff_for(NOW), NOW - timedelta(days=30))

    def test_listing_asks_only_for_runs_before_the_cutoff(self):
        _, github, _ = go([make(1, 5)], live=False)
        self.assertIn("created=%3C2026-11-13T07:17:00Z", github.calls[0][1])


class Candidates(unittest.TestCase):
    def test_protected_ids_never_deleted(self):
        receipt, github, _ = go([make(10, 9), make(11, 8)], protected={10})
        self.assertEqual(deleted(github), [11])
        self.assertEqual(receipt["left_out"]["protected"], 1)

    def test_queued_and_in_progress_never_deleted(self):
        runs = [make(1, 9, "queued"), make(2, 8, "in_progress"), make(3, 7, "completed"), make(4, 6, "waiting")]
        receipt, github, _ = go(runs)
        self.assertEqual(deleted(github), [3])
        self.assertEqual(receipt["left_out"]["not_completed"], 3)

    def test_newer_runs_in_the_listing_are_never_candidates(self):
        receipt, github, _ = go([make(1, -48), make(2, 5)])
        self.assertEqual(deleted(github), [2])
        self.assertEqual(receipt["left_out"]["newer"], 1)

    def test_oldest_first_one_second_apart(self):
        receipt, github, sleeps = go([make(1, 5), make(2, 50), make(3, 20)])
        self.assertEqual(deleted(github), [2, 3, 1])
        self.assertEqual(sleeps, [1.0, 1.0])
        self.assertEqual(receipt["last_run_id"], 1)
        self.assertEqual(receipt["deleted"], 3)
        self.assertEqual(receipt["stop_reason"], "done")

    def test_duplicates_counted_once(self):
        keep, _ = rc.validate([make(1, 5), make(1, 5)], CUTOFF, set())
        self.assertEqual(len(keep), 1)

    def test_listing_pages_until_a_short_page(self):
        runs = [make(i, 5 + i) for i in range(1, 251)]
        receipt, github, _ = go(runs, live=False, remaining=5000)
        self.assertEqual(receipt["candidates"], 250)
        self.assertEqual(sum(1 for m, _ in github.calls if m == "GET"), 3)
        self.assertFalse(receipt["truncated"])

    def test_inventory_capped_at_one_thousand_is_flagged(self):
        runs = [make(i, 5 + i) for i in range(1, 1101)]
        receipt, github, _ = go(runs, live=False, remaining=5000)
        self.assertEqual(sum(1 for m, _ in github.calls if m == "GET"), 10)
        self.assertEqual(receipt["candidates"], 1000)
        self.assertTrue(receipt["truncated"])


class DryRun(unittest.TestCase):
    def test_default_deletes_nothing(self):
        receipt, github, _ = go([make(1, 5), make(2, 6)], live=False)
        self.assertEqual(deleted(github), [])
        self.assertEqual(receipt["candidates"], 2)
        self.assertEqual(receipt["deleted"], 0)
        self.assertEqual(receipt["stop_reason"], "dry_run")
        self.assertEqual(receipt["mode"], "dry_run")

    def test_main_without_live_flag_is_a_dry_run(self):
        github, out = Github([make(1, 5)]), io.StringIO()
        code = rc.main([], {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}, github, NOW, lambda s: None, out)
        self.assertEqual(code, 0)
        self.assertEqual(deleted(github), [])

    def test_main_live_flag_deletes(self):
        github, out = Github([make(1, 5)]), io.StringIO()
        code = rc.main(["--live"], {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}, github, NOW, lambda s: None, out)
        self.assertEqual(code, 0)
        self.assertEqual(deleted(github), [1])

    def test_main_without_token_stops(self):
        self.assertEqual(rc.main([], {}, None, NOW, lambda s: None, io.StringIO()), 2)


class Stops(unittest.TestCase):
    def test_stops_below_the_rate_floor_and_resumes_from_a_fresh_inventory(self):
        runs = [make(i, 100 - i) for i in range(1, 8)]
        receipt, github, _ = go(runs, remaining=303)  # the listing leaves 302; each delete costs 1
        self.assertEqual(receipt["stop_reason"], "rate_low")
        self.assertEqual(receipt["deleted"], 3)  # 302 -> 301 -> 300 -> 299: the fourth is refused
        first = deleted(github)
        left = [r for r in runs if r["id"] not in first]  # the next run lists only what remains
        again, github2, _ = go(left)
        self.assertEqual(again["stop_reason"], "done")
        self.assertEqual(sorted(first + deleted(github2)), list(range(1, 8)))

    def test_call_cap(self):
        runs = [make(i, 5 + i) for i in range(1, 700)]
        receipt, github, _ = go(runs, remaining=5000)
        self.assertEqual(receipt["stop_reason"], "call_cap")
        self.assertEqual(receipt["calls"], rc.MAX_CALLS)
        self.assertEqual(len(deleted(github)), rc.MAX_CALLS - 7)  # 7 listing pages

    def test_403_stops_at_once_no_retry(self):
        receipt, github, _ = go([make(1, 9), make(2, 8), make(3, 7)], script={1: (403, {})})
        self.assertEqual(receipt["stop_reason"], "forbidden_403")
        self.assertEqual(deleted(github), [1])
        self.assertEqual(receipt["deleted"], 0)

    def test_429_stops_at_once(self):
        receipt, github, _ = go([make(1, 9), make(2, 8)], script={1: (429, {})})
        self.assertEqual(receipt["stop_reason"], "rate_limited_429")
        self.assertEqual(deleted(github), [1])

    def test_retry_after_stops_at_once(self):
        receipt, github, _ = go([make(1, 9), make(2, 8)], script={1: (500, {"retry-after": "60"})})
        self.assertEqual(receipt["stop_reason"], "retry_after")
        self.assertEqual(deleted(github), [1])

    def test_retry_after_on_the_listing_stops_before_any_delete(self):
        receipt, github, _ = go([make(1, 9)], listing_headers={"retry-after": "30"})
        self.assertEqual(receipt["stop_reason"], "retry_after")
        self.assertEqual(deleted(github), [])

    def test_partial_stop_keeps_the_count_and_last_id(self):
        receipt, github, _ = go([make(1, 9), make(2, 8), make(3, 7)], script={3: (403, {})})
        self.assertEqual((receipt["deleted"], receipt["last_run_id"], receipt["stop_reason"]), (2, 2, "forbidden_403"))

    def test_other_http_error_and_network_error_stop(self):
        receipt, _, _ = go([make(1, 9)], script={1: (500, {})})
        self.assertEqual(receipt["stop_reason"], "http_500")
        receipt, _, _ = go([make(1, 9)], script={1: urllib.error.URLError("down")})
        self.assertEqual(receipt["stop_reason"], "network_error")

    def test_a_run_already_gone_is_counted_and_the_run_continues(self):
        receipt, github, _ = go([make(1, 9), make(2, 8)], script={1: (404, {})})
        self.assertEqual((receipt["already_gone"], receipt["deleted"]), (1, 1))
        self.assertEqual(receipt["stop_reason"], "done")

    def test_exit_code_fails_only_on_a_real_stop(self):
        out = io.StringIO()
        env = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}
        self.assertEqual(
            rc.main(["--live"], env, Github([make(1, 9)], script={1: (403, {})}), NOW, lambda s: None, out), 1
        )


class Receipt(unittest.TestCase):
    def test_receipt_fields_in_central_time(self):
        receipt, _, _ = go([make(1, 9)], live=False)
        self.assertEqual(receipt["cutoff"], "Nov 13 2026, 1:17 AM CT")
        self.assertEqual(receipt["rate_reset"], "Dec 13 2026, 2:00 AM CT")
        self.assertEqual(receipt["last_run_id"], None)
        text = rc.summary(receipt)
        self.assertNotIn("UTC", text)
        for word in ("Cutoff", "Candidates", "Deleted", "last run ID", "rate remaining", "Stop reason"):
            self.assertIn(word, text)

    def test_summary_file_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.md"
            env = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r", "GITHUB_STEP_SUMMARY": str(path)}
            rc.main([], env, Github([make(1, 9)]), NOW, lambda s: None, io.StringIO())
            self.assertIn("Run cleanup receipt (dry_run)", path.read_text())


class Protected(unittest.TestCase):
    def test_the_repo_file_lists_the_three_evidence_runs(self):
        self.assertEqual(rc.load_protected(), {45052884549, 45057467411, 45058265030})

    def test_comments_and_blanks_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p"
            path.write_text("# c\n\n12 # note\nx\n")
            self.assertEqual(rc.load_protected(path), {12})


if __name__ == "__main__":
    unittest.main()
