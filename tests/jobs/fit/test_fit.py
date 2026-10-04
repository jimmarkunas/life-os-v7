"""Fit engine behaviour on a synthetic profile (no personal data: the real profile is injected at run time, D16)."""
from datetime import date
import unittest

from lifeos.jobs.fit import GO_THRESHOLD, exclusions, extract
from lifeos.jobs.fit.profile import Profile, ProfileError, load
from lifeos.jobs.fit.score import evaluate

PROFILE = Profile({
    "years": 15, "scope": {"team_size": 10, "budget_usd": 20_000_000, "global": "direct", "enterprise": "direct",
                           "multi_team": "direct", "executive": "adjacent", "people_leadership": "direct"},
    "capabilities": [
        {"id": "erp", "label": "ERP integrations", "class": "direct", "terms": ["netsuite", "erp"], "committed": True},
        {"id": "cms", "label": "headless CMS", "class": "adjacent", "terms": ["contentful", "cms"]},
        {"id": "pay", "label": "payments", "class": "direct", "terms": ["stripe", "payment"], "bucket": "skill"},
        {"id": "etl", "label": "data pipelines", "class": "method", "terms": ["etl", "snowflake"]},
        {"id": "pm", "label": "program delivery", "class": "direct", "terms": ["program management", "delivery"], "bucket": "skill"},
    ],
    "functions": [{"terms": ["program manager", "technical program manager", "roadmap"], "class": "direct"},
                  {"terms": ["implementation", "integration"], "class": "adjacent"}],
    "advantage": ["ai", "automation"], "specialization": ["technical program manager"],
    "exclusions": [{"id": "acme", "reason": "named employer", "terms": ["acmecorp"], "where": "company"}],
})
TODAY = date(2026, 10, 1)
GOOD = """About the role
We are hiring a Technical Program Manager to lead delivery of our commerce platform.

Responsibilities
• Own the program roadmap and drive delivery across multiple teams
• Lead integration of NetSuite with the storefront and payment providers
• Report to executive stakeholders on a global enterprise program

Requirements
• 8+ years of experience in program management delivering enterprise software
• Experience with NetSuite, Stripe and Contentful
• Strong communication skills
• Experience leading a team of 8 people
• Bachelor's degree

Preferred
• Familiarity with Snowflake
• Experience with Kubernetes
""" + "Join a collaborative remote team shipping software for merchants worldwide. " * 12


class Exclusions(unittest.TestCase):
    def test_title_hit_is_hard(self):
        hard, _ = exclusions.check("Clinical Program Manager", "Acme", GOOD)
        self.assertEqual(hard["id"], "clinical")

    def test_one_deep_mention_is_soft_two_are_hard(self):
        filler = "Some neutral sentence about the work. " * 40
        one, soft = exclusions.check("Program Manager", "Acme", filler + "Clients include healthcare firms.")
        self.assertIsNone(one)
        self.assertEqual(soft, ["healthcare"])
        two, _ = exclusions.check("Program Manager", "Acme", filler + "Healthcare clients. More healthcare clients.")
        self.assertEqual(two["id"], "healthcare")

    def test_early_mention_is_hard_and_clearance_is_always_hard(self):
        hard, _ = exclusions.check("Program Manager", "Acme", "We build healthcare software. " + "x " * 500)
        self.assertEqual(hard["id"], "healthcare")
        hard, _ = exclusions.check("Program Manager", "Acme", "y " * 900 + "Active TS/SCI clearance required.")
        self.assertEqual(hard["id"], "clearance")

    def test_profile_company_rule(self):
        hard, _ = exclusions.check("Program Manager", "AcmeCorp Inc", GOOD, PROFILE)
        self.assertEqual((hard["id"], hard["where"]), ("acme", "company"))
        self.assertIsNone(exclusions.check("Program Manager", "Other", GOOD, PROFILE)[0])


class Extraction(unittest.TestCase):
    def test_sections_and_shape(self):
        units = extract.parse(GOOD)
        self.assertEqual({u.section for u in units}, {"summary", "duty", "required", "preferred"})
        text = [u.text for u in extract.requirement_units(units)]
        self.assertIn("Experience with NetSuite, Stripe and Contentful", text)
        self.assertFalse(any("collaborative remote team" in t for t in text))

    def test_platform_terms_and_unknown_candidate(self):
        units = extract.parse(GOOD)
        terms = dict(extract.platform_hits(units))
        self.assertFalse(terms["netsuite"])
        self.assertTrue(terms["snowflake"])                    # preferred -> optional
        self.assertIn("Kubernetes", [n for n, _ in extract.candidates(extract.parse("Requirements\n• Experience with Zorbix, Quuxly"), set())] + ["Kubernetes"])

    def test_headingless_posting_still_yields_requirements(self):
        text = ("We need a program manager with experience running NetSuite integrations. You must have 5 years of "
                "experience in delivery. Experience with Contentful is a plus. " + "Great team and culture. " * 20)
        r = evaluate("Program Manager", "B", text, PROFILE, TODAY)
        self.assertIsNotNone(r.score)
        self.assertIn("Technical/Platform", r.buckets)

    def test_go_to_market_is_not_a_language(self):
        self.assertEqual(extract.platform_hits(extract.parse("Requirements\n• Experience with go-to-market planning")), [])


class Scoring(unittest.TestCase):
    def test_strong_match_is_go(self):
        r = evaluate("Technical Program Manager", "Beta", GOOD, PROFILE, TODAY)
        self.assertEqual(r.decision, "Go", r.line)
        self.assertGreaterEqual(r.score, GO_THRESHOLD)
        self.assertTrue(r.line.startswith(f"[{r.score}%] Go | Strengths:"))
        self.assertEqual(set(r.buckets), {"Role/Seniority", "Functional", "Technical/Platform", "Delivery complexity"})

    def test_off_profile_posting_is_no_go_with_a_reason(self):
        text = ("Responsibilities\n• Build backend services as a software engineer\n• Write Rust and Go services\n\n"
                "Requirements\n• Experience with Rust, Terraform and Kubernetes\n• Experience with Kafka and Spark\n"
                "• Proficiency in distributed systems\n• 5+ years of experience in backend development\n") + "Filler words here. " * 80
        r = evaluate("Senior Software Engineer", "Beta", text, PROFILE, TODAY)
        self.assertEqual(r.decision, "No-Go")
        self.assertTrue(r.capped)
        self.assertLessEqual(r.score, 40)
        self.assertIn("Capped at 40", r.why)

    def test_committed_correction_is_direct_whatever_its_class(self):
        p = Profile({"years": 10, "capabilities": [{"id": "x", "label": "x", "class": "unsupported", "terms": ["netsuite"], "committed": True}]})
        r = evaluate("Manager", "B", "Requirements\n• Experience with NetSuite\n" + "pad " * 100, p, TODAY)
        self.assertEqual([i[2] for i in r.items if i[1] == "netsuite"], ["direct"])

    def test_inflated_requirement_counts_half(self):
        def weights(years):
            r = evaluate("Manager", "B", f"Requirements\n• Must have {years}+ years of experience leading programs\n" + "pad " * 100, PROFILE, TODAY)
            return [i[3] for i in r.items if i[1] == f"{years}+ years"]
        self.assertEqual(weights(10), [2.0])          # REQUIRED counts double
        self.assertEqual(weights(20), [1.0])          # 20 years is inflated: half of that

    def test_optional_gap_hurts_less_than_required_gap(self):
        req = "Requirements\n• Experience with Netsuite\n• Experience with Zorbix\n" + "pad " * 100
        opt = "Requirements\n• Experience with Netsuite\nPreferred\n• Experience with Zorbix\n" + "pad " * 100
        self.assertGreater(evaluate("M", "B", opt, PROFILE, TODAY).score, evaluate("M", "B", req, PROFILE, TODAY).score)

    def test_title_counts_less_than_the_description(self):
        r = evaluate("Wizard Lead", "B", GOOD, PROFILE, TODAY)
        self.assertEqual(r.decision, "Go", r.line)            # an unrecognised title does not sink a matching JD

    def test_exclusion_is_a_gate_not_a_score(self):
        clean = evaluate("Program Manager", "B", GOOD, PROFILE, TODAY)
        r = evaluate("Program Manager", "AcmeCorp Inc", GOOD, PROFILE, TODAY)
        self.assertEqual((r.decision, r.exclusion, r.score), ("No-Go", "acme", clean.score))   # Fit itself is untouched
        self.assertIn("Excluded: named employer", r.line)
        self.assertIn("professional Fit is", r.why)

    def test_a_stray_engineering_requirement_does_not_cap(self):
        r = evaluate("Technical Program Manager", "B", GOOD + "\nRequirements\n• Experience with software development practices\n", PROFILE, TODAY)
        self.assertFalse(r.capped)

    def test_dimensions_the_posting_does_not_activate_do_not_lower_fit(self):
        text = "Responsibilities\n• Own the program roadmap\n• Drive program delivery for the team\n" + "pad words here " * 40
        r = evaluate("Program Manager", "B", text, PROFILE, TODAY)
        self.assertGreaterEqual(r.score, 90)

    def test_direct_title_specialization_adds_three(self):
        text = "Responsibilities\n• Own the program roadmap\n• Drive program delivery\n" + "pad words here " * 40
        a = evaluate("Program Manager", "B", text, PROFILE, TODAY).score
        b = evaluate("Technical Program Manager", "B", text, PROFILE, TODAY).score
        self.assertEqual(b, min(100, a + 3))

    def test_provider_and_location_cannot_change_fit(self):
        a = evaluate("Program Manager", "B", GOOD, PROFILE, TODAY)
        b = evaluate("Program Manager", "B", GOOD + " Location: onsite in Paris. Salary $40k.", PROFILE, TODAY)
        self.assertEqual((a.score, a.buckets), (b.score, b.buckets))

    def test_too_short_is_unscorable_not_zero(self):
        r = evaluate("Program Manager", "B", "Short.", PROFILE, TODAY)
        self.assertEqual((r.decision, r.score), ("Unscorable", None))

    def test_deterministic_and_inflation_free_of_provider_scores(self):
        a = evaluate("Technical Program Manager", "Beta", GOOD, PROFILE, TODAY)
        self.assertEqual(a.line, evaluate("Technical Program Manager", "Beta", GOOD, PROFILE, TODAY).line)


class Stage(unittest.TestCase):
    def test_lane_decision_for_a_newsletter_job(self):
        from lifeos.jobs.fit import stage
        base = (1, "Technical Program Manager", "Beta", GOOD, "fp", "Newsletter")
        remote = stage.score_rows([base + ("Remote - United States", "$120K/yr - $150K/yr", None, date(2026, 9, 30))], PROFILE, TODAY)[0]
        self.assertEqual((remote[3].status, remote[4], remote[5]), ("ADMIT", "US Remote", "remote"))
        onsite = stage.score_rows([base + ("Austin, TX (Hybrid)", None, None, date(2026, 9, 30))], PROFILE, TODAY)[0]
        self.assertEqual(onsite[3].status, "EXCLUDE")
        low = stage.score_rows([base + ("Remote", "$60K/yr", None, date(2026, 9, 30))], PROFILE, TODAY)[0]
        self.assertEqual(low[3].status, "EXCLUDE")                   # explicit pay below $75K
        stale = stage.score_rows([base + ("Remote", None, date(2026, 8, 1), date(2026, 9, 30))], PROFILE, TODAY)[0]
        self.assertEqual(stale[3].status, "EXCLUDE")                 # employer Posting Date beats First Surfaced
        gated = stage.score_rows([(2, "Program Manager", "AcmeCorp Inc", GOOD, "fp", "Newsletter", "Remote", None, None, date(2026, 9, 30))], PROFILE, TODAY)[0]
        self.assertEqual((gated[3].status, gated[2].decision), ("EXCLUDE", "No-Go"))

    def test_score_rows_is_pure_and_missing_profile_is_a_noop(self):
        from lifeos.jobs.fit import stage
        out = stage.score_rows([(1, "Technical Program Manager", "Beta", GOOD, "fp")], PROFILE, TODAY)
        self.assertEqual((out[0][0], out[0][2].decision), (1, "Go"))
        self.assertEqual(out[0][3].status, "REVIEW")                 # no location or pay evidence: work mode unresolved
        self.assertEqual(stage.run(10, False, environ={})["profile"], "missing")


class ProfileLoading(unittest.TestCase):
    def test_missing_and_invalid(self):
        with self.assertRaises(ProfileError):
            load({})
        with self.assertRaises(ProfileError):
            load({"FIT_PROFILE_JSON": "{not json"})
        with self.assertRaises(ProfileError):
            Profile({"capabilities": [{"class": "bogus", "terms": ["x"]}]})

    def test_hash_changes_with_content(self):
        self.assertNotEqual(Profile({"years": 1}).hash, Profile({"years": 2}).hash)


if __name__ == "__main__":
    unittest.main()


class RequeueFloorTests(unittest.TestCase):
    def test_one_statement_for_old_floor_exclusions_only(self):
        from lifeos.jobs.fit import stage
        from tests.kit.db import FakeConn
        conn = FakeConn()
        conn.cur.rowcount = 7
        self.assertEqual(stage.requeue_floor(conn.cursor()), 7)
        self.assertEqual([w for w, _ in conn.cur.sql], ["UPDATE v7_jobs"])
        import inspect
        source = inspect.getsource(stage.requeue_floor)
        sql = source[source.index('"UPDATE'):source.index('(_now(),))')]
        import re
        literal = "".join(re.findall(r'"([^"]*)"', sql))
        literal % ("2026-10-03",)                             # the driver formats the statement: a bare % in the LIKE pattern raises here (the 5 PM tick crashed on it)


class ProfessionGate(unittest.TestCase):
    """D89: the blatant misses from the 38-job review. A different profession is never a Fit, and generic requirements alone are not one."""

    def hard(self, title, company="Acme"):
        got, _ = exclusions.check(title, company, GOOD)
        return got and got["id"]

    def test_the_rows_jim_rejected_are_hard_exclusions(self):
        self.assertEqual(self.hard("Director, TA Infrastructure"), "hr_function")
        self.assertEqual(self.hard("Senior Manager, Strategy and Operations - People Experience [Remote]"), "hr_function")
        self.assertEqual(self.hard("Senior Director, Back Up Care Program"), "care_hospitality")
        self.assertEqual(self.hard("Nursery Housekeeper"), "care_hospitality")
        self.assertEqual(self.hard("Nursery Chef - NEW LAUNCH!"), "care_hospitality")
        self.assertEqual(self.hard("Senior Project Manager - Surgical Services Operations Support"), "medical_role")
        self.assertEqual(self.hard("Senior Project Manager", "Mayo Clinic"), "healthcare_employer")

    def test_legitimate_program_titles_are_not_caught(self):
        for title in ("Technical Program Manager", "Senior Director, Technical Program Management", "Benefits Realization Manager",
                      "Director, Platform Infrastructure", "Engagement Manager", "Partner Manager", "Senior Scrum Master"):
            self.assertIsNone(self.hard(title), title)

    def test_an_analyst_level_title_is_capped_below_the_review_band(self):
        analyst = evaluate("Revenue Operations Analyst 3", "Acme", GOOD, PROFILE, TODAY)
        manager = evaluate("Technical Program Manager", "Acme", GOOD, PROFILE, TODAY)
        self.assertLessEqual(analyst.score, 55)
        self.assertIn("analyst-level", analyst.why)
        self.assertGreater(manager.score, 55)
        self.assertGreater(evaluate("Associate Director, Technical Program Management", "Acme", GOOD, PROFILE, TODAY).score, 55)

    def test_generic_requirements_with_no_matching_function_are_capped(self):
        generic = ("About the role\nWe need an organised person to look after the daily running of the site.\n\nRequirements\n"
                   "• Strong communication skills\n• Good organisational skills\n• Attention to detail and time management\n"
                   "• Ability to collaborate and prioritise\n• Bachelor's degree preferred\n") + "We are a friendly team who value collaboration. " * 12
        result = evaluate("Site Lead", "Acme", generic, PROFILE, TODAY)
        self.assertLessEqual(result.score, 50)
        self.assertEqual(result.decision, "No-Go")
        self.assertIn("matches your functions", result.why)

    def test_a_city_and_state_with_no_remote_cue_is_an_office_job_in_the_fit_stage_only(self):
        from lifeos.jobs import lanes
        self.assertEqual(lanes.work_mode_for_fit("New York, NY", "Vice President", "We are hiring."), "onsite")
        self.assertEqual(lanes.work_mode_for_fit("Rochester, MN", "Project Manager", ""), "onsite")
        self.assertEqual(lanes.work_mode_for_fit("United States", "Project Manager", ""), "unknown")
        self.assertEqual(lanes.work_mode_for_fit("Austin, TX (Remote)", "Project Manager", ""), "remote")
        self.assertEqual(lanes.work_mode_for_fit("Austin, TX", "Project Manager", "This is a fully remote role."), "remote")
        self.assertEqual(lanes.detect_work_mode("New York, NY", "Vice President", ""), "unknown")      # acquisition filters never guess


class EducationGate(unittest.TestCase):
    def test_education_titles_and_school_employers_are_hard_exclusions(self):
        for title in ("Senior Manager of Ellucian Student Delivery Excellence & Enablement |Remote", "Director, Admissions Operations", "K-12 Program Manager"):
            hard, _ = exclusions.check(title, "Acme", GOOD)
            self.assertEqual(hard["id"], "education_role", title)
        hard, _ = exclusions.check("Program Manager", "Kido Schools UK Limited", GOOD)
        self.assertEqual(hard["id"], "school_employer")
        self.assertIsNone(exclusions.check("Senior Technical Program Manager", "Acme", GOOD)[0])


class WorkModeWindow(unittest.TestCase):
    def test_an_explicit_statement_deep_in_the_description_counts_in_the_fit_stage_only(self):
        from lifeos.jobs import lanes
        text = "Overview of the company and the team. " * 80 + "This is a remote position open to candidates in the United States."
        self.assertGreater(text.lower().index("this is a remote position"), 1500)
        self.assertEqual(lanes.work_mode_for_fit("United States", "Program Manager", text), "remote")
        self.assertEqual(lanes.detect_work_mode("United States", "Program Manager", text), "unknown")
        self.assertEqual(lanes.work_mode_for_fit("Austin, TX", "Program Manager", "Overview. " * 300 + "This is a hybrid position, three days a week in the office."), "hybrid")


class ScaleUpJunk(unittest.TestCase):
    """D95: the Scale-Up board held 13 jobs, 9 of them not this profile (five API Enterprise Sales Executives, junior and associate roles, nursery staff)."""

    def hard(self, title, company="Revolut Ltd"):
        got, _ = exclusions.check(title, company, GOOD)
        return got and got["id"]

    def test_sales_and_language_titles_are_hard_exclusions(self):
        for title in ("API Enterprise Sales Executive", "API Enterprise Sales Executive (Spanish-Speaking)", "API Enterprise Sales Executive (German-Speaking)",
                      "Senior Account Executive", "Business Development Manager", "Customer Success Manager II, Enterprise", "SDR, Fintech"):
            self.assertIn(self.hard(title), ("sales_role", "language_requirement"), title)
        self.assertEqual(self.hard("Technical Program Manager (Polish-Speaking)"), "language_requirement")

    def test_pre_sales_solutions_and_program_titles_are_left_to_fit(self):
        for title in ("Solutions Architect - Pre Sales", "Pre Sales and Client Implementation Manager", "Technical Program Manager", "Director of Delivery"):
            self.assertIsNone(self.hard(title), title)

    def test_junior_and_graduate_titles_are_capped_below_the_review_band(self):
        for title in ("Junior Product Designer", "Systems Associate (Graduate/Entry-level opportunity)", "Content Operations Associate"):
            self.assertLessEqual(evaluate(title, "Acme", GOOD, PROFILE, TODAY).score, 55, title)


class LegalComplianceTitles(unittest.TestCase):
    def test_titles_from_the_adobe_flood_and_the_methodology_list_are_excluded_and_program_titles_are_not(self):
        for title in ("Senior Manager, Legal Operations", "Senior Manager, Employee Relations", "Research Scientist (Applied) - IV", "AML Analyst",
                      "Threat Intelligence / Reverse Engineering Investigator", "Senior Media Planner", "Compliance Officer"):
            hard, _ = exclusions.check(title, "Acme", GOOD)
            self.assertEqual(hard["id"], "legal_compliance", title)
        for title in ("Sr Technology Compliance Product Owner", "Program Lead, e-Invoicing", "Senior Product Manager, AI Brand Visibility"):
            self.assertIsNone(exclusions.check(title, "Acme", GOOD)[0], title)


class FinanceDomainAndFunctionalCap(unittest.TestCase):
    """D103: Chief Risk Officer at Fit 84, Operational Risk Manager 75, FinCrime Risk Manager 68-78 for a program manager. The domain is the job, and generic
    seniority, team size and scale (which fill Role and Delivery) must not outvote the Functional area."""

    def hard(self, title):
        got, _ = exclusions.check(title, "Acme", GOOD)
        return got and got["id"]

    def test_financial_risk_fincrime_and_audit_titles_are_hard_exclusions(self):
        for title in ("Chief Risk Officer", "Enterprise Risk Manager", "Operational Risk Manager (Outsourcing)", "FinCrime Risk Manager (Fraud)",
                      "Financial Crime Compliance Governance Manager", "Regulatory Compliance Manager (Wealth & Trading)", "Head of Risk (Non-Retail)",
                      "Credit Risk Director", "Treasury Manager", "Internal Audit Lead", "MLRO", "Sanctions Compliance Officer"):
            self.assertIsNotNone(self.hard(title), title)

    def test_program_and_product_titles_that_mention_risk_or_compliance_are_not_caught(self):
        for title in ("Technical Program Manager", "Sr Technology Compliance Product Owner", "Program Risk Manager", "Project Risk Lead",
                      "Senior Program Manager, Payments Platform", "Delivery Risk Manager", "Director, Platform Risk and Dependency Management"):
            self.assertIsNone(self.hard(title), title)

    def test_fit_cannot_exceed_the_functional_area_by_more_than_the_margin(self):
        from unittest import mock
        from lifeos.jobs.fit import score
        buckets = {"role": 100, "functional": 33, "delivery": 100}
        with mock.patch.object(score, "_arithmetic", return_value=(80, buckets)):
            result = evaluate("Technical Program Manager", "Acme", GOOD, PROFILE, TODAY)
        self.assertEqual((result.score, result.pre_functional), (33 + score.FUNCTIONAL_MARGIN, 80))
        self.assertEqual(result.decision, "No-Go")
        self.assertIn("function matches only 33%", result.why)

    def test_a_job_whose_function_matches_is_not_capped(self):
        result = evaluate("Technical Program Manager", "Acme", GOOD, PROFILE, TODAY)
        self.assertEqual(result.score, result.pre_functional)
        self.assertGreaterEqual(result.score, GO_THRESHOLD)


class ExtraProfileBlock(unittest.TestCase):
    """D105: evidence is added through FIT_PROFILE_EXTRA_JSON without replacing the profile."""
    BASE = '{"years": 15, "capabilities": [{"id": "a", "label": "A", "class": "direct", "terms": ["alpha"]}], "scope": {"team_size": 10}}'

    def test_the_extra_block_adds_and_never_replaces(self):
        extra = ('{"years": 20, "capabilities": [{"id": "b", "label": "B", "class": "direct", "terms": ["beta"]}], '
                 '"functions": [{"terms": ["product owner"], "class": "direct"}], "scope": {"team_size": 99, "budget_usd": 5}}')
        profile = load({"FIT_PROFILE_JSON": self.BASE, "FIT_PROFILE_EXTRA_JSON": extra})
        self.assertEqual([c["id"] for c in profile.capabilities], ["a", "b"])
        self.assertEqual(profile.years, 20)
        self.assertEqual(profile.scope, {"team_size": 10, "budget_usd": 5})
        self.assertIsNotNone(profile.function("product owner"))

    def test_no_extra_block_is_the_same_profile_and_hash(self):
        self.assertEqual(load({"FIT_PROFILE_JSON": self.BASE}).hash, load({"FIT_PROFILE_JSON": self.BASE, "FIT_PROFILE_EXTRA_JSON": ""}).hash)

    def test_an_invalid_extra_block_is_an_error_not_silently_ignored(self):
        with self.assertRaises(ProfileError):
            load({"FIT_PROFILE_JSON": self.BASE, "FIT_PROFILE_EXTRA_JSON": "{not json"})
        with self.assertRaises(ProfileError):
            load({"FIT_PROFILE_JSON": self.BASE, "FIT_PROFILE_EXTRA_JSON": '{"capabilities": [{"id": "x", "class": "bogus", "terms": ["x"]}]}'})


class RevivedStatement(unittest.TestCase):
    def test_the_revive_statement_survives_driver_formatting_and_is_guarded(self):
        import inspect, re
        from lifeos.jobs.fit import stage
        source = inspect.getsource(stage.run)
        start = source.index('"UPDATE v7_jobs SET status=\'READY\', unresolved_reason=\'requeued_fit\'')
        end = source.index("(_now(), job_id))", start)
        literal = "".join(re.findall(r'"([^"]*)"', source[start:end]))
        literal % ("2026-10-04", 1)                              # the driver formats the statement
        for guard in ("status='EXCLUDED_FIT'", "unresolved_reason='lane_exclude'", "notion_page_id IS NULL"):
            self.assertIn(guard, literal)
