"""D202: the employer-board pass. The registry built from the seed and extras, the seed builder, and one bounded run over an in-memory repo."""
from datetime import datetime, timedelta
import json
import tempfile
import unittest
from pathlib import Path

from lifeos.jobs import lanes
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


class Screen(unittest.TestCase):
    """A seed board is an unknown employer: a job is stored only when its title is in the family and remote is SHOWN (place, title or an
    explicit description statement). Every other job gets a named reason."""

    def test_each_job_gets_its_reason(self):
        remote_jd = "About the role. This is a fully remote position open to candidates across the US."
        cases = [("Senior Technical Program Manager", "Remote, US", None, "kept_remote_in_location"),
                 ("Program Manager", "Remote", None, "kept_remote_in_location_place_unresolved"),          # M5: remote alone names no market
                 ("Senior Project Manager", "Austin/Remote within the US", None, "kept_remote_in_location"),
                 ("Technical Program Manager", "New York, NY", remote_jd, "kept_remote_in_description"),
                 ("Technical Program Manager", "United States", remote_jd, "kept_remote_in_description"),
                 ("TPM", "United States", "Lead programs across teams.", "kept_remote_assumed"),      # D204: nothing required: presumed, labelled so
                 ("Technical Program Manager", "Austin, Texas, United States", None, "kept_remote_assumed"),
                 ("Technical Program Manager", "San Diego HQ", None, "kept_remote_assumed"),                 # M5: a US city (lanes.US_PLACES)
                 ("Technical Program Manager", "RWC HQ", None, "kept_remote_assumed_place_unresolved"),          # names nothing known: unresolved
                 ("Program Manager", "Espoo", None, "non_us"), ("Program Manager", "Abu Dhabi", None, "non_us"),   # foreign places seen in the M4 sample
                 ("Program Manager", "Calgary", None, "non_us"), ("Program Manager", "Wrocław", None, "non_us"),
                 ("Program Manager", "UT - Cottonwood Heights; FL - Jacksonville", None, "kept_remote_assumed"),
                 ("Program Manager", "Hybrid - Remote, US", None, "kept_remote_in_location"),                 # remote offered alongside hybrid
                 ("Program Manager", "New York, NY", "This is a hybrid role: 3 days a week in our office.", "not_remote_hybrid"),
                 ("Program Manager", "Seattle, WA", "There is no onsite requirement for this role.", "kept_remote_assumed"),
                 ("Program Manager", "Algiers, Algeria", None, "non_us"), ("Program Manager", "Remote | North America or Europe", None, "kept_remote_in_location_place_unresolved"),
                 ("Program Manager", "Seattle, WA", "This role is onsite five days a week.", "not_remote_onsite"),
                 ("Project Manager", "Remote - Colombia", remote_jd, "non_us"),
                 ("Program Manager", "Remote - EMEA", None, "non_us"),
                 ("Program Manager", "London, UK", None, "non_us"),
                 ("Staff Software Engineer", "Remote, US", None, "title_other_profession_engineer")]
        for title, where, text, want in cases:
            with self.subTest(title=title, where=where):
                self.assertEqual(boards.screen({"title": title, "location": where, "posted": None, "content": text}), want)
                self.assertEqual(boards.keep({"title": title, "location": where, "posted": None, "content": text}), want.startswith("kept_"))


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
        self.assertEqual((got["complete"], got["admit"], got["pass"]), (3, 1, "employer boards"))
        self.assertEqual(got["screen"], {"kept_remote_in_location": 1, "title_other_profession_engineer": 1, "title_other_profession_account_executive": 1, "non_us": 1})
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


class StillListedIsNotRemoved(unittest.TestCase):
    """Production-shaped (the real SqlRepo statements over the in-memory Jobs store): a stored job that the screen no longer keeps while the
    employer still lists it stays CURRENT; only a job gone from the complete listing is REMOVED."""

    def test_a_classification_change_never_masquerades_as_a_removal(self):
        from unittest import mock
        from tests.kit.jobsdb import JobsDB
        db = JobsDB()
        board = [{**SOURCES[0], "id": "gh-still", "slug": "still"}]
        listings = [lister.Listing(lister.COMPLETE, [job(1, "Technical Program Manager"), job(2, "Program Manager")]),
                    lister.Listing(lister.COMPLETE, [job(1, "Technical Program Manager", "London, UK"), job(2, "Program Manager")]),    # job 1 still listed, now screened out (non_us)
                    lister.Listing(lister.COMPLETE, [job(2, "Program Manager")])]                                                  # job 1 gone from the board
        state = lambda: dict(db.raw.execute("SELECT provider_job_id, state FROM v7_source_items WHERE source_id='gh-still'").fetchall())
        with mock.patch.object(run.store, "connect", return_value=db), mock.patch.object(run.store, "ensure_schema"):
            got = [boards.run(600, True, NOW + timedelta(days=d), lambda s, l=l: l, None, board) for d, l in enumerate(listings)]
            self.assertEqual(state(), {"1": "REMOVED", "2": "CURRENT"})
        self.assertEqual([(g["removed"], g["still_listed"]) for g in got], [(0, 0), (0, 1), (1, 0)])


class MissedJobs(unittest.TestCase):
    """The review's real cases (Oct 17), each as its employer board listed it: title, place and the work-mode sentence of its description."""

    def screen(self, title, where, text=None):
        return boards.screen({"title": title, "location": where, "posted": None, "content": text})

    def test_the_named_vacancies_are_kept(self):
        dave = ("Dave is building a better banking experience. " * 160 + "As a virtual-first company, team members can live and work anywhere in the "
                "United States, except Hawaii. " + "Benefits. " * 20 + "#LI-REMOTE")                  # the tag ends a 7,900-character description
        spruce = "Technical Program Manager (TPM) (Remote but must be based in the continental US; Mountain/Central time zone preferred). We build identity software."
        cases = [("Dave", "Lead Technical Program Manager", "United States", dave, "kept_remote_in_description"),
                 ("SpruceID", "Technical Program Manager (TPM)", "United States", spruce, "kept_remote_in_description"),
                 ("Chainguard", "Senior Technical Program Manager", "United States - Remote", None, "kept_remote_in_location"),
                 ("Runpod", "Technical Program Manager (Software Engineer)", "Remote - USA", None, "kept_remote_in_location"),
                 ("Human Interest", "Senior Technical Program Manager", "United States, Remote", None, "kept_remote_in_location"),
                 ("Fluxon", "Senior Program Manager", "United States, Remote", None, "kept_remote_in_location"),
                 ("Backblaze", "Sr. Technical Program Manager (Security)", "Remote - US", None, "kept_remote_in_location"),
                 ("Fluxon", "Senior Program Manager", "Canada, Remote", None, "non_us"),
                 ("Backblaze", "Sr. Technical Program Manager | LATAM", "Remote - Colombia ; Remote - Costa Rica", None, "non_us")]
        for company, title, where, text, want in cases:
            with self.subTest(company=company, title=title, where=where):
                self.assertEqual(self.screen(title, where, text), want)

    def test_fit_reads_the_same_statements_so_the_lane_does_not_park_them_on_review(self):
        from lifeos.jobs import lanes
        self.assertEqual(lanes.work_mode_for_fit("United States", "Technical Program Manager (TPM)", "(Remote but must be based in the continental US)"), "remote")
        self.assertEqual(lanes.work_mode_for_fit("United States", "Lead Technical Program Manager", "x " * 4000 + "#LI-REMOTE"), "remote")
        self.assertEqual(lanes.detect_work_mode("United States", "Program Manager", "About us. #LI-Hybrid"), "hybrid")      # the tag is read for every mode

    def test_the_review_sample_roles_get_the_right_verdict(self):
        cases = [("Principal Product Manager", "strong"), ("Sr. Product Manager - Mortgage Lending Technology", "strong"), ("Product Owner - Revenue Systems", "strong"),
                 ("Director of Product Marketing", "title_other_profession_marketing"), ("AI Strategy & Transformation Expert", "adjacent"), ("Head of Technology & Product (US or Europe, remote)", "adjacent"),
                 ("Director of Operations & Communications", "adjacent"),
                 ("Product Management Coach", "title_not_a_role_coach"), ("Operations Specialist I, Lifesciences", "title_other_profession_specialist"),
                 ("Senior Product Designer II, Growth (Remote US)", "title_other_profession_designer"), ("Revenue Operations Manager", "title_other_profession_revenue"),
                 ("Senior Program Officer", "title_other_profession_officer"), ("Underwriting Program Leader - Property and Casualty", "title_other_profession_underwriting"),
                 ("Product Marketing Coordinator", "title_other_profession_marketing"), ("Interested in future Tech positions? (Engineering, IT, Data, Product)", "title_not_a_role_future")]
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
        cases = {f"Fit 55 below {lanes.REVIEW_FLOOR}": "fit_below_review_floor", f"Fit 70 below {lanes.FIT_FLOOR}": "fit_below_admit_floor",
                 f"Fit 70.5 below {lanes.FIT_FLOOR} (on Jim's keep list)": "fit_below_admit_floor_keep_list",
                 f"Fit 65 below {lanes.NEAR_MISS_FLOOR} and market unresolved": "fit_below_near_miss_with_open_question",
                 "not remote: hybrid required": "not_remote_hybrid_required", "not remote: onsite required": "not_remote_onsite_required",
                 "remote assumed": "remote_assumed", "Fit unscorable": "fit_unscorable", "vacancy closed": "vacancy_closed",
                 "explicit pay below $120,000": "pay_below_floor", "posting date unresolved": "posting_date_unresolved",
                 "posting date in the future": "posting_date_future", "older than 30 days": "older_than_max_age", "market unresolved": "market_unresolved",
                 "market UK is not US": "market_mismatch", "market not shown to be UK": "market_not_shown", "geography unresolved": "geography_unresolved",
                 "geography does not qualify": "geography_not_qualifying", "not in London": "not_in_london", "Skilled Worker route negative": "route_negative",
                 "Skilled Worker route unresolved": "route_unresolved", "lane disabled": "lane_disabled", lanes.TITLE_ONLY_REASON: "title_only",
                 None: "none", "": "none"}
        for reason, code in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(lanes.public_reason(reason), code)

    def test_a_public_reason_never_copies_a_private_rule_a_quote_or_unknown_text(self):
        private = {"excluded: acme-recruiting-rule": "excluded_private_rule",
                   'not remote: hybrid required ("3 days a week in our Chicago office")': "not_remote_hybrid_required",
                   'not remote: onsite required ("must work onsite")': "not_remote_onsite_required",
                   "Fit 71 below 72 because of profile capability secret-alpha": "other", "Traceback: KeyError 'FIT_PROFILE_JSON'": "other",
                   "remote assumed; profile: secret-alpha": "other", "Fit 71 below 99": "fit_below_floor", "not remote: hybrid required, see secret-alpha": "other"}
        for reason, code in private.items():
            with self.subTest(reason=reason):
                got = lanes.public_reason(reason)
                self.assertEqual(got, code)
                self.assertIn(got, {c for _, c in lanes.PUBLIC_REASONS} | {"none", "other"})     # only enumerated codes ever leave
                for word in ("acme", "Chicago", "secret", "Traceback", "must work"):
                    self.assertNotIn(word.casefold(), got.casefold())

    def test_the_acceptance_cuts_sit_around_the_fit_floor(self):
        got = [boards.acceptance_band(s) for s in (None, 0, 59.9, 60, 69, 69.5, 70, 71.9, 72, 100)]
        self.assertEqual(got, ["unscored", "lt60", "lt60", "60_69", "60_69", "60_69", "70_71", "70_71", "72_up", "72_up"])

    def test_counts_every_stage_and_writes_nothing(self):
        from tests.kit.jobsdb import JobsDB
        from lifeos.jobs import identity
        db = JobsDB()
        known = "https://boards.greenhouse.io/x/jobs/2"
        db.insert("v7_jobs", dedupe_key=identity.url_hash(known), status="PUBLISHED", title="TPM", company="A", source="web:x", source_url=known,
                  first_seen=NOW, last_seen=NOW, updated_at=NOW, lane="US Remote", notion_page_id="p1")
        listing = lister.Listing(lister.COMPLETE, [job(1, "Senior Technical Program Manager"), job(2, "Technical Program Manager"), job(3, "Staff Software Engineer")])
        env = {"FIT_PROFILE_JSON": '{"years": 15, "capabilities": [{"id": "a", "label": "A", "class": "direct", "terms": ["alpha"]}], "scope": {"team_size": 10}}'}
        before = db.raw.execute("SELECT COUNT(*) FROM v7_jobs").fetchone()[0]
        got = boards.preview(3, False, env, lambda s: listing, connection=db)
        self.assertEqual((got["boards"], got["kept"], got["fit_evaluated"], got["already_held"], got["already_published"]), (3, 2, 2, 1, 1))
        self.assertEqual(sum(got["decision"].values()), 2)
        self.assertEqual(got["screen"]["title_other_profession_engineer"], 3)
        self.assertEqual(sum(got["fit_band"].values()), 2)
        self.assertEqual(list(got["by_evidence"]), ["remote_in_location"])                  # decisions split by remote stated / assumed
        self.assertEqual(sum(v for k, v in got["by_evidence"]["remote_in_location"].items() if not k.startswith("fit_")), 2)
        self.assertEqual(db.raw.execute("SELECT COUNT(*) FROM v7_jobs").fetchone()[0], before)
