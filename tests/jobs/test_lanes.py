"""The lane policy matrix (docs/LANES.md): one qualify() over data-driven policies."""
from datetime import date, timedelta
import unittest

from lifeos.jobs import lanes
from lifeos.jobs.lanes import ADMIT, DISABLED, EXCLUDE, NEGATIVE, POLICIES, POSITIVE, REVIEW, UNRESOLVED, Facts, qualify

TODAY = date(2026, 10, 1)
US, SCALE, SW = POLICIES["US Remote"], POLICIES["Scale-Up"], POLICIES["Skilled Worker"]


def ago(days):
    return TODAY - timedelta(days=days)


def us(**kw):
    return Facts(**{"fit": 80, "market": "US", "work_mode": "remote", "posted": ago(1), **kw})


def uk(**kw):
    return Facts(**{"fit": 80, "market": "UK", "work_mode": "onsite", "posted": ago(2),
                    "route": {"Scale-up": POSITIVE}, "geography": POSITIVE, **kw})


class UsRemote(unittest.TestCase):
    def test_admit_and_the_gates(self):
        self.assertEqual(qualify(US, us(), TODAY).status, ADMIT)
        self.assertEqual(qualify(US, us(work_mode="onsite"), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(US, us(work_mode="hybrid"), TODAY).status, EXCLUDE)
        self.assertEqual((qualify(US, us(work_mode="unknown"), TODAY).status, qualify(US, us(work_mode="unknown"), TODAY).reason), (ADMIT, "remote assumed"))   # D204: presumed, recorded as such
        self.assertEqual(qualify(US, us(pay_min=74_999, pay_currency="$"), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(US, us(pay_min=75_000, pay_currency="$"), TODAY).status, ADMIT)
        self.assertEqual(qualify(US, us(pay_min=70_000, pay_currency="$"), TODAY).reason, "explicit pay below $75,000")   # the documented reason
        self.assertEqual(qualify(US, us(pay_min=None), TODAY).status, ADMIT)               # missing pay is allowed

    def test_freshness_is_14_days_and_unknown_is_review(self):
        self.assertEqual(qualify(US, us(posted=ago(14)), TODAY).status, ADMIT)
        self.assertEqual(qualify(US, us(posted=ago(15)), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(US, us(posted=None), TODAY).status, REVIEW)


class ScaleUp(unittest.TestCase):
    def test_the_liberal_lane(self):
        self.assertEqual(qualify(SCALE, uk(pay_min=39_999, pay_currency="£"), TODAY).status, EXCLUDE)       # D87: Scale-Up has a £40,000 floor
        self.assertEqual(qualify(SCALE, uk(pay_min=39_999, pay_currency="£"), TODAY).reason, "explicit pay below £40,000")
        for case in (uk(pay_min=40_000, pay_currency="£"),      # at the floor is fine
                     uk(pay_min=None),                           # missing pay
                     uk(work_mode="hybrid"), uk(work_mode="onsite"), uk(work_mode="remote"),
                     uk(posted=ago(29)), uk(posted=ago(30)),
                     uk(posted=None)):                           # a missing date does not suppress
            self.assertEqual(qualify(SCALE, case, TODAY).status, ADMIT, case)

    def test_age_gate_is_30_days_by_decision(self):
        self.assertEqual(qualify(SCALE, uk(posted=ago(31)), TODAY).status, EXCLUDE)

    def test_route_and_geography_evidence(self):
        self.assertEqual(qualify(SCALE, uk(route={}), TODAY).status, REVIEW)                       # unresolved
        self.assertEqual(qualify(SCALE, uk(route={"Scale-up": UNRESOLVED}), TODAY).status, REVIEW)
        self.assertEqual(qualify(SCALE, uk(route={"Scale-up": NEGATIVE}), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(SCALE, uk(geography=UNRESOLVED), TODAY).status, REVIEW)
        self.assertEqual(qualify(SCALE, uk(geography=NEGATIVE), TODAY).status, EXCLUDE)

    def test_fit_and_closure(self):
        self.assertEqual(qualify(SCALE, uk(fit=67), TODAY).status, REVIEW)       # perfect route cannot rescue Fit; below the floor is Review (D81)
        self.assertEqual(qualify(SCALE, uk(closed=True), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(SCALE, uk(fit=None), TODAY).status, REVIEW)       # unscorable is review, not 0


class SkilledWorker(unittest.TestCase):
    def test_the_policy(self):
        on, sw = SW, {"Skilled Worker": POSITIVE}
        self.assertTrue(SW.enabled)
        self.assertEqual(qualify(on, uk(route=sw), TODAY).status, ADMIT)
        self.assertEqual(qualify(on, uk(route=sw, pay_min=50_000, pay_currency="£"), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(on, uk(route={}), TODAY).status, REVIEW)                           # sponsor unresolved
        self.assertEqual(qualify(on, uk(route=sw, posted=None), TODAY).status, REVIEW)
        self.assertEqual(qualify(on, uk(route=sw, market=None), TODAY).status, REVIEW)              # never assume the job is in the UK
        self.assertEqual(qualify(on, uk(route=sw, geography=UNRESOLVED), TODAY).status, REVIEW)      # UK-remote / unplaced
        self.assertEqual(qualify(on, uk(route=sw, geography=NEGATIVE), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(on, uk(route={"Skilled Worker": NEGATIVE}), TODAY).status, EXCLUDE)  # employer not on the register
        self.assertEqual(qualify(on, uk(route=sw, posted=ago(20)), TODAY).status, EXCLUDE)            # 14 days, unlike Scale-Up


class Routing(unittest.TestCase):
    def test_a_uk_newsletter_job_at_a_sponsor_goes_to_skilled_worker(self):
        facts = uk(route={"Skilled Worker": POSITIVE})
        decision, lane, eligible = lanes.decide_all("Newsletter", facts, TODAY)
        self.assertEqual((decision.status, lane, eligible), (ADMIT, "Skilled Worker", ["Skilled Worker"]))

    def test_a_us_job_stays_us_remote_and_a_dual_route_is_scale_up(self):
        self.assertEqual(lanes.decide_all("Newsletter", us(), TODAY)[1:], ("US Remote", ["US Remote"]))
        both = uk(route={"Scale-up": POSITIVE, "Skilled Worker": POSITIVE})
        self.assertEqual(lanes.decide_all("Scale-Up", both, TODAY)[1:], ("Scale-Up", ["Scale-Up", "Skilled Worker"]))
        self.assertEqual(lanes.decide_all("Skilled Worker", both, TODAY)[1:], ("Scale-Up", ["Scale-Up", "Skilled Worker"]))

    def test_nothing_admits_keeps_the_own_lane_decision(self):
        decision, lane, eligible = lanes.decide_all("Newsletter", uk(route={}), TODAY)
        self.assertEqual((decision.status, lane, eligible), (EXCLUDE, "US Remote", []))               # a UK job is not a US Remote job

    def test_route_strings_merge(self):
        self.assertEqual(lanes.route_dict("Scale-up:POSITIVE;Skilled Worker:NEGATIVE"), {"Scale-up": "POSITIVE", "Skilled Worker": "NEGATIVE"})
        self.assertEqual(lanes.join_routes("Scale-up:POSITIVE", **{"Skilled Worker": "POSITIVE"}), "Scale-up:POSITIVE;Skilled Worker:POSITIVE")


class CrossLane(unittest.TestCase):
    def test_one_fit_floor_for_every_lane(self):
        """D191: ADMIT needs Fit 72 in every active lane; 71 is stored as Review, never admitted on Fit grounds."""
        skilled = lambda **kw: uk(route={"Skilled Worker": POSITIVE}, **kw)
        for policy, facts in ((US, us), (SCALE, uk), (SW, skilled)):
            for fit in (60, 67, 68, 71):
                decision = qualify(policy, facts(fit=fit), TODAY)
                self.assertEqual((decision.status, decision.reason), (REVIEW, f"Fit {fit} below 72"), (policy.name, fit))
            for fit in (72, 73, 100):
                self.assertEqual(qualify(policy, facts(fit=fit), TODAY).status, ADMIT, (policy.name, fit))
            self.assertEqual(qualify(policy, facts(fit=None), TODAY).reason, "Fit unscorable")       # no invented number
        from lifeos.jobs import fit
        self.assertEqual((lanes.FIT_FLOOR, fit.UNIVERSAL_FIT_FLOOR, fit.GO_THRESHOLD), (72, 72, 72))       # one value: the lanes and the Go / No-Go bar cannot drift apart

    def test_a_clear_score_never_overrides_an_independent_gate(self):
        self.assertEqual(qualify(US, us(fit=95, closed=True), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(US, us(fit=95, work_mode="onsite"), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(US, us(fit=95, market="UK"), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(US, us(fit=95, pay_min=50_000, pay_currency="$"), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(US, us(fit=72, work_mode="unknown"), TODAY).status, ADMIT)                # D204: no office requirement is not an open question

    def test_target_does_not_lower_fit(self):
        self.assertEqual(SCALE.bucket, "Target")
        self.assertEqual(qualify(SCALE, uk(fit=67), TODAY).status, REVIEW)

    def test_pay_in_the_wrong_currency_is_not_compared(self):
        self.assertEqual(qualify(US, us(pay_min=50_000, pay_currency="£"), TODAY).status, ADMIT)

    def test_dual_route_is_one_job_and_scale_up_is_visible(self):
        results, visible = lanes.qualify_all(uk(route={"Scale-up": POSITIVE, "Skilled Worker": POSITIVE}), TODAY)
        self.assertEqual(visible, "Scale-Up")
        self.assertEqual(results["Skilled Worker"].status, ADMIT)          # both routes preserved on the one job

    def test_market_mismatch_excludes(self):
        self.assertEqual(qualify(US, us(market="UK"), TODAY).status, EXCLUDE)


class Parsing(unittest.TestCase):
    def test_pay(self):
        self.assertEqual(lanes.parse_pay("$110K/yr - $153K/yr"), (110_000, "$"))
        self.assertEqual(lanes.parse_pay("£65,000 - £80,000 a year"), (65_000, "£"))
        self.assertEqual(lanes.parse_pay("$50/hr"), (104_000, "$"))
        self.assertEqual(lanes.parse_pay("competitive salary"), (None, None))
        self.assertEqual(lanes.parse_pay("$5"), (None, None))

    def test_geography_and_route_evidence(self):
        self.assertEqual(lanes.geography_status("London, UK"), POSITIVE)
        self.assertEqual(lanes.geography_status("Manchester"), NEGATIVE)
        self.assertEqual(lanes.geography_status("Remote"), UNRESOLVED)
        self.assertEqual(lanes.route_dict("Scale-up:POSITIVE"), {"Scale-up": POSITIVE})
        self.assertEqual(lanes.route_dict(None), {})
        facts = lanes.facts_for(80, "Program Manager", "London", "text", None, None, TODAY, route="Scale-up:POSITIVE")
        self.assertEqual(lanes.decide("Scale-Up", facts, TODAY)[0].status, ADMIT)        # route + geography positive, any work mode
        self.assertEqual(lanes.decide("Scale-Up", lanes.facts_for(80, "PM", "London", "t", None, None, TODAY), TODAY)[0].status, REVIEW)

    def test_work_mode(self):
        self.assertEqual(lanes.detect_work_mode("Remote - United States"), "remote")
        self.assertEqual(lanes.detect_work_mode("Austin, TX (Hybrid)"), "hybrid")
        self.assertEqual(lanes.detect_work_mode("London", "", "This is a fully remote position"), "remote")
        self.assertEqual(lanes.detect_work_mode("Denver, CO", "", "On-site role"), "onsite")
        self.assertEqual(lanes.detect_work_mode("Denver, CO"), "unknown")


if __name__ == "__main__":
    unittest.main()


class MarketTests(unittest.TestCase):
    def test_market_detection(self):
        for text, market in (("Remote - United States", "US"), ("Austin, TX", "US"), ("London, UK", "UK"), ("Manchester", "UK"),
                             ("Toronto, ON", "OTHER"), ("Berlin, Germany", "OTHER"), ("Remote", None), ("", None), ("US or UK", None),
                             ("London, ON", "OTHER"), ("Remote, Europe", "OTHER")):
            with self.subTest(text):
                self.assertEqual(lanes.market_of(text), market)

    def test_us_remote_excludes_a_uk_posting_and_scale_up_a_us_one(self):
        facts = lanes.facts_for(80, "Program Manager", "London, UK", "remote", None, None, date(2026, 10, 1))
        self.assertEqual(lanes.qualify(lanes.POLICIES["US Remote"], facts, date(2026, 10, 1)).status, lanes.EXCLUDE)
        facts = lanes.facts_for(80, "Program Manager", "Remote", "remote", None, date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(lanes.qualify(lanes.POLICIES["US Remote"], facts, date(2026, 10, 1)).status, lanes.ADMIT)


class ReviewBand(unittest.TestCase):
    def test_60_to_71_is_review_and_below_60_is_excluded(self):
        for policy, facts in ((US, us), (SCALE, uk)):
            self.assertEqual(qualify(policy, facts(fit=59), TODAY).status, EXCLUDE)
            self.assertEqual(qualify(policy, facts(fit=60), TODAY).status, REVIEW)
            self.assertEqual(qualify(policy, facts(fit=67), TODAY).status, REVIEW)
            self.assertEqual(qualify(policy, facts(fit=71), TODAY).status, REVIEW)
            self.assertEqual(qualify(policy, facts(fit=72), TODAY).status, ADMIT)


class LondonAndRemote(unittest.TestCase):
    """D96: Scale-Up needs London, not remote; US Remote needs remote."""

    def facts(self, location, mode, fit=80):
        return lanes.Facts(fit=fit, market="UK", work_mode=mode, posted=ago(2), route={"Scale-up": POSITIVE},
                           geography=lanes.geography_status(location), located=bool(location.strip()))

    def test_remote_never_disqualifies_a_london_scale_up_job(self):
        for location, mode in (("London", "onsite"), ("London (hybrid)", "hybrid"), ("London (Remote)", "remote"), ("Remote - London, UK", "remote"),
                               ("Remote: UK", "remote"), ("Remote - United Kingdom", "remote"), ("Remote: England", "remote"), ("UK Remote", "remote"),
                               ("Barcelona · office · Spain | London · office · United Kingdom | Madrid · office · Spain", "onsite")):
            self.assertEqual(qualify(SCALE, self.facts(location, mode), TODAY).status, ADMIT, location)

    def test_a_scale_up_job_outside_london_is_excluded_whether_or_not_it_is_remote(self):
        for location, mode in (("Manchester", "onsite"), ("Remote - Ireland", "remote"), ("Edinburgh, Scotland", "hybrid"), ("Leeds (Remote)", "remote"), ("Remote - EMEA", "remote")):
            decision = qualify(SCALE, self.facts(location, mode), TODAY)
            self.assertEqual((decision.status, decision.reason), (EXCLUDE, "not in London"), location)

    def test_a_scale_up_job_with_no_place_stays_review(self):
        self.assertEqual(qualify(SCALE, self.facts("", "unknown"), TODAY).status, REVIEW)

    def test_a_us_job_must_still_be_remote(self):
        self.assertEqual(qualify(US, us(work_mode="remote"), TODAY).status, ADMIT)
        self.assertEqual(qualify(US, us(work_mode="onsite"), TODAY).reason, "not remote: onsite required")
        self.assertEqual(qualify(US, us(work_mode="hybrid", work_evidence="3 days a week in the office"), TODAY).reason, 'not remote: hybrid required ("3 days a week in the office")')


class TwoDoubts(unittest.TestCase):
    """D100: the 6 PM tick published 40 Adobe near-miss jobs with an unknown work mode as Review. One doubt is a question; two doubts on a weak fit is a miss."""

    def test_a_near_miss_fit_with_another_unresolved_fact_is_excluded(self):
        self.assertEqual(qualify(US, us(fit=63, posted=None), TODAY).status, EXCLUDE)               # posting date unresolved
        decision = qualify(US, us(fit=66, work_mode="unknown"), TODAY)                               # D204: an unstated work mode is no longer a doubt
        self.assertEqual((decision.status, decision.reason), (REVIEW, "Fit 66 below 72"))

    def test_one_doubt_is_still_review(self):
        self.assertEqual(qualify(US, us(fit=66), TODAY).status, REVIEW)                                  # near-miss alone
        self.assertEqual(qualify(US, us(fit=75, work_mode="unknown"), TODAY).status, ADMIT)              # D204: clear fit, remote presumed
        self.assertEqual(qualify(US, us(fit=68, work_mode="unknown"), TODAY).status, REVIEW)             # Fit below 72 is the one doubt
        self.assertEqual(qualify(US, us(fit=71, work_mode="unknown"), TODAY).status, REVIEW)
        self.assertEqual(qualify(US, us(fit=67, work_mode="unknown"), TODAY).status, REVIEW)

    def test_nothing_else_changes(self):
        self.assertEqual(qualify(US, us(fit=59, work_mode="unknown"), TODAY).status, EXCLUDE)
        self.assertEqual(qualify(US, us(fit=80), TODAY).status, ADMIT)


class OtherLaneNeedsAPositiveMarket(unittest.TestCase):
    """D102: Revolut's Argentina / Colombia / South Africa / Cyprus remote roles reached the board because the US Remote lane, which only asks for remote, took
    what Scale-Up had rejected; their market is unknown (the market word list does not know those places), and unknown is not US."""

    def facts(self, location):
        return lanes.Facts(fit=77, market=lanes.market_of(location), work_mode="remote", posted=ago(2), route={"Scale-up": POSITIVE},
                           geography=lanes.geography_status(location), located=True)

    def test_a_scale_up_job_outside_london_is_not_taken_by_the_us_lane(self):
        for location in ("Argentina - Remote · remote · Argentina", "Colombia - Remote · remote · Colombia", "South Africa - Remote · remote · South Africa",
                         "Cyprus - Remote · remote · Cyprus", "Switzerland - Remote · remote · Switzerland"):
            decision, lane, eligible = lanes.decide_all("Scale-Up", self.facts(location), TODAY)
            self.assertEqual((decision.status, decision.reason, lane, eligible), (EXCLUDE, "not in London", "Scale-Up", []), location)

    def test_london_and_uk_remote_still_admit(self):
        for location in ("London · office · United Kingdom", "UK - Remote · remote · United Kingdom"):
            self.assertEqual(lanes.decide_all("Scale-Up", self.facts(location), TODAY)[0].status, ADMIT, location)

    def test_a_us_row_is_still_judged_by_its_own_lane(self):
        decision, lane, _ = lanes.decide_all("US Remote", lanes.Facts(fit=80, market=None, work_mode="remote", posted=ago(1)), TODAY)
        self.assertEqual((decision.status, lane), (ADMIT, "US Remote"))

    def test_a_newsletter_uk_sponsor_job_is_still_taken_by_the_uk_lane(self):
        facts = lanes.Facts(fit=80, market="UK", work_mode="onsite", posted=ago(2), route={"Skilled Worker": POSITIVE}, geography=POSITIVE, located=True)
        self.assertEqual(lanes.decide_all("Newsletter", facts, TODAY)[1], "Skilled Worker")


class FirstPartyAge(unittest.TestCase):
    """D111: a job on its employer's own board is open: its posting age is not judged (aggregator and feed jobs keep the age rule)."""

    def test_an_old_posting_on_an_employers_board_is_not_excluded_but_the_same_age_elsewhere_is(self):
        old = uk(posted=ago(90))
        self.assertEqual(qualify(SCALE, old, TODAY).reason, "older than 30 days")
        self.assertEqual(qualify(SCALE, Facts(**{**old.__dict__, "first_party": True}), TODAY).status, ADMIT)
        us_old = us(posted=ago(60))
        self.assertEqual(qualify(US, us_old, TODAY).reason, "older than 14 days")
        self.assertEqual(qualify(US, Facts(**{**us_old.__dict__, "first_party": True}), TODAY).status, ADMIT)

    def test_an_undated_first_party_us_job_is_not_sent_to_review_for_its_date(self):
        undated = Facts(**{**us().__dict__, "posted": None})
        self.assertEqual(qualify(US, undated, TODAY).reason, "posting date unresolved")
        self.assertEqual(qualify(US, Facts(**{**undated.__dict__, "first_party": True}), TODAY).status, ADMIT)

    def test_only_an_employer_board_source_counts_as_first_party(self):
        self.assertTrue(lanes.first_party_source("web:su-wheely-technologies-ltd"))
        self.assertTrue(lanes.first_party_source("web:greenhouse-acme"))
        for source in ("web:openjobs", "jobright", "lensa", "linkedin", "", None):
            self.assertFalse(lanes.first_party_source(source), source)


class KeepListTests(unittest.TestCase):
    """D115: a role on Jim's keep list is never excluded for Fit alone; it goes to Review."""

    def facts(self, fit, kept):
        return lanes.facts_for(fit, "Product Designer (Platform)", "London · office · United Kingdom", "x", None, None, date(2026, 10, 4),
                               route="Scale-up:POSITIVE", first_party=True, kept=kept)

    def test_fit_below_the_review_floor_is_review_when_kept_and_excluded_otherwise(self):
        today = date(2026, 10, 4)
        self.assertEqual(lanes.decide("Scale-Up", self.facts(56, False), today)[0].status, lanes.EXCLUDE)
        decision = lanes.decide("Scale-Up", self.facts(56, True), today)[0]
        self.assertEqual(decision.status, lanes.REVIEW)
        self.assertIn("keep list", decision.reason)

    def test_keep_list_matches_only_the_named_roles(self):
        self.assertTrue(lanes.on_keep_list("Revolut Ltd", "Product Designer (Platform)"))
        self.assertTrue(lanes.on_keep_list("Revolut Ltd", "Partnerships Manager (Lifestyle)"))
        self.assertFalse(lanes.on_keep_list("Revolut Ltd", "Product Designer (Mobile)"))
        self.assertFalse(lanes.on_keep_list("Acme", "Product Designer (Platform)"))

    def test_the_four_revolut_roles_jim_named_are_on_the_list(self):
        for title in ("Credit Manager (Product)", "Product Owner (UX)", "Product Owner (Technical)", "Operations Manager (Revenue)",
                      "Product Owner - UX", "Product Owner, Technical", "Product Owner (User Experience)"):
            self.assertTrue(lanes.on_keep_list("Revolut Ltd", title), title)
        for title in ("Product Owner (Crypto)", "Product Owner", "Credit Manager (Risk)", "Credit Manager", "Credit Risk Manager"):
            self.assertFalse(lanes.on_keep_list("Revolut Ltd", title), title)
        self.assertFalse(lanes.on_keep_list("Acme", "Credit Manager (Product)"))

    def test_only_credit_manager_product_overrides_a_private_title_rule(self):
        self.assertTrue(lanes.keep_overrides_exclusion("Revolut Ltd", "Credit Manager (Product)"))
        for company, title in (("Revolut Ltd", "Credit Manager (Risk)"), ("Revolut Ltd", "Product Owner (UX)"), ("Acme", "Credit Manager (Product)")):
            self.assertFalse(lanes.keep_overrides_exclusion(company, title), (company, title))

    def test_a_hard_exclusion_still_wins(self):
        decision = lanes.decide("Scale-Up", self.facts(80, True), date(2026, 10, 4), exclusion="clinical")[0]
        self.assertEqual(decision.status, lanes.EXCLUDE)


class RemoteDefault(unittest.TestCase):
    """D204 (Jim, Oct 17): remote is presumed unless the employer REQUIRES office attendance; only mandatory words are evidence."""

    def mode(self, text, where="San Diego, CA", title="Technical Program Manager"):
        from lifeos.jobs import lanes
        return lanes.work_mode_evidence(where, title, text)

    def test_mandatory_attendance_excludes_with_the_employers_words(self):
        self.assertEqual(self.mode("You must work onsite at our Austin office."), ("onsite", "must work onsite"))
        self.assertEqual(self.mode("This is an on-site role in Seattle."), ("onsite", "This is an on-site"))
        self.assertEqual(self.mode("We work 3 days a week in the office."), ("hybrid", "3 days a week in the office"))
        self.assertEqual(self.mode("Hybrid schedule with your team."), ("hybrid", "Hybrid schedule"))
        self.assertEqual(self.mode("", where="Hybrid - New York, NY"), ("hybrid", "Hybrid"))
        self.assertEqual(self.mode("This is not a remote position."), ("onsite", "not a remote position"))

    def test_optional_negated_or_incidental_office_words_are_not_requirements(self):
        for text in ("There is no onsite requirement for this role.", "Optional office access in Denver.", "Occasional travel to our NYC office for planning.",
                     "Lead onsite customer visits twice a quarter.", "You are not required to be in the office.", "This is not a hybrid role.",
                     "Coordinate with onsite and remote teams."):
            with self.subTest(text=text):
                self.assertEqual(self.mode(text)[0], "unknown")

    def test_a_named_city_with_no_requirement_is_presumed_remote_not_office(self):
        from lifeos.jobs import lanes
        self.assertEqual(lanes.work_mode_for_fit("New York, NY", "Vice President", "We are hiring."), "unknown")
        self.assertEqual(qualify(US, us(work_mode=lanes.work_mode_for_fit("Rochester, MN", "Project Manager", "")), TODAY).reason, "remote assumed")

    def test_remote_offered_alongside_hybrid_is_remote_and_a_stated_remote_is_verified(self):
        self.assertEqual(self.mode("", where="Remote or Hybrid - Chicago, IL")[0], "remote")
        self.assertEqual(self.mode("This is a fully remote role.")[0], "remote")
        self.assertIsNone(qualify(US, us(work_mode="remote"), TODAY).reason)                            # verified remote carries no "assumed" reason

    def test_a_non_us_market_still_excludes_whatever_the_work_mode(self):
        self.assertEqual(qualify(US, us(work_mode="unknown", market="UK"), TODAY).status, EXCLUDE)


class ContradictoryWorkMode(unittest.TestCase):
    """M3 review (D204): a mandatory attendance requirement wins over any remote cue it contradicts; a genuine remote option, an optional or
    occasional visit, customer-site work and a condition for local candidates only are not requirements."""

    def mode(self, text, where="Remote - US"):
        from lifeos.jobs import lanes
        return lanes.work_mode_evidence(where, "Technical Program Manager", text)

    def test_a_required_attendance_beats_a_remote_place_a_remote_statement_and_a_remote_tag(self):
        self.assertEqual(self.mode("You must work onsite at our Chicago office."), ("onsite", "must work onsite"))
        self.assertEqual(self.mode("This is a hybrid role: 3 days a week in the office."), ("hybrid", "hybrid role"))
        self.assertEqual(self.mode("This is a fully remote role. You must work onsite at our Chicago office.", "Chicago, IL"), ("onsite", "must work onsite"))
        self.assertEqual(self.mode("#LI-Remote You must work onsite at our Chicago office.", "Chicago, IL"), ("onsite", "must work onsite"))
        self.assertEqual(self.mode("Remote work is not available for this role."), ("onsite", "Remote work is not available"))

    def test_a_recruiters_hybrid_or_onsite_tag_beats_a_remote_place(self):
        self.assertEqual(self.mode("Great team. #LI-Hybrid"), ("hybrid", "#LI-Hybrid"))
        self.assertEqual(self.mode("Great team. #LI-Onsite"), ("onsite", "#LI-Onsite"))
        self.assertEqual(self.mode("Fully remote. #LI-Remote", "Chicago, IL"), ("remote", None))

    def test_negated_optional_occasional_and_conditional_attendance_keeps_the_remote_place(self):
        for text in ("There is no onsite requirement.", "You will be expected to be onsite for quarterly planning.",
                     "You will need to work in the office occasionally.", "You may need to be onsite at customer sites.",
                     "If you live within 50 miles of our Chicago office, you will need to be in the office two days a week.",
                     "If you live near Chicago, you must work onsite 2 days a week. Otherwise this role is fully remote.",
                     "Our office is not required; we offer onsite 3 days a week for those who want it.",
                     "You have the option to work in the office 2 days a week.", "This is not a hybrid role.  #li-remote"):
            with self.subTest(text=text):
                self.assertEqual(self.mode(text), ("remote", None))

    def test_the_same_qualifiers_leave_an_unstated_place_presumed_not_verified(self):
        for text in ("You will need to work in the office occasionally.", "If you are local, you are expected to be onsite 2 days a week."):
            with self.subTest(text=text):
                self.assertEqual(self.mode(text, "Seattle, WA"), ("unknown", None))

    def test_remote_offered_alongside_hybrid_stays_remote_and_a_hybrid_place_beats_a_remote_claim(self):
        self.assertEqual(self.mode("", "Remote or Hybrid - Chicago, IL"), ("remote", None))
        self.assertEqual(self.mode("This is a fully remote role.", "Hybrid - Austin, TX"), ("hybrid", "Hybrid"))

    def test_a_contradicted_remote_place_is_excluded_by_the_lane_with_the_employers_words(self):
        from lifeos.jobs import lanes
        mode, words = self.mode("You must work onsite at our Chicago office.")
        decision = qualify(US, us(work_mode=mode, work_evidence=words), TODAY)
        self.assertEqual((decision.status, decision.reason), (EXCLUDE, 'not remote: onsite required ("must work onsite")'))
        self.assertEqual(lanes.detect_work_mode("Remote - US", "", "Great team. #LI-Hybrid"), "hybrid")


class FirstPartyUsEligibility(unittest.TestCase):
    """M5 review: an employer board lists the world, so a board job's US eligibility must be shown by its place (a US city or state) or the
    employer's own statement; remote alone is not US eligibility. Unknown is Review, never Admit. Newsletter rows are unchanged."""

    def decide(self, where, text="", first_party=True):
        from lifeos.jobs import lanes
        facts = lanes.facts_for(80, "Technical Program Manager", where, text, None, ago(1), None, first_party=first_party)
        got = qualify(US, facts, TODAY)
        return got.status, got.reason, facts.market

    def test_a_us_city_or_state_or_a_stated_us_eligibility_is_us(self):
        for where, text in (("San Jose", ""), ("Phoenix", ""), ("UT - Cottonwood Heights; FL - Jacksonville", ""), ("Palo Alto, California", ""),
                            ("Remote", "You can be based anywhere in the United States."), ("Remote", "Candidates must be authorized to work in the US."),
                            ("Remote", "This is a US-based remote role.")):
            with self.subTest(where=where, text=text):
                self.assertEqual(self.decide(where, text)[::2], (ADMIT, "US"))

    def test_remote_alone_a_region_or_an_unknown_place_is_review_not_admit(self):
        for where, text in (("Remote", ""), ("Remote, North America", ""), ("Americas", ""), ("RWC HQ", ""), ("Newcastle", ""),
                            ("Remote", "We serve US customers across the world."), ("Remote", "Fully remote team.")):
            with self.subTest(where=where, text=text):
                self.assertEqual(self.decide(where, text), (REVIEW, "market unresolved", None))

    def test_a_named_foreign_market_still_excludes(self):
        self.assertEqual(self.decide("Remote - Germany")[0], EXCLUDE)

    def test_a_newsletter_row_with_no_place_keeps_its_existing_decision(self):
        self.assertEqual(self.decide("Remote", first_party=False)[:2], (ADMIT, None))


class OpenApplication(unittest.TestCase):
    """LIFE-41 / LIFE-59: an unsolicited or general application is not a vacancy; no lane admits it, whatever its Fit."""

    def test_open_application_titles(self):
        for title in ("Initiativbewerbung", "Initiativbewerbung (m/w/d)", "Spontanbewerbung", "Open Application", "Unsolicited application",
                      "General Application - Engineering", "Speculative Application", "Join our Talent Pool", "Candidature spontanée"):
            self.assertTrue(lanes.open_application(title), title)
        for title in ("Technical Program Manager", "Application Engineer", "Program Manager, Applications", "Talent Acquisition Partner"):
            self.assertFalse(lanes.open_application(title), title)

    def test_no_lane_admits_an_open_application(self):
        facts = us(open_application=True)
        for policy in (US, SCALE, SW):
            decision = qualify(policy, facts, TODAY)
            self.assertEqual((decision.status, decision.reason), (EXCLUDE, "not a vacancy: open application"))
        self.assertEqual(lanes.public_reason("not a vacancy: open application"), "not_a_vacancy")
        self.assertEqual(lanes.facts_for(80, "Initiativbewerbung", "Remote", "", None, None, TODAY).open_application, True)
