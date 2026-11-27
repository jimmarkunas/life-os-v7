"""AC6, AC11, AC12: the promotion check is pure and injected"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from lifeos.accountability import promotion as p

CT = ZoneInfo("America/Chicago")
AFTER = datetime(2027, 1, 1, 8, 0, tzinfo=CT)
EXPLAINED = {"by": "MC", "what": "moved before the freeze", "source": "LIFE-918 comment"}


def board(**over):
    data = {
        "frozen": ["MEJ-1", "MEJ-2"],
        "counted": ["MEJ-1", "MEJ-2", "MEJ-3"],
        "goals": {"MEJ-1": ["MEJ-9"], "MEJ-2": ["MEJ-9"]},
        "completions": {"MEJ-1": {"date": "2027-01-04", "source": "changelog"}},
        "history": {"before": {"q3": 4}, "after": {"q3": 4}},
        "recurring": {"manual": 2, "auto": 3, "total": 5, "manual_keys": ["MEJ-70"], "auto_keys": ["MEJ-71"]},
        "discrepancies": [],
    }
    data.update(over)
    return data


def good(**mej):
    return {"quarter": "2027Q1", "boards": {"JFM": board(), "MEJ": board(**mej)}}


class Calls:
    def __init__(self, inputs=None, set_error=None, alert_error=None):
        self.inputs, self.set_error, self.alert_error = inputs, set_error, alert_error
        self.reads, self.sets, self.alerts = 0, 0, []

    def read(self):
        self.reads += 1
        if isinstance(self.inputs, Exception):
            raise self.inputs
        return self.inputs

    def set(self):
        self.sets += 1
        if self.set_error:
            raise self.set_error

    def alert(self, item):
        self.alerts.append(item)
        if self.alert_error:
            raise self.alert_error

    def run(self, now=AFTER, promotion=True, official=False):
        return p.run_if_due(now, {"V2_PROMOTION": promotion, "V2_OFFICIAL": official}, self.read, self.set, self.alert)


class Evaluate(unittest.TestCase):
    def test_a_clean_quarter_passes(self):
        verdict = p.evaluate(good())
        self.assertEqual((verdict["verdict"], verdict["reasons"]), ("PASS", []))

    def test_missing_membership_is_a_hard_fail(self):
        for frozen in (None, []):
            verdict = p.evaluate(good(frozen=frozen))
            self.assertEqual((verdict["verdict"], verdict["reasons"]), ("FAIL", [p.MEMBERSHIP_MISSING]))

    def test_the_shipped_manifest_has_no_q1_list_so_it_fails_closed(self):
        manifest = json.loads(
            (Path(__file__).resolve().parents[2] / "lifeos/accountability/mej_quarter_membership.json").read_text()
        )
        self.assertNotIn("2027-q1", manifest["quarters"])
        self.assertIn(
            p.MEMBERSHIP_MISSING, p.evaluate(good(frozen=(manifest["quarters"].get("2027Q1") or None)))["reasons"]
        )

    def test_membership_must_be_one_hundred_percent(self):
        verdict = p.evaluate(good(counted=["MEJ-1"]))
        self.assertEqual(verdict["reasons"], [p.MEMBERSHIP_INCOMPLETE])

    def test_duplicate_attribution_fails(self):
        self.assertEqual(p.evaluate(good(counted=["MEJ-1", "MEJ-2", "MEJ-2"]))["reasons"], [p.DUPLICATE_ATTRIBUTION])
        self.assertEqual(p.evaluate(good(goals={"MEJ-1": ["MEJ-9", "MEJ-8"]}))["reasons"], [p.DUPLICATE_ATTRIBUTION])

    def test_completion_dates_must_come_from_the_changelog(self):
        for completions in (
            {"MEJ-1": {"date": "2027-01-04", "source": "resolutiondate"}},
            {"MEJ-1": {"date": None, "source": "changelog"}},
            {"MEJ-1": {"date": "2027-01-04", "source": "changelog", "ambiguous": True}},
            None,
        ):
            self.assertEqual(p.evaluate(good(completions=completions))["reasons"], [p.COMPLETION_DATE_UNVERIFIED])

    def test_historical_figures_must_not_move(self):
        self.assertEqual(
            p.evaluate(good(history={"before": {"q3": 4}, "after": {"q3": 5}}))["reasons"], [p.HISTORY_DRIFT]
        )

    def test_manual_and_automatic_recurring_closures_stay_distinct(self):
        self.assertEqual(
            p.evaluate(good(recurring={"manual": 2, "auto": 3, "total": 6}))["reasons"], [p.RECURRING_NOT_DISTINCT]
        )
        overlap = {"manual": 1, "auto": 1, "total": 2, "manual_keys": ["MEJ-70"], "auto_keys": ["MEJ-70"]}
        self.assertEqual(p.evaluate(good(recurring=overlap))["reasons"], [p.RECURRING_NOT_DISTINCT])

    def test_a_missing_board_or_quarter_is_invalid_input(self):
        self.assertEqual(p.evaluate({"quarter": "2027Q1", "boards": {"JFM": board()}})["reasons"], [p.INPUT_INVALID])
        self.assertEqual(p.evaluate(None)["reasons"], [p.INPUT_INVALID])
        self.assertEqual(p.evaluate({"boards": {}})["reasons"], [p.INPUT_INVALID])


class Tolerance(unittest.TestCase):
    def test_reconcile_separates_explained_from_unexplained(self):
        out = p.reconcile(
            [
                {"key": "MEJ-5", "explanation": EXPLAINED},
                {"key": "MEJ-6", "explanation": None},
                {"key": "MEJ-7", "explanation": {"by": "MC", "what": " ", "source": "x"}},
                {"key": "MEJ-6", "explanation": None},
                {"key": "", "explanation": EXPLAINED},
            ]
        )
        self.assertEqual(out, {"explained": ["MEJ-5"], "unexplained": ["MEJ-6", "MEJ-7"]})

    def test_one_unexplained_discrepancy_per_board_is_tolerated(self):
        verdict = p.evaluate(
            good(discrepancies=[{"key": "MEJ-5", "explanation": None}, {"key": "MEJ-6", "explanation": EXPLAINED}])
        )
        self.assertEqual(
            (verdict["verdict"], verdict["boards"]["MEJ"]["unexplained"], verdict["boards"]["MEJ"]["explained"]),
            ("PASS", 1, 1),
        )

    def test_two_unexplained_discrepancies_block_promotion(self):
        verdict = p.evaluate(
            good(discrepancies=[{"key": "MEJ-5", "explanation": None}, {"key": "MEJ-6", "explanation": None}])
        )
        self.assertEqual((verdict["verdict"], verdict["reasons"]), ("FAIL", [p.TOLERANCE_EXCEEDED]))

    def test_the_tolerance_is_per_board(self):
        inputs = good(discrepancies=[{"key": "MEJ-5", "explanation": None}])
        inputs["boards"]["JFM"]["discrepancies"] = [
            {"key": "JFM-5", "explanation": None},
            {"key": "JFM-6", "explanation": None},
        ]
        verdict = p.evaluate(inputs)
        self.assertEqual(
            (verdict["verdict"], verdict["boards"]["JFM"]["unexplained"], verdict["boards"]["MEJ"]["unexplained"]),
            ("FAIL", 2, 1),
        )

    def test_the_tolerance_never_excuses_an_ac11_failure(self):
        verdict = p.evaluate(good(counted=["MEJ-1"], discrepancies=[]))
        self.assertEqual(verdict["verdict"], "FAIL")
        verdict = p.evaluate(
            good(history={"before": 1, "after": 2}, discrepancies=[{"key": "MEJ-5", "explanation": None}])
        )
        self.assertEqual((verdict["verdict"], verdict["reasons"]), ("FAIL", [p.HISTORY_DRIFT]))


class Boundary(unittest.TestCase):
    def test_not_due_a_second_before_eight_and_due_at_eight_central(self):
        self.assertFalse(p.due(AFTER - timedelta(seconds=1)))
        self.assertTrue(p.due(AFTER))
        self.assertFalse(p.due(datetime(2026, 12, 31, 23, 59, tzinfo=CT)))

    def test_the_instant_is_the_same_in_any_zone(self):
        self.assertEqual(AFTER.astimezone(timezone.utc), datetime(2027, 1, 1, 14, 0, tzinfo=timezone.utc))
        self.assertTrue(p.due(datetime(2027, 1, 1, 14, 0, tzinfo=timezone.utc)))
        self.assertFalse(p.due(datetime(2027, 1, 1, 13, 59, tzinfo=timezone.utc)))
        self.assertTrue(p.due(datetime(2027, 1, 1, 9, 0, tzinfo=ZoneInfo("America/New_York"))))

    def test_eight_central_follows_daylight_time_not_a_fixed_offset(self):
        self.assertEqual(p.boundary(2027, 1, 1).astimezone(timezone.utc).hour, 14)
        self.assertEqual(p.boundary(2027, 7, 1).astimezone(timezone.utc).hour, 13)
        self.assertEqual(
            p.boundary(2027, 3, 13).astimezone(timezone.utc).hour, 14
        )  # Saturday before daylight time starts: CST
        self.assertEqual(p.boundary(2027, 3, 14).astimezone(timezone.utc).hour, 13)
        self.assertEqual(p.boundary(2027, 3, 14).utcoffset(), timedelta(hours=-5))

    def test_a_time_without_a_zone_is_refused(self):
        with self.assertRaises(p.PromotionError) as caught:
            p.due(datetime(2027, 1, 1, 8, 0))
        self.assertEqual(caught.exception.code, p.INPUT_INVALID)


class RunIfDue(unittest.TestCase):
    def test_kill_switch_off_reads_nothing_writes_nothing_alerts_nothing(self):
        calls = Calls(good())
        self.assertEqual(calls.run(promotion=False), {"status": "not_enabled"})
        self.assertEqual((calls.reads, calls.sets, calls.alerts), (0, 0, []))

    def test_before_the_first_run_instant_nothing_happens(self):
        calls = Calls(good())
        self.assertEqual(calls.run(now=AFTER - timedelta(minutes=1)), {"status": "not_due"})
        self.assertEqual((calls.reads, calls.sets, calls.alerts), (0, 0, []))

    def test_at_the_boundary_a_pass_sets_the_official_row_once(self):
        calls = Calls(good())
        out = calls.run()
        self.assertEqual((out["status"], out["verdict"]["verdict"]), ("promoted", "PASS"))
        self.assertEqual((calls.reads, calls.sets, calls.alerts), (1, 1, []))

    def test_already_official_is_a_no_op_so_a_second_run_changes_nothing(self):
        calls = Calls(good())
        self.assertEqual(calls.run(official=True), {"status": "already_official"})
        self.assertEqual((calls.reads, calls.sets, calls.alerts), (0, 0, []))

    def test_a_fail_keeps_legacy_and_sends_one_alert_with_a_stable_key(self):
        calls = Calls(good(frozen=None))
        out = calls.run()
        self.assertEqual(
            (out["status"], out["alerted"], out["verdict"]["reasons"]), ("kept_legacy", True, [p.MEMBERSHIP_MISSING])
        )
        self.assertEqual((calls.sets, len(calls.alerts)), (0, 1))
        later = Calls(good(frozen=None))
        later.run(now=AFTER + timedelta(days=3))
        self.assertEqual(
            calls.alerts[0]["key"], later.alerts[0]["key"]
        )  # the same row every day: the inbox send updates it
        self.assertEqual(calls.alerts[0]["key"], "v2-promotion-2027Q1")
        self.assertNotIn("MEJ-", json.dumps(calls.alerts[0]))  # codes and counts only, never a Task key or summary

    def test_missing_membership_with_the_shipped_empty_manifest_never_promotes(self):
        calls = Calls(good(frozen=[]))
        self.assertEqual(calls.run()["status"], "kept_legacy")
        self.assertEqual(calls.sets, 0)

    def test_unreadable_inputs_fail_closed_with_a_fixed_code(self):
        calls = Calls(RuntimeError("boom with secrets"))
        out = calls.run()
        self.assertEqual((out["status"], out["verdict"]["reasons"]), ("kept_legacy", [p.INPUT_INVALID]))
        self.assertNotIn("boom", json.dumps(out) + json.dumps(calls.alerts))

    def test_a_pass_with_no_safe_setter_keeps_legacy_and_alerts(self):
        calls = Calls(good())
        out = p.run_if_due(
            AFTER, {"V2_PROMOTION": True, "V2_OFFICIAL": False}, calls.read, p.refusing_setter, calls.alert
        )
        self.assertEqual((out["status"], out["verdict"]["reasons"]), ("kept_legacy", [p.SETTER_UNAVAILABLE]))
        self.assertEqual(len(calls.alerts), 1)

    def test_a_failed_alert_never_turns_a_fail_into_a_pass(self):
        calls = Calls(good(frozen=None), alert_error=RuntimeError("down"))
        out = calls.run()
        self.assertEqual((out["status"], out["alerted"], out["alert_code"]), ("kept_legacy", False, p.ALERT_FAILED))

    def test_a_naive_clock_fails_closed(self):
        with self.assertRaises(p.PromotionError):
            Calls(good()).run(now=datetime(2027, 1, 1, 9, 0))


class InboxAlerter(unittest.TestCase):
    def test_it_uses_the_existing_inbox_delivery_with_the_item_and_a_sender(self):
        seen = []
        send = p.inbox_alerter(lambda item: seen.append(item) or {"ok": True, "updated": 1})
        send(p.alert_item({"quarter": "2027Q1", "reasons": [p.HISTORY_DRIFT]}))
        self.assertEqual(
            (seen[0]["key"], seen[0]["from"], seen[0]["urgency"]), ("v2-promotion-2027Q1", "v7-promotion", "Today")
        )

    def test_a_delivery_that_is_not_ok_raises(self):
        send = p.inbox_alerter(lambda item: {"ok": False, "reason": "inbox_failed"})
        with self.assertRaises(p.PromotionError):
            send(p.alert_item({"quarter": "2027Q1", "reasons": []}))


class OneUnit(unittest.TestCase):
    """Jim, '918 uniform new' (comment 14228): JFM and MEJ promote together or not at all"""

    def both(self, jfm=None, mej=None):
        return {"quarter": "2027Q1", "boards": {"JFM": board(**(jfm or {})), "MEJ": board(**(mej or {}))}}

    def test_both_boards_passing_promote_once_with_no_alert(self):
        calls = Calls(self.both())
        out = calls.run()
        self.assertEqual((out["status"], calls.sets, calls.alerts), ("promoted", 1, []))

    def test_jfm_failing_keeps_both_on_legacy_with_one_alert_naming_jfm_only(self):
        calls = Calls(self.both(jfm={"history": {"before": 1, "after": 2}}))
        out = calls.run()
        self.assertEqual((out["status"], calls.sets, len(calls.alerts)), ("kept_legacy", 0, 1))
        self.assertIn("failing board: JFM.", calls.alerts[0]["details"])
        self.assertNotIn("MEJ", calls.alerts[0]["details"].split("reasons:")[0])

    def test_mej_failing_keeps_both_on_legacy_with_one_alert_naming_mej_only(self):
        calls = Calls(self.both(mej={"discrepancies": [{"key": "MEJ-1"}, {"key": "MEJ-2"}]}))
        out = calls.run()
        self.assertEqual((out["status"], calls.sets, len(calls.alerts)), ("kept_legacy", 0, 1))
        self.assertIn("failing board: MEJ.", calls.alerts[0]["details"])

    def test_either_list_missing_fails_and_names_that_board(self):
        for name, jfm, mej in (("JFM", {"frozen": None}, None), ("MEJ", None, {"frozen": []})):
            calls = Calls(self.both(jfm=jfm, mej=mej))
            out = calls.run()
            self.assertEqual((out["status"], calls.sets, len(calls.alerts)), ("kept_legacy", 0, 1), name)
            self.assertIn(f"failing board: {name}.", calls.alerts[0]["details"])

    def test_both_failing_is_still_one_alert_naming_both(self):
        calls = Calls(self.both(jfm={"frozen": None}, mej={"frozen": None}))
        calls.run()
        self.assertEqual(len(calls.alerts), 1)
        self.assertIn("failing board: JFM, MEJ.", calls.alerts[0]["details"])

    def test_the_kill_switch_covers_both_boards(self):
        calls = Calls(self.both())
        self.assertEqual((calls.run(promotion=False)["status"], calls.reads, calls.sets), ("not_enabled", 0, 0))


class NotWired(unittest.TestCase):
    def test_only_the_one_call_site_calls_the_promotion(self):
        root = Path(__file__).resolve().parents[2]
        callers = [
            str(path.relative_to(root))
            for path in (root / "lifeos").rglob("*.py")
            if path.name not in ("promotion.py", "promotion_call.py")
            and "promotion" in path.read_text()
            and "run_if_due" in path.read_text()
        ]
        self.assertEqual(callers, [])


if __name__ == "__main__":
    unittest.main()
