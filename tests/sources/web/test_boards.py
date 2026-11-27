"""the employer-board pass."""

from datetime import datetime, timedelta
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from lifeos.jobs import lanes
from lifeos.platform import limits
from lifeos.sources.web import boards, lister, registry, run
from tests.sources.web.test_web import MemRepo

NOW = datetime(2026, 10, 17, 12, 0)


def seed_file(seed, extras=(), skip=()):
    path = Path(tempfile.mkdtemp()) / "boards.json"
    path.write_text(json.dumps({"seed": seed, "extras": list(extras), "skip": list(skip)}))
    return path


def job(i, title, where="Remote - US", company=None):
    return {
        "id": str(i),
        "title": title,
        "location": where,
        "url": f"https://boards.greenhouse.io/x/jobs/{i}",
        "posted": None,
        "content": None,
        "company": company,
    }


class Registry(unittest.TestCase):
    def test_the_committed_list_has_the_named_boards_and_reads_no_curated_board_twice(self):
        rows = registry.boards()
        by = {(r["kind"], r["slug"]): r for r in rows}
        self.assertEqual(by[("ashby", "surge-ai")]["company"], "Surge AI")
        self.assertEqual(
            by[("greenhouse", "keepersecurity")]["company"], "Keeper Security"
        )  # the extra's name wins over the seed's slug
        self.assertNotIn(("greenhouse", "affirm"), by)
        self.assertGreater(len(rows), 10_000)
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        self.assertTrue(all(len("web:" + r["id"]) <= 40 for r in rows))

    def test_rows_are_ready_employer_boards_on_the_slow_refresh(self):
        rows = registry.boards(seed_file({"lever": ["acme-co"]}))
        self.assertEqual(
            rows,
            [
                {
                    "id": "lv-acme-co",
                    "company": "Acme Co",
                    "tier": "employer",
                    "kind": "lever",
                    "status": "ready",
                    "enabled": True,
                    "slug": "acme-co",
                    "refresh_hours": limits.BOARDS_REFRESH_HOURS,
                    "hot_hours": limits.BOARDS_HOT_HOURS,
                    "quiet": True,
                }
            ],
        )

    def test_skip_and_unknown_kinds_are_left_out_and_duplicates_collapse(self):
        rows = registry.boards(
            seed_file(
                {"greenhouse": ["a", "A", "b"]},
                extras=[{"kind": "workday", "slug": "w", "company": "W"}],
                skip=["greenhouse:b"],
            )
        )
        self.assertEqual([r["id"] for r in rows], ["gh-a"])

    def test_an_id_too_long_for_the_source_column_is_refused(self):
        with self.assertRaises(registry.RegistryError):
            registry.boards(seed_file({"ashby": ["x" * 40]}))


class Screen(unittest.TestCase):
    """A seed board is an unknown employer: a job is stored only when its title is in the family and remote is SHOWN
    (place, title or an explicit.
    """

    def test_each_job_gets_its_reason(self):
        remote_jd = "About the role. This is a fully remote position open to candidates across the US."
        cases = [
            ("Senior Technical Program Manager", "Remote, US", None, "kept_remote_in_location"),
            (
                "Program Manager",
                "Remote",
                None,
                "kept_remote_in_location_place_unresolved",
            ),  # M5: remote alone names no market
            ("Senior Project Manager", "Austin/Remote within the US", None, "kept_remote_in_location"),
            ("Technical Program Manager", "New York, NY", remote_jd, "kept_remote_in_description"),
            ("Technical Program Manager", "United States", remote_jd, "kept_remote_in_description"),
            (
                "TPM",
                "United States",
                "Lead programs across teams.",
                "kept_remote_assumed",
            ),  # nothing required: presumed, labelled so
            ("Technical Program Manager", "Austin, Texas, United States", None, "kept_remote_assumed"),
            (
                "Technical Program Manager",
                "San Diego HQ",
                None,
                "kept_remote_assumed",
            ),  # M5: a US city (lanes.US_PLACES)
            (
                "Technical Program Manager",
                "RWC HQ",
                None,
                "kept_remote_assumed_place_unresolved",
            ),  # names nothing known: unresolved
            ("Program Manager", "Espoo", None, "non_us"),
            ("Program Manager", "Abu Dhabi", None, "non_us"),  # foreign places seen in the M4 sample
            ("Program Manager", "Calgary", None, "non_us"),
            ("Program Manager", "Wrocław", None, "non_us"),
            ("Program Manager", "UT - Cottonwood Heights; FL - Jacksonville", None, "kept_remote_assumed"),
            (
                "Program Manager",
                "Hybrid - Remote, US",
                None,
                "kept_remote_in_location",
            ),  # remote offered alongside hybrid
            (
                "Program Manager",
                "New York, NY",
                "This is a hybrid role: 3 days a week in our office.",
                "not_remote_hybrid",
            ),
            ("Program Manager", "Seattle, WA", "There is no onsite requirement for this role.", "kept_remote_assumed"),
            ("Program Manager", "Algiers, Algeria", None, "non_us"),
            ("Program Manager", "Remote | North America or Europe", None, "kept_remote_in_location_place_unresolved"),
            ("Program Manager", "Seattle, WA", "This role is onsite five days a week.", "not_remote_onsite"),
            ("Project Manager", "Remote - Colombia", remote_jd, "non_us"),
            ("Program Manager", "Remote - EMEA", None, "non_us"),
            ("Program Manager", "London, UK", None, "non_us"),
            ("Staff Software Engineer", "Remote, US", None, "title_other_profession_engineer"),
        ]
        for title, where, text, want in cases:
            with self.subTest(title=title, where=where):
                self.assertEqual(
                    boards.screen({"title": title, "location": where, "posted": None, "content": text}), want
                )


SOURCES = [
    {
        "id": f"gh-b{i}",
        "company": f"B{i}",
        "tier": "employer",
        "kind": "greenhouse",
        "status": "ready",
        "enabled": True,
        "slug": f"b{i}",
        "refresh_hours": 24,
        "hot_hours": 6,
        "quiet": True,
        "extra": True,
    }
    for i in range(3)
]
SEED = [{**SOURCES[0], "id": f"gh-s{i}", "slug": f"s{i}", "extra": False} for i in range(5)]


class DeadBoards(unittest.TestCase):
    """nothing is skipped by the pass itself."""

    def pass_at(self, repo, hours, reason, sources):
        def lister_fn(source):
            return (
                lister.Listing(lister.FAILED, reason=reason)
                if source["id"] == "gh-s1"
                else lister.Listing(lister.COMPLETE, [])
            )

        return boards.run(900, True, NOW + timedelta(hours=hours), lister_fn, repo, sources)

    def test_a_board_is_listed_only_after_repeated_not_found(self):
        repo, sources = MemRepo(), SEED
        listed = []
        for n in range(1, 8):
            out = self.pass_at(repo, 24 * n * 3, "not_found", sources) if n > 1 else None
            if n == 1:
                boards.backfill(
                    450,
                    True,
                    NOW,
                    lambda s: (
                        lister.Listing(lister.FAILED, reason="not_found")
                        if s["id"] == "gh-s1"
                        else lister.Listing(lister.COMPLETE, [])
                    ),
                    repo,
                    sources,
                    {"BOARDS_BACKFILL": "on"},
                )
                continue
            listed.append((repo.state["gh-s1"]["failures"], out["dead_ids"]))
        self.assertEqual([(f, ids) for f, ids in listed if ids], [(limits.BOARDS_DEAD_AFTER, ["gh-s1"])])
        self.assertTrue(
            all(ids == [] for f, ids in listed if f < limits.BOARDS_DEAD_AFTER)
        )  # not before BOARDS_DEAD_AFTER failures in a row
        self.assertEqual(sum(1 for _, ids in listed if ids), 1)  # listed on the fifth failure

    def test_other_failures_and_healthy_boards_are_never_listed(self):
        repo = MemRepo()
        boards.backfill(
            450, True, NOW, lambda s: lister.Listing(lister.COMPLETE, []), repo, SEED, {"BOARDS_BACKFILL": "on"}
        )
        repo.state["gh-s1"]["failures"] = 9
        out = self.pass_at(repo, 24 * 3, "http_500", SEED)  # a long run of 500s, not not_found
        self.assertEqual((out["failed"], out["dead_ids"]), (1, []))

    def test_the_list_is_capped_and_the_tick_no_longer_reads_boards(self):
        from lifeos.jobs import tick

        self.assertEqual(limits.BOARDS_DEAD_IDS_MAX, 600)
        self.assertNotIn("boards", [s.name for s in tick.PLAN])  # the four board windows (boards.yml) own the stage
        self.assertIn("boards-backfill", [s.name for s in tick.PLAN])


class StoppedPass(unittest.TestCase):
    def test_boards_read_before_the_stop_are_committed_and_the_rest_stay_due(self):
        from lifeos.jobs import tick

        repo = MemRepo()

        def stop_at_third(pool, fn, items):
            for n, item in enumerate(items):
                if n == 2:
                    raise tick.StageTimeout()
                yield fn(item)

        with mock.patch.object(run.ThreadPoolExecutor, "map", stop_at_third):
            with self.assertRaises(tick.StageTimeout):
                boards.backfill(
                    450, True, NOW, lambda s: lister.Listing(lister.COMPLETE, []), repo, SEED, {"BOARDS_BACKFILL": "on"}
                )
        self.assertEqual(sorted(repo.state), ["gh-s0", "gh-s1"])  # committed as they arrived, not lost with the stop

    def test_a_window_has_time_for_its_cap_at_a_slow_boards_pace(self):
        from lifeos.platform import boards_window as bw

        for mode, cap in (("window", limits.BOARDS_PER_RUN), ("catchup", limits.BOARDS_CATCHUP_PER_RUN)):
            got = bw.plan(datetime(2026, 11, 22, 7), mode, "")
            self.assertEqual(got["limit"], cap)
            self.assertLessEqual(cap * 0.4, got["deadline_minutes"] * 60)


class Backfill(unittest.TestCase):
    """a seed board's first read belongs to the hibernatable backfill"""

    ON = {"BOARDS_BACKFILL": "on"}

    def read(self, seen):
        def lister_fn(source):
            seen.append(source["id"])
            return (
                lister.Listing(lister.FAILED, reason="http_500")
                if source["id"] == "gh-s1"
                else lister.Listing(lister.COMPLETE, [])
            )

        return lister_fn

    def test_off_by_default_it_reads_nothing_and_touches_no_store(self):
        seen = []
        for environ in ({}, {"BOARDS_BACKFILL": "off"}, {"BOARDS_BACKFILL": "yes"}):
            got = boards.backfill(
                450, True, NOW, self.read(seen), repo=None, sources=SEED, environ=environ
            )  # repo None: a store session would fail here
            self.assertEqual(got, {"pass": "employer boards backfill", "backfill": "off"})
        self.assertEqual(seen, [])

    def test_the_hourly_pass_never_makes_a_first_read(self):
        seen, repo = [], MemRepo()
        got = boards.run(600, True, NOW, self.read(seen), repo, SEED)
        self.assertEqual((seen, got["due"]), ([], 0))

    def test_it_drains_in_id_order_resumes_where_it_stopped_and_hands_boards_to_the_hourly_pass(self):
        seen, repo = [], MemRepo()
        first = boards.backfill(2, True, NOW, self.read(seen), repo, SEED, self.ON)
        self.assertEqual(sorted(seen), ["gh-s0", "gh-s1"])  # the first two in id order (read in parallel)
        self.assertEqual(
            {k: first[k] for k in ("read", "errors", "remaining", "backfill")},
            {"read": 2, "errors": 1, "remaining": 3, "backfill": "on"},
        )
        rest = boards.backfill(450, True, NOW, self.read(seen), repo, SEED, self.ON)
        self.assertEqual(sorted(seen[2:]), ["gh-s2", "gh-s3", "gh-s4"])  # nothing read twice
        self.assertEqual((rest["remaining"], rest["backfill"]), (0, "drained"))
        self.assertEqual(boards.backfill(450, True, NOW, self.read(seen), repo, SEED, self.ON)["read"], 0)
        later = boards.run(600, True, NOW + timedelta(days=2), self.read(seen), repo, SEED)
        self.assertEqual(later["due"], 5)  # the failed one is retried by the hourly pass too
        self.assertEqual(sorted(seen[5:]), [s["id"] for s in SEED])

    def test_a_dry_run_writes_nothing_and_reports_the_whole_backlog(self):
        seen, repo = [], MemRepo()
        got = boards.backfill(2, False, NOW, self.read(seen), repo, SEED, self.ON)
        self.assertEqual((got["read"], got["remaining"], repo.state), (2, 5, {}))

    def test_one_tick_reads_a_forced_known_board_once_and_still_drains_the_never_read_ones(self):
        """TL gate on 9c366e5: known / unread is classified against the persisted state"""
        from lifeos.jobs import tick

        seen, repo = [], MemRepo()
        boards.backfill(
            450, True, NOW, self.read(seen), repo, SEED[:3], self.ON
        )  # gh-s0..s2 read before: known, not due for a day
        del seen[:]
        steps = (tick.Step("boards", 600, 600, bulk=True), next(s for s in tick.PLAN if s.name == "boards-backfill"))
        stages = {
            "boards": lambda limit, live: boards.run(
                limit, live, NOW + timedelta(hours=1), self.read(seen), repo, SEED
            ),
            "boards-backfill": lambda limit, live: boards.backfill(
                limit, live, NOW + timedelta(hours=1), self.read(seen), repo, SEED, self.ON
            ),
        }
        with mock.patch.dict("os.environ", {"WEB_FORCE": "board:gh-s2"}):
            out = tick.tick(True, plan=steps, stages=stages)
        self.assertEqual(out, {"boards": "ok", "boards-backfill": "ok"})
        self.assertEqual(sorted(seen[:2]), ["gh-s1", "gh-s2"])
        self.assertEqual(sorted(seen[2:]), ["gh-s3", "gh-s4"])  # backfill: only the never-read backlog
        self.assertEqual(seen.count("gh-s2"), 1)
        self.assertEqual(boards.backfill(450, False, NOW, self.read(seen), repo, SEED, self.ON)["remaining"], 0)


class OneRun(unittest.TestCase):
    def test_only_possible_targets_are_stored_and_admitted_with_the_boards_own_name(self):
        listings = {
            "b0": lister.Listing(
                lister.COMPLETE,
                [
                    job(1, "Senior Technical Program Manager", company="Board Zero Inc"),
                    job(2, "Staff Software Engineer"),
                    job(3, "Program Manager", "London, UK"),
                ],
            ),
            "b1": lister.Listing(lister.COMPLETE, [job(4, "Account Executive")]),
            "b2": lister.Listing(lister.COMPLETE, []),
        }
        repo = MemRepo()
        got = boards.run(600, True, NOW, lambda s: listings[s["slug"]], repo, SOURCES)
        self.assertEqual((got["complete"], got["admit"], got["pass"]), (3, 1, "employer boards"))
        self.assertEqual(
            got["screen"],
            {
                "kept_remote_in_location": 1,
                "title_other_profession_engineer": 1,
                "title_other_profession_account_executive": 1,
                "non_us": 1,
            },
        )
        self.assertEqual(
            repo.items_by["gh-b0"], {"1": repo.items_by["gh-b0"]["1"]}
        )  # the engineer and the London role were never stored
        self.assertEqual(repo.items_by.get("gh-b1", {}), {})
        admitted = [o for o, _ in repo.commits if o.admit][0].admit[0]
        self.assertEqual(admitted["company"], "Board Zero Inc")

    def test_a_board_with_a_target_is_due_sooner_than_one_without(self):
        listings = {
            "b0": lister.Listing(lister.COMPLETE, [job(1, "Program Manager")]),
            "b1": lister.Listing(lister.COMPLETE, [job(2, "Engineer")]),
            "b2": lister.Listing(lister.COMPLETE, []),
        }
        repo = MemRepo()
        boards.run(600, True, NOW, lambda s: listings[s["slug"]], repo, SOURCES)
        hot, cold = repo.state["gh-b0"]["due_at"], repo.state["gh-b1"]["due_at"]
        self.assertTrue(NOW + timedelta(hours=3) <= hot < NOW + timedelta(hours=9))
        self.assertTrue(NOW + timedelta(hours=12) <= cold < NOW + timedelta(hours=36))
        self.assertLess(hot, cold)

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
        self.assertEqual(guard({**SOURCES[0], "kind": "ashby"}).reason, "rate_limited_429")  # another ATS is still read

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
        self.assertEqual(
            self.commit({"id": "affirm", "kind": "greenhouse", "company": "Affirm"}, nothing),
            ["v7_sources", "v7_source_runs"],
        )


class HeldConnection(unittest.TestCase):
    """a board pass reuses ONE store connection (a new one through the tunnel costs ~0.5 s, and the pass made one per
    board).
    """

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=None):
            pass

        def fetchall(self):
            return []

    class Conn:
        opened = closed = pings = 0

        def __init__(self, owner):
            self.owner = owner

        def __enter__(self):
            self.owner.opened += 1
            return self

        def __exit__(self, *a):
            self.owner.closed += 1
            return False

        def cursor(self):
            return HeldConnection.Cur()

        def ping(self, reconnect=False):
            self.owner.pings += 1

    class Owner:
        opened = closed = pings = 0

    def repo_and_owner(self):
        owner = self.Owner()
        return run.SqlRepo(), owner

    def uses(self, repo, owner, n):
        from unittest import mock

        with mock.patch.object(run.store, "connect", side_effect=lambda: HeldConnection.Conn(owner)):
            for _ in range(n):
                repo.items("x")

    def test_outside_a_pass_every_use_opens_its_own_connection(self):
        repo, owner = self.repo_and_owner()
        self.uses(repo, owner, 5)
        self.assertEqual((owner.opened, owner.closed), (5, 5))

    def test_inside_a_pass_every_use_shares_one_connection_that_is_closed_at_the_end(self):
        repo, owner = self.repo_and_owner()
        with repo.holding():
            self.uses(repo, owner, 5)
            self.assertEqual((owner.opened, owner.closed), (1, 0))
        self.assertEqual((owner.opened, owner.closed), (1, 1))

    def test_a_failed_use_drops_the_connection_and_the_next_use_starts_clean(self):
        from unittest import mock

        repo, owner = self.repo_and_owner()
        with repo.holding(), mock.patch.object(run.store, "connect", side_effect=lambda: HeldConnection.Conn(owner)):
            repo.items("x")
            with self.assertRaises(RuntimeError), repo._connection():
                raise RuntimeError("lost")
            repo.items("x")
        self.assertEqual((owner.opened, owner.closed), (2, 2))

    def test_a_timed_out_save_detaches_the_connection_without_closing_the_write_that_still_owns_it(self):
        from unittest import mock

        repo, owner = self.repo_and_owner()
        with repo.holding(), mock.patch.object(run.store, "connect", side_effect=lambda: HeldConnection.Conn(owner)):
            repo.items("x")
            repo.detach()
            repo.items("x")
        self.assertEqual((owner.opened, owner.closed), (2, 1))

    def test_a_connection_idle_for_a_few_seconds_is_pinged_before_the_next_use(self):
        from unittest import mock

        repo, owner = self.repo_and_owner()
        with repo.holding(), mock.patch.object(run.store, "connect", side_effect=lambda: HeldConnection.Conn(owner)):
            repo.items("x")
            repo.items("x")
            self.assertEqual(owner.pings, 0)
            repo._live = (repo._live[0], repo._live[1], repo._live[2] - run.SqlRepo.HELD_IDLE_SECONDS - 1)
            repo.items("x")
        self.assertEqual((owner.opened, owner.pings), (1, 1))


class TouchInChunks(unittest.TestCase):
    """touching a board's unchanged postings is one statement per 500 ids, not one per posting (each statement is a round
    trip through the store tunnel).
    """

    def test_twelve_hundred_unchanged_postings_are_three_statements_with_every_id_once(self):
        sent = []

        class Cur:
            def execute(self, sql, args=None):
                sent.append((sql, args))

        ids = [f"j{i}" for i in range(1200)]
        run.update_in_chunks(
            Cur(), "UPDATE v7_source_items SET last_seen=%s WHERE source_id=%s AND provider_job_id", (NOW, "s1"), ids
        )
        self.assertEqual([len(a) - 2 for _, a in sent], [500, 500, 200])
        self.assertEqual([i for _, a in sent for i in a[2:]], ids)
        self.assertTrue(all(sql.count("%s") == len(a) for sql, a in sent))
        run.update_in_chunks(Cur(), "UPDATE x SET y=%s WHERE z", (1,), [])
        self.assertEqual(len(sent), 3)  # no ids: no statement

    def test_the_same_rows_are_touched_on_a_real_engine(self):
        from unittest import mock
        from tests.jobs.fakes_jobsdb import JobsDB

        db = JobsDB()
        board = [{**SOURCES[0], "id": "gh-touch", "slug": "touch"}]
        full = lister.Listing(
            lister.COMPLETE, [job(1, "Technical Program Manager"), job(2, "Program Manager"), job(3, "Program Manager")]
        )
        seen = lambda: dict(
            db.raw.execute(
                "SELECT provider_job_id, last_seen FROM v7_source_items WHERE source_id='gh-touch'"
            ).fetchall()
        )
        with (
            mock.patch.object(run.store, "connect", return_value=db),
            mock.patch.object(run.store, "ensure_schema"),
            mock.patch.object(run, "update_in_chunks", wraps=run.update_in_chunks) as spy,
        ):
            boards.run(600, True, NOW, lambda s: full, None, board)
            later = NOW + timedelta(days=1)
            boards.run(
                600,
                True,
                later,
                lambda s: lister.Listing(lister.COMPLETE, full.jobs[:2] + [job(3, "Program Manager", "London, UK")]),
                None,
                board,
            )
        self.assertTrue(spy.called)
        self.assertEqual({str(v)[:19] for v in seen().values()}, {str(later)[:19]})


class StillListedIsNotRemoved(unittest.TestCase):
    """only a job gone from the complete listing is REMOVED."""

    def test_a_classification_change_never_masquerades_as_a_removal(self):
        from unittest import mock
        from tests.jobs.fakes_jobsdb import JobsDB

        db = JobsDB()
        board = [{**SOURCES[0], "id": "gh-still", "slug": "still"}]
        listings = [
            lister.Listing(lister.COMPLETE, [job(1, "Technical Program Manager"), job(2, "Program Manager")]),
            lister.Listing(
                lister.COMPLETE, [job(1, "Technical Program Manager", "London, UK"), job(2, "Program Manager")]
            ),  # job 1 still listed, now screened out (non_us)
            lister.Listing(lister.COMPLETE, [job(2, "Program Manager")]),
        ]  # job 1 gone from the board
        state = lambda: dict(
            db.raw.execute("SELECT provider_job_id, state FROM v7_source_items WHERE source_id='gh-still'").fetchall()
        )
        with mock.patch.object(run.store, "connect", return_value=db), mock.patch.object(run.store, "ensure_schema"):
            got = [
                boards.run(600, True, NOW + timedelta(days=d), lambda s, l=l: l, None, board)
                for d, l in enumerate(listings)
            ]
            self.assertEqual(state(), {"1": "REMOVED", "2": "CURRENT"})
        self.assertEqual([(g["removed"], g["still_listed"]) for g in got], [(0, 0), (0, 1), (1, 0)])


class ChangedDescription(unittest.TestCase):
    """an unchanged posting costs nothing"""

    BODY = (
        "We are hiring a Technical Program Manager to lead cross-functional programs across engineering, product and operations. "
        "You will own roadmaps, manage dependencies, run planning cycles and report to executives. Requirements: 7 years of program management experience, "
        "strong communication, experience with cloud platforms and agile delivery. This is a fully remote role open to candidates in the United States. "
    )

    def board(self, content):
        listing = dict(job(7, "Technical Program Manager"), content=content, posted=None)
        return lister.Listing(lister.COMPLETE, [listing])

    def pass_(self, db, day, content):
        from unittest import mock

        with mock.patch.object(run.store, "connect", return_value=db), mock.patch.object(run.store, "ensure_schema"):
            return boards.run(
                600,
                True,
                NOW + timedelta(days=day),
                lambda s: self.board(content),
                None,
                [{**SOURCES[0], "id": "gh-desc", "slug": "desc"}],
            )

    def stored(self, db):
        return db.raw.execute(
            "SELECT j.id, j.status, d.fingerprint FROM v7_jobs j JOIN v7_job_descriptions d ON d.job_id = j.id"
        ).fetchall()

    def test_an_unchanged_posting_rewrites_nothing_and_a_changed_one_rewrites_once(self):
        from tests.jobs.fakes_jobsdb import JobsDB

        db = JobsDB()
        first = self.pass_(db, 0, self.BODY)
        self.assertEqual(first["jd_reread"], 0)
        ((job_id, status, fp1),) = self.stored(db)
        again = self.pass_(db, 2, self.BODY)
        self.assertEqual((again["jd_reread"], again["fit_rescore_due"]), (0, 0))
        changed = self.pass_(
            db, 4, self.BODY + "New: the role now also owns the data platform migration and vendor management. " * 3
        )
        self.assertEqual((changed["jd_reread"], changed["fit_rescore_due"], changed["changed"]), (1, 1, 1))
        ((_, status2, fp2),) = self.stored(db)
        self.assertNotEqual(fp1, fp2)
        self.assertEqual(status2, status)  # no status is written by the refresh

    def test_the_fit_stage_picks_the_job_up_because_its_stored_fingerprint_no_longer_matches_its_score(self):
        from tests.jobs.fakes_jobsdb import JobsDB

        db = JobsDB()
        self.pass_(db, 0, self.BODY)
        ((job_id, _, fp1),) = self.stored(db)
        db.raw.execute(
            "INSERT INTO v7_job_fit (job_id, score, decision, line, confidence, model_version, profile_hash, jd_fingerprint, scored_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, 80, "GO", "x", "high", "6", "p", fp1, "2026-10-01 00:00:00"),
        )
        self.pass_(db, 2, self.BODY)
        self.assertEqual(
            db.raw.execute(
                "SELECT COUNT(*) FROM v7_job_descriptions d JOIN v7_job_fit f ON f.job_id = d.job_id WHERE f.jd_fingerprint <> d.fingerprint"
            ).fetchone()[0],
            0,
        )
        self.pass_(
            db,
            4,
            self.BODY
            + "Changed duties: lead the migration of the data platform and manage vendors across three regions. " * 3,
        )
        self.assertEqual(
            db.raw.execute(
                "SELECT COUNT(*) FROM v7_job_descriptions d JOIN v7_job_fit f ON f.job_id = d.job_id WHERE f.jd_fingerprint <> d.fingerprint"
            ).fetchone()[0],
            1,
        )

    def test_a_published_job_keeps_its_status_and_notion_link_when_its_description_changes(self):
        from tests.jobs.fakes_jobsdb import JobsDB

        db = JobsDB()
        self.pass_(db, 0, self.BODY)
        ((job_id, _, _),) = self.stored(db)
        db.raw.execute(
            "UPDATE v7_jobs SET status='PUBLISHED', notion_page_id='page-1', final_apply_url='https://example.com/apply' WHERE id=?",
            (job_id,),
        )
        self.pass_(
            db,
            4,
            self.BODY
            + "Duties changed: the role now also covers vendor management and budget ownership for the programs. " * 3,
        )
        row = db.raw.execute(
            "SELECT status, notion_page_id, final_apply_url FROM v7_jobs WHERE id=?", (job_id,)
        ).fetchone()
        self.assertEqual(row, ("PUBLISHED", "page-1", "https://example.com/apply"))

    def test_a_description_that_fails_the_new_job_checks_is_not_stored(self):
        from tests.jobs.fakes_jobsdb import JobsDB

        db = JobsDB()
        self.pass_(db, 0, self.BODY)
        ((_, _, fp1),) = self.stored(db)
        got = self.pass_(db, 4, "too short")
        self.assertEqual(got["jd_reread"], 0)
        self.assertEqual(self.stored(db)[0][2], fp1)

    def test_a_failed_read_changes_nothing(self):
        from unittest import mock
        from tests.jobs.fakes_jobsdb import JobsDB

        db = JobsDB()
        self.pass_(db, 0, self.BODY)
        before = self.stored(db)
        with mock.patch.object(run.store, "connect", return_value=db), mock.patch.object(run.store, "ensure_schema"):
            got = boards.run(
                600,
                True,
                NOW + timedelta(days=4),
                lambda s: lister.Listing(lister.FAILED, reason="network"),
                None,
                [{**SOURCES[0], "id": "gh-desc", "slug": "desc"}],
            )
        self.assertEqual((got["failed"], got["jd_reread"]), (1, 0))
        self.assertEqual(self.stored(db), before)


class MissedJobs(unittest.TestCase):
    def screen(self, title, where, text=None):
        return boards.screen({"title": title, "location": where, "posted": None, "content": text})

    def test_the_named_vacancies_are_kept(self):
        dave = (
            "Dave is building a better banking experience. "
            * 160
            + "As a virtual-first company, team members can live and work anywhere in the "
            "United States, except Hawaii. " + "Benefits. " * 20 + "#LI-REMOTE"
        )  # the tag ends a 7,900-character description
        spruce = "Technical Program Manager (TPM) (Remote but must be based in the continental US; Mountain/Central time zone preferred). We build identity software."
        cases = [
            ("Dave", "Lead Technical Program Manager", "United States", dave, "kept_remote_in_description"),
            ("SpruceID", "Technical Program Manager (TPM)", "United States", spruce, "kept_remote_in_description"),
            (
                "Chainguard",
                "Senior Technical Program Manager",
                "United States - Remote",
                None,
                "kept_remote_in_location",
            ),
            (
                "Runpod",
                "Technical Program Manager (Software Engineer)",
                "Remote - USA",
                None,
                "kept_remote_in_location",
            ),
            (
                "Human Interest",
                "Senior Technical Program Manager",
                "United States, Remote",
                None,
                "kept_remote_in_location",
            ),
            ("Fluxon", "Senior Program Manager", "United States, Remote", None, "kept_remote_in_location"),
            ("Backblaze", "Sr. Technical Program Manager (Security)", "Remote - US", None, "kept_remote_in_location"),
            ("Fluxon", "Senior Program Manager", "Canada, Remote", None, "non_us"),
            (
                "Backblaze",
                "Sr. Technical Program Manager | LATAM",
                "Remote - Colombia ; Remote - Costa Rica",
                None,
                "non_us",
            ),
        ]
        for company, title, where, text, want in cases:
            with self.subTest(company=company, title=title, where=where):
                self.assertEqual(self.screen(title, where, text), want)

    def test_fit_reads_the_same_statements_so_the_lane_does_not_park_them_on_review(self):
        from lifeos.jobs import lanes

        self.assertEqual(
            lanes.work_mode_for_fit(
                "United States", "Technical Program Manager (TPM)", "(Remote but must be based in the continental US)"
            ),
            "remote",
        )
        self.assertEqual(
            lanes.work_mode_for_fit("United States", "Lead Technical Program Manager", "x " * 4000 + "#LI-REMOTE"),
            "remote",
        )
        self.assertEqual(
            lanes.detect_work_mode("United States", "Program Manager", "About us. #LI-Hybrid"), "hybrid"
        )  # the tag is read for every mode

    def test_the_review_sample_roles_get_the_right_verdict(self):
        cases = [
            ("Principal Product Manager", "strong"),
            ("Sr. Product Manager - Mortgage Lending Technology", "strong"),
            ("Product Owner - Revenue Systems", "strong"),
            ("Director of Product Marketing", "title_other_profession_marketing"),
            ("AI Strategy & Transformation Expert", "adjacent"),
            ("Head of Technology & Product (US or Europe, remote)", "adjacent"),
            ("Director of Operations & Communications", "adjacent"),
            ("Product Management Coach", "title_not_a_role_coach"),
            ("Operations Specialist I, Lifesciences", "title_other_profession_specialist"),
            ("Senior Product Designer II, Growth (Remote US)", "title_other_profession_designer"),
            ("Revenue Operations Manager", "title_other_profession_revenue"),
            ("Senior Program Officer", "title_other_profession_officer"),
            ("Underwriting Program Leader - Property and Casualty", "title_other_profession_underwriting"),
            ("Product Marketing Coordinator", "title_other_profession_marketing"),
            ("Interested in future Tech positions? (Engineering, IT, Data, Product)", "title_not_a_role_future"),
        ]
        for title, want in cases:
            with self.subTest(title=title):
                tier, why = boards.role(title)
                self.assertEqual(tier or "title_" + why, want)

    def test_runpod_is_read_as_a_hand_added_board(self):
        self.assertIn(("ashby", "runpod"), {(r["kind"], r["slug"]) for r in registry.boards()})


class Preview(unittest.TestCase):
    """The acceptance preview reads the same sample, scores kept jobs with the shared Fit, and writes nothing."""

    def test_every_lane_reason_has_a_fixed_public_code(self):
        from lifeos.jobs import lanes

        cases = {
            f"Fit 55 below {lanes.REVIEW_FLOOR}": "fit_below_review_floor",
            f"Fit 70 below {lanes.FIT_FLOOR}": "fit_below_admit_floor",
            f"Fit 70.5 below {lanes.FIT_FLOOR} (on Jim's keep list)": "fit_below_admit_floor_keep_list",
            f"Fit 65 below {lanes.NEAR_MISS_FLOOR} and market unresolved": "fit_below_near_miss_with_open_question",
            "not remote: hybrid required": "not_remote_hybrid_required",
            "not remote: onsite required": "not_remote_onsite_required",
            "remote assumed": "remote_assumed",
            "Fit unscorable": "fit_unscorable",
            "vacancy closed": "vacancy_closed",
            "explicit pay below $120,000": "pay_below_floor",
            "posting date unresolved": "posting_date_unresolved",
            "posting date in the future": "posting_date_future",
            "older than 30 days": "older_than_max_age",
            "market unresolved": "market_unresolved",
            "market UK is not US": "market_mismatch",
            "market not shown to be UK": "market_not_shown",
            "geography unresolved": "geography_unresolved",
            "geography does not qualify": "geography_not_qualifying",
            "not in London": "not_in_london",
            "Skilled Worker route negative": "route_negative",
            "Skilled Worker route unresolved": "route_unresolved",
            "lane disabled": "lane_disabled",
            lanes.TITLE_ONLY_REASON: "title_only",
            None: "none",
            "": "none",
        }
        for reason, code in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(lanes.public_reason(reason), code)

    def test_a_public_reason_never_copies_a_private_rule_a_quote_or_unknown_text(self):
        private = {
            "excluded: acme-recruiting-rule": "excluded_private_rule",
            'not remote: hybrid required ("3 days a week in our Chicago office")': "not_remote_hybrid_required",
            'not remote: onsite required ("must work onsite")': "not_remote_onsite_required",
            "Fit 71 below 72 because of profile capability secret-alpha": "other",
            "Traceback: KeyError 'FIT_PROFILE_JSON'": "other",
            "remote assumed; profile: secret-alpha": "other",
            "Fit 71 below 99": "fit_below_floor",
            "not remote: hybrid required, see secret-alpha": "other",
        }
        for reason, code in private.items():
            with self.subTest(reason=reason):
                got = lanes.public_reason(reason)
                self.assertEqual(got, code)
                self.assertIn(
                    got, {c for _, c in lanes.PUBLIC_REASONS} | {"none", "other"}
                )  # only enumerated codes ever leave
                for word in ("acme", "Chicago", "secret", "Traceback", "must work"):
                    self.assertNotIn(word.casefold(), got.casefold())

    def test_the_acceptance_cuts_sit_around_the_fit_floor(self):
        got = [boards.acceptance_band(s) for s in (None, 0, 59.9, 60, 69, 69.5, 70, 71.9, 72, 100)]
        self.assertEqual(
            got, ["unscored", "lt60", "lt60", "60_69", "60_69", "60_69", "70_71", "70_71", "72_up", "72_up"]
        )

    def test_counts_every_stage_and_writes_nothing(self):
        from tests.jobs.fakes_jobsdb import JobsDB
        from lifeos.jobs import identity

        db = JobsDB()
        known = "https://boards.greenhouse.io/x/jobs/2"
        db.insert(
            "v7_jobs",
            dedupe_key=identity.url_hash(known),
            status="PUBLISHED",
            title="TPM",
            company="A",
            source="web:x",
            source_url=known,
            first_seen=NOW,
            last_seen=NOW,
            updated_at=NOW,
            lane="US Remote",
            notion_page_id="p1",
        )
        listing = lister.Listing(
            lister.COMPLETE,
            [
                job(1, "Senior Technical Program Manager"),
                job(2, "Technical Program Manager"),
                job(3, "Staff Software Engineer"),
            ],
        )
        env = {
            "FIT_PROFILE_JSON": '{"years": 15, "capabilities": [{"id": "a", "label": "A", "class": "direct", "terms": ["alpha"]}], "scope": {"team_size": 10}}'
        }
        before = db.raw.execute("SELECT COUNT(*) FROM v7_jobs").fetchone()[0]
        got = boards.preview(3, False, env, lambda s: listing, connection=db)
        self.assertEqual(
            (got["boards"], got["kept"], got["fit_evaluated"], got["already_held"], got["already_published"]),
            (3, 2, 2, 1, 1),
        )
        self.assertEqual(sum(got["decision"].values()), 2)
        self.assertEqual(got["screen"]["title_other_profession_engineer"], 3)
        self.assertEqual(sum(got["fit_band"].values()), 2)
        self.assertEqual(list(got["by_evidence"]), ["remote_in_location"])  # decisions split by remote stated / assumed
        self.assertEqual(
            sum(v for k, v in got["by_evidence"]["remote_in_location"].items() if not k.startswith("fit_")), 2
        )
        self.assertEqual(db.raw.execute("SELECT COUNT(*) FROM v7_jobs").fetchone()[0], before)


def quiet(_source):
    return lister.Listing(lister.COMPLETE, [])


class Window(unittest.TestCase):
    """one board window."""

    ENV = {
        "BOARDS_SLOT_CT": "1:00 AM CT",
        "BOARDS_MODE": "window",
        "BOARDS_LATE_REASON": "",
        "BOARDS_SLOT": "2026-10-17T06:00:00Z",
    }

    def waiting(self, hours):
        repo = MemRepo()
        repo.state = {s["id"]: {"due_at": NOW - timedelta(hours=h), "failures": 0} for s, h in zip(SEED, hours)}
        return repo

    def test_the_receipt_counts_due_processed_deferred_failed_and_the_age_of_the_backlog(self):
        repo = self.waiting((9, 7, 5, 3, 1))
        got = boards.run(
            3,
            True,
            NOW,
            lambda s: (
                lister.Listing(lister.FAILED, reason="rate_limited_429")
                if s["id"] == "gh-s1"
                else lister.Listing(lister.COMPLETE, [])
            ),
            repo,
            SEED,
            self.ENV,
        )
        r = got["receipt"]
        self.assertEqual((r["due"], r["processed"], r["deferred"], r["failed"]), (5, 3, 2, 1))
        self.assertEqual((r["backlog_age_hours"], r["oldest_deferred_hours"]), (9.0, 3.0))
        self.assertEqual(r["carry_forward"], {"boards": 2, "oldest_overdue_hours": 3.0, "next_window": "7:00 AM CT"})
        self.assertEqual(
            r["provider_limit_hits"], {"rate_limited_429": 1, "skipped_after_rate_limit_429": 0, "total": 1}
        )
        self.assertEqual((r["freshness_ceiling_hours"], r["carry_within_ceiling"]), (6, True))
        self.assertEqual(
            (r["window"], r["mode"], r["late_reason"], r["cap"]), ("1:00 AM CT", "window", "", limits.BOARDS_PER_RUN)
        )
        self.assertTrue(r["started"].endswith("CT") and r["finished"].endswith("CT"))
        self.assertEqual(r["timing"]["boards"], 3)
        self.assertIn("Save s per board (p50, p95)", boards.receipt_markdown(r))

    def test_the_counts_say_where_the_time_went_reading_waiting_and_saving(self):
        got = boards.run(5, True, NOW, quiet, self.waiting((9, 7, 5, 3, 1)), SEED, self.ENV)
        t = got["timing"]
        self.assertEqual(t["boards"], 5)
        for key in ("wall_s", "waiting_for_reads_s", "saving_s"):
            self.assertGreaterEqual(t[key], 0)
        self.assertEqual(len(t["read_s_p50_p95"]), 2)
        self.assertEqual(len(t["save_s_p50_p95"]), 2)

    def test_a_window_reads_at_most_the_window_cap_and_a_catch_up_the_larger_one_oldest_due_first(self):
        many = [{**SEED[0], "id": f"gh-m{i:04d}", "slug": f"m{i}"} for i in range(3000)]
        repo = MemRepo()
        repo.state = {s["id"]: {"due_at": NOW - timedelta(minutes=3000 - i), "failures": 0} for i, s in enumerate(many)}
        window = boards.run(5000, False, NOW, quiet, repo, many, self.ENV)
        self.assertEqual(
            (window["receipt"]["processed"], window["receipt"]["deferred"], window["receipt"]["cap"]),
            (limits.BOARDS_PER_RUN, 3000 - limits.BOARDS_PER_RUN, 1800),
        )
        catchup = boards.run(5000, False, NOW, quiet, repo, many, {**self.ENV, "BOARDS_MODE": "catchup"})
        self.assertEqual(
            (catchup["receipt"]["processed"], catchup["receipt"]["deferred"], catchup["receipt"]["cap"]),
            (limits.BOARDS_CATCHUP_PER_RUN, 3000 - limits.BOARDS_CATCHUP_PER_RUN, 2900),
        )
        self.assertEqual(catchup["slowest"] is not None, True)

    def test_leftovers_come_first_in_the_next_window_and_nothing_due_is_lost(self):
        repo = self.waiting((9, 7, 5, 3, 1))
        seen = []

        def read(source):
            seen.append(source["id"])
            return lister.Listing(lister.COMPLETE, [])

        boards.run(3, True, NOW, read, repo, SEED, self.ENV)  # an interrupted window: 3 of 5 read
        self.assertEqual(sorted(seen), ["gh-s0", "gh-s1", "gh-s2"])
        del seen[:]
        later = boards.run(1800, True, NOW, read, repo, SEED, self.ENV)
        self.assertEqual(sorted(seen), ["gh-s3", "gh-s4"])
        self.assertEqual((later["receipt"]["due"], later["receipt"]["deferred"]), (2, 0))

    def test_the_soft_deadline_stops_new_reads_keeps_what_was_read_and_still_writes_the_receipt(self):
        repo = self.waiting((9, 7, 5, 3, 1))
        ticks = iter([0, 0, 0, 100, 100, 100, 100, 100])
        got = boards.run(
            1800, True, NOW, quiet, repo, SEED, {**self.ENV, "BOARDS_DEADLINE_MINUTES": "1"}, clock=lambda: next(ticks)
        )
        self.assertEqual(sorted(repo.state), sorted(s["id"] for s in SEED))
        r = got["receipt"]
        self.assertEqual((r["processed"], r["deferred"], r["stopped_at_deadline"]), (2, 3, True))
        self.assertEqual(r["carry_forward"]["boards"], 3)

    def test_without_a_window_environment_the_stage_still_runs_as_a_plain_window(self):
        repo = self.waiting((2, 1, 1, 1, 1))
        got = boards.run(1800, False, NOW, quiet, repo, SEED, {})
        self.assertEqual(
            (got["receipt"]["mode"], got["receipt"]["window"], got["receipt"]["cap"]),
            ("window", "", limits.BOARDS_PER_RUN),
        )
