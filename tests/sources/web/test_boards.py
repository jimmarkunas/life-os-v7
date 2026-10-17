"""D202: the employer-board pass. The registry built from the seed and extras, the seed builder, and one bounded run over an in-memory repo."""
from datetime import datetime, timedelta
import json
import tempfile
import unittest
from pathlib import Path

from lifeos.platform import limits
from lifeos.sources.web import boards, boards_seed, lister, registry, run
from tests.sources.web.test_web import MemRepo

NOW = datetime(2026, 10, 17, 12, 0)


def seed_file(seed, extras=(), skip=()):
    path = Path(tempfile.mkdtemp()) / "boards.json"
    path.write_text(json.dumps({"seed": seed, "extras": list(extras), "skip": list(skip)}))
    return path


def job(i, title, where="Remote - US", company=None):
    return {"id": str(i), "title": title, "location": where, "url": f"https://boards.greenhouse.io/x/jobs/{i}", "posted": None, "content": None, "company": company}


class Registry(unittest.TestCase):
    def test_the_committed_list_has_the_named_boards_and_reads_no_curated_board_twice(self):
        rows = registry.boards()
        by = {(r["kind"], r["slug"]): r for r in rows}
        self.assertEqual(by[("ashby", "surge-ai")]["company"], "Surge AI")
        self.assertEqual(by[("greenhouse", "keepersecurity")]["company"], "Keeper Security")     # the extra's name wins over the seed's slug
        self.assertNotIn(("greenhouse", "affirm"), by)                                          # a priority board (us_remote.json) is read by the web pass only
        self.assertGreater(len(rows), 10_000)
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        self.assertTrue(all(len("web:" + r["id"]) <= 40 for r in rows))

    def test_rows_are_ready_employer_boards_on_the_slow_refresh(self):
        rows = registry.boards(seed_file({"lever": ["acme-co"]}))
        self.assertEqual(rows, [{"id": "lv-acme-co", "company": "Acme Co", "tier": "employer", "kind": "lever", "status": "ready", "enabled": True,
                                 "slug": "acme-co", "refresh_hours": limits.BOARDS_REFRESH_HOURS, "hot_hours": limits.BOARDS_HOT_HOURS, "quiet": True}])

    def test_skip_and_unknown_kinds_are_left_out_and_duplicates_collapse(self):
        rows = registry.boards(seed_file({"greenhouse": ["a", "A", "b"]}, extras=[{"kind": "workday", "slug": "w", "company": "W"}], skip=["greenhouse:b"]))
        self.assertEqual([r["id"] for r in rows], ["gh-a"])

    def test_an_id_too_long_for_the_source_column_is_refused(self):
        with self.assertRaises(registry.RegistryError):
            registry.boards(seed_file({"ashby": ["x" * 40]}))


class Keep(unittest.TestCase):
    """A seed board is an unknown employer: a job is stored only when its title and place show the target."""

    def test_keeps_remote_us_target_family_titles_only(self):
        cases = [("Senior Technical Program Manager", "Remote, US", True), ("Program Manager", "Remote", True), ("TPM", "United States", True),
                 ("Implementation Project Manager", "USA, Remote", True), ("Senior Project Manager", "Austin/Remote within the US", True),
                 ("Project Manager", "Remote - Colombia", False), ("Technical Program Manager", "Austin, Texas, United States", False),
                 ("Program Manager", "Remote - EMEA", False), ("Program Manager", "London, UK", False), ("Staff Engineer", "Remote, US", False),
                 ("Program Manager", "Hybrid - Remote, US", False), ("Program Manager", "", False)]
        for title, where, want in cases:
            with self.subTest(title=title, where=where):
                self.assertEqual(boards.keep({"title": title, "location": where, "posted": None}), want)


class SeedBuilder(unittest.TestCase):
    def test_keeps_extras_and_skip_and_only_safe_short_slugs(self):
        slugs = {"generated_at": "2026-09-09", "ats": {"greenhouse": ["b", "a", "bad/slug", "x" * 34, 7], "ashby": ["z"], "workday": ["w"]}}
        out = boards_seed.build(slugs, {"about": "x", "extras": [{"kind": "ashby", "slug": "surge-ai", "company": "Surge AI"}], "skip": ["lever:q"]})
        self.assertEqual(out["seed"], {"greenhouse": ["a", "b"], "ashby": ["z"], "lever": []})
        self.assertEqual((out["seed_generated_at"], out["extras"][0]["slug"], out["skip"]), ("2026-09-09", "surge-ai", ["lever:q"]))


SOURCES = [{"id": f"gh-b{i}", "company": f"B{i}", "tier": "employer", "kind": "greenhouse", "status": "ready", "enabled": True, "slug": f"b{i}",
            "refresh_hours": 24, "hot_hours": 6, "quiet": True} for i in range(3)]


class OneRun(unittest.TestCase):
    def test_only_possible_targets_are_stored_and_admitted_with_the_boards_own_name(self):
        listings = {"b0": lister.Listing(lister.COMPLETE, [job(1, "Senior Technical Program Manager", company="Board Zero Inc"), job(2, "Staff Software Engineer"),
                                                          job(3, "Program Manager", "London, UK")]),
                    "b1": lister.Listing(lister.COMPLETE, [job(4, "Account Executive")]), "b2": lister.Listing(lister.COMPLETE, [])}
        repo = MemRepo()
        got = boards.run(600, True, NOW, lambda s: listings[s["slug"]], repo, SOURCES)
        self.assertEqual((got["complete"], got["admit"], got["not_kept"], got["pass"]), (3, 1, 3, "employer boards"))
        self.assertEqual(repo.items_by["gh-b0"], {"1": repo.items_by["gh-b0"]["1"]})       # the engineer and the London role were never stored
        self.assertEqual(repo.items_by.get("gh-b1", {}), {})
        admitted = [o for o, _ in repo.commits if o.admit][0].admit[0]
        self.assertEqual(admitted["company"], "Board Zero Inc")

    def test_a_board_with_a_target_is_due_sooner_than_one_without(self):
        listings = {"b0": lister.Listing(lister.COMPLETE, [job(1, "Program Manager")]), "b1": lister.Listing(lister.COMPLETE, [job(2, "Engineer")]),
                    "b2": lister.Listing(lister.COMPLETE, [])}
        repo = MemRepo()
        boards.run(600, True, NOW, lambda s: listings[s["slug"]], repo, SOURCES)
        hot, cold = repo.state["gh-b0"]["due_at"], repo.state["gh-b1"]["due_at"]
        self.assertTrue(NOW + timedelta(hours=6) <= hot < NOW + timedelta(hours=7))
        self.assertTrue(NOW + timedelta(hours=24) <= cold < NOW + timedelta(hours=25))

    def test_three_429s_from_one_ats_stop_reading_it_this_run(self):
        calls = []

        def lister_fn(source):
            calls.append(source["slug"])
            return lister.Listing(lister.FAILED, reason="rate_limited_429")
        many = [{**SOURCES[0], "id": f"gh-c{i}", "slug": f"c{i}"} for i in range(6)]
        guard = boards.StopOnRateLimit(lister_fn)
        got = [guard(s).reason for s in many]
        self.assertEqual(len(calls), 3)
        self.assertEqual(got, ["rate_limited_429"] * 3 + ["skipped_after_rate_limit_429"] * 3)
        self.assertEqual(guard({**SOURCES[0], "kind": "ashby"}).reason, "rate_limited_429")       # another ATS is still read

    def test_the_run_is_capped_by_the_boards_limit_not_the_priority_one(self):
        many = [{**SOURCES[0], "id": f"gh-d{i:02d}", "slug": f"d{i}"} for i in range(limits.WEB_BOARDS_PER_RUN + 5)]
        got = boards.run(600, False, NOW, lambda s: lister.Listing(lister.COMPLETE, []), MemRepo(), many)
        self.assertEqual(got["due"], len(many))


class QuietRunRow(unittest.TestCase):
    """SqlRepo.commit: a seed board read with nothing new writes its v7_sources state but no v7_source_runs row."""

    class Cur:
        def __init__(self, log):
            self.log = log

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=None):
            self.log.append(sql.split()[2] if sql.startswith("INSERT") else sql.split()[0])

        def executemany(self, sql, rows):
            self.log.append(sql.split()[0])

    class Conn:
        def __init__(self, log):
            self.log = log

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return QuietRunRow.Cur(self.log)

    def commit(self, source, counts):
        from unittest import mock
        log = []
        o = run.Outcome(source, NOW, "COMPLETE", frontier="f", counts=counts)
        o.due_at = NOW
        with mock.patch.object(run.store, "connect", return_value=self.Conn(log)):
            run.SqlRepo().commit(o, 0)
        return [t for t in log if t.startswith("v7_")]

    def test_quiet_board_writes_state_only_and_a_priority_board_still_logs(self):
        nothing = {"added": 0, "changed": 0, "removed": 0, "admit": 0}
        self.assertEqual(self.commit(SOURCES[0], nothing), ["v7_sources"])
        self.assertEqual(self.commit(SOURCES[0], {**nothing, "added": 1}), ["v7_sources", "v7_source_runs"])
        self.assertEqual(self.commit({"id": "affirm", "kind": "greenhouse", "company": "Affirm"}, nothing), ["v7_sources", "v7_source_runs"])
