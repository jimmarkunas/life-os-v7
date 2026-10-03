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
        self.assertEqual(qualify(US, us(work_mode="unknown"), TODAY).status, REVIEW)      # never guessed
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
        for policy, facts in ((US, us), (SCALE, uk)):
            self.assertEqual(qualify(policy, facts(fit=67), TODAY).status, REVIEW)
            self.assertEqual(qualify(policy, facts(fit=68), TODAY).status, ADMIT)

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
    def test_60_to_67_is_review_and_below_60_is_excluded(self):
        for policy, facts in ((US, us), (SCALE, uk)):
            self.assertEqual(qualify(policy, facts(fit=59), TODAY).status, EXCLUDE)
            self.assertEqual(qualify(policy, facts(fit=60), TODAY).status, REVIEW)
            self.assertEqual(qualify(policy, facts(fit=67), TODAY).status, REVIEW)
            self.assertEqual(qualify(policy, facts(fit=68), TODAY).status, ADMIT)


class LondonAndRemote(unittest.TestCase):
    """D96: Scale-Up needs London, not remote; US Remote needs remote."""

    def facts(self, location, mode, fit=80):
        return lanes.Facts(fit=fit, market="UK", work_mode=mode, posted=ago(2), route={"Scale-up": POSITIVE},
                           geography=lanes.geography_status(location), located=bool(location.strip()))

    def test_remote_never_disqualifies_a_london_scale_up_job(self):
        for location, mode in (("London", "onsite"), ("London (hybrid)", "hybrid"), ("London (Remote)", "remote"), ("Remote - London, UK", "remote"),
                               ("Barcelona · office · Spain | London · office · United Kingdom | Madrid · office · Spain", "onsite")):
            self.assertEqual(qualify(SCALE, self.facts(location, mode), TODAY).status, ADMIT, location)

    def test_a_scale_up_job_outside_london_is_excluded_whether_or_not_it_is_remote(self):
        for location, mode in (("Manchester", "onsite"), ("Remote - UK", "remote"), ("Edinburgh, Scotland", "hybrid"), ("Leeds (Remote)", "remote")):
            decision = qualify(SCALE, self.facts(location, mode), TODAY)
            self.assertEqual((decision.status, decision.reason), (EXCLUDE, "not in London"), location)

    def test_a_scale_up_job_with_no_place_stays_review(self):
        self.assertEqual(qualify(SCALE, self.facts("", "unknown"), TODAY).status, REVIEW)

    def test_a_us_job_must_still_be_remote(self):
        self.assertEqual(qualify(US, us(work_mode="remote"), TODAY).status, ADMIT)
        self.assertEqual(qualify(US, us(work_mode="onsite"), TODAY).reason, "not remote")
        self.assertEqual(qualify(US, us(work_mode="hybrid"), TODAY).reason, "not remote")
