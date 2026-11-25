"""LIFE-921: the Honcho line and the Anthropic rows, exactly as Jim approved them (comment 13901): red only for ALERT and STOPPED, UNVERIFIED neutral, reserved and actual never swapped."""
import datetime as dt
import unittest
from copy import deepcopy

from lifeos.oraclecloud import card, system_table
from lifeos.platform import honcho_status as hs
from tests.oraclecloud.test_card import NOW, stats

UTC = dt.timezone.utc
READ = dt.datetime(2026, 11, 23, 18, 40, tzinfo=UTC)                           # 12:40 PM CT
GOOD = {"health": True, "failed": False, "exhausted": False, "stopped": False, "backup": {"at": "2026-11-23T09:30:27Z", "ok": True}, "meter": {"cap": 0.9, "reserved": 0.40692}}
BILLED = {"billed_usd": 1.38, "honcho_usd": 0.02, "as_of": "2026-11-23T18:35:00Z"}


def text(segments):
    return "".join(t for t, _ in segments)


class Line(unittest.TestCase):
    def test_healthy_reads_as_the_wireframe(self):
        self.assertEqual(hs.line(GOOD, READ), [("Honcho: healthy ✓ · last backup Nov 23, 3:30 AM CT ✓ · open alerts 0 · read 12:40 PM CT", None)])

    def test_a_synthetic_FAILED_marker_shows_as_a_red_ALERT(self):
        line = hs.line({**GOOD, "failed": True, "backup": {"at": "2026-11-23T09:30:27Z", "ok": False}}, READ)
        self.assertEqual(line[0], ("Honcho: ALERT ✗", "red"))
        self.assertEqual(text(line), "Honcho: ALERT ✗ · backup FAILED Nov 23, 3:30 AM CT · open alerts 1 · read 12:40 PM CT")

    def test_an_EXHAUSTED_meter_shows_as_a_red_STOPPED(self):
        line = hs.line({**GOOD, "exhausted": True}, READ)
        self.assertEqual((line[0], text(line)), (("Honcho: STOPPED ✗", "red"), "Honcho: STOPPED ✗ · meter EXHAUSTED · open alerts 1 · read 12:40 PM CT"))

    def test_an_unreadable_status_is_UNVERIFIED_and_neutral(self):
        for reading in (None, {**GOOD, "health": None}, {**GOOD, "failed": None}):
            line = hs.line(reading, READ)
            self.assertEqual(line, [("Honcho: UNVERIFIED · could not read status · read 12:40 PM CT", None)])

    def test_a_service_that_does_not_answer_is_an_alert(self):
        self.assertEqual(text(hs.line({**GOOD, "health": False}, READ)), "Honcho: ALERT ✗ · API not answering · open alerts 1 · read 12:40 PM CT")

    def test_a_missing_last_backup_reads_unverified_not_a_tick(self):
        self.assertIn("last backup UNVERIFIED", text(hs.line({**GOOD, "backup": None}, READ)))

    def test_only_alert_and_stopped_are_red(self):
        reds = {state: any(c == "red" for _, c in hs.line(r, READ)) for state, r in (("healthy", GOOD), ("unverified", None), ("alert", {**GOOD, "failed": True}), ("stopped", {**GOOD, "stopped": True}))}
        self.assertEqual(reds, {"healthy": False, "unverified": False, "alert": True, "stopped": True})


class Rows(unittest.TestCase):
    def test_the_two_rows_match_the_wireframe(self):
        promo, meter = hs.rows(GOOD, BILLED, READ)
        self.assertEqual(promo, ["Anthropic · promo credit", "$98.62 of $100", "▱▱▱▱▱▱▱▱▱▱ $1.38 billed", "monthly", "Sat Dec 5, 6:00 PM CT", "AVAILABLE"])
        self.assertEqual(meter, ["Anthropic · Honcho meter", "$0.49 of $0.90 cap", "▰▰▰▰▰▱▱▱▱▱ $0.407 reserved · $0.02 actual", "monthly cap", "Sat Dec 5, 6:00 PM CT", "AVAILABLE"])

    def test_reserved_and_actual_are_never_swapped(self):
        _, meter = hs.rows(GOOD, {**BILLED, "honcho_usd": 0.50}, READ)
        self.assertIn("$0.407 reserved · $0.50 actual", meter[2])
        self.assertNotIn("$0.407 actual", meter[2])
        self.assertEqual(hs.summary(GOOD, BILLED, READ), "Anthropic API · $1.38 billed of $100 promo credit this window (Anthropic report, read 12:40 PM CT) · Honcho meter $0.407 reserved, $0.02 actual of $0.90 monthly cap")

    def test_unknown_values_are_UNVERIFIED_never_zero(self):
        promo, meter = hs.rows(None, None, READ)
        self.assertEqual((promo[1:3], promo[5], meter[1:3], meter[5]), (["UNVERIFIED", "UNVERIFIED"], "UNAVAILABLE", ["UNVERIFIED", "UNVERIFIED"], "UNAVAILABLE"))
        _, meter = hs.rows(GOOD, {**BILLED, "honcho_usd": None}, READ)
        self.assertIn("actual UNVERIFIED", meter[2])
        self.assertIn("billed spend UNVERIFIED", hs.summary(GOOD, None, READ))

    def test_statuses(self):
        self.assertEqual(hs.rows(GOOD, {**BILLED, "billed_usd": 80.0}, READ)[0][5], "NEAR LIMIT")
        self.assertEqual(hs.rows(GOOD, {**BILLED, "billed_usd": 100.0}, READ)[0][5], "EXHAUSTED")
        self.assertEqual(hs.rows({**GOOD, "exhausted": True}, BILLED, READ)[1][5], "EXHAUSTED")
        after = dt.datetime(2026, 12, 6, 0, 1, tzinfo=UTC)                          # just after Dec 5, 6:00 PM CT: a new window, never EXPIRED (LIFE-950)
        self.assertEqual((hs.rows(GOOD, BILLED, after)[0][5], hs.rows(GOOD, BILLED, after)[1][5]), ("AVAILABLE", "AVAILABLE"))
        self.assertEqual(hs.rows(GOOD, BILLED, after)[0][4], "Tue Jan 5, 6:00 PM CT")                  # the reset date moves to the next 5th

    def test_the_readings_are_validated_whole(self):
        self.assertTrue(hs.honcho_ok(GOOD) and hs.honcho_ok(None) and hs.anthropic_ok(BILLED) and hs.anthropic_ok(None))
        for bad in ({**GOOD, "extra": 1}, {**GOOD, "health": "yes"}, {**GOOD, "meter": {"cap": 0.9}}, {**GOOD, "backup": {"at": "not a time", "ok": True}}):
            self.assertFalse(hs.honcho_ok(bad), bad)
        for bad in ({**BILLED, "billed_usd": -1}, {**BILLED, "as_of": "x"}, {"billed_usd": 1}):
            self.assertFalse(hs.anthropic_ok(bad), bad)


class Read(unittest.TestCase):
    def reply(self, **over):
        import json
        return json.dumps({"observed_at": "2026-11-23T18:39:00Z", "honcho": GOOD, "anthropic": BILLED, **over}).encode()

    def test_a_fresh_reply_gives_both_readings(self):
        self.assertEqual(hs.read("h", READ, lambda host: self.reply()), (GOOD, BILLED))

    def test_unreachable_stale_oversize_or_off_contract_is_unknown_never_zero(self):
        def boom(host):
            raise OSError("down")
        self.assertEqual(hs.read("h", READ, boom), (None, None))
        self.assertEqual(hs.read("h", READ, lambda host: self.reply(observed_at="2026-11-23T17:00:00Z")), (None, None))        # older than ten minutes
        self.assertEqual(hs.read("h", READ, lambda host: b"x" * 5000), (None, None))
        self.assertEqual(hs.read("h", READ, lambda host: b"not json"), (None, None))
        self.assertEqual(hs.read("h", READ, lambda host: self.reply(honcho={"health": True})), (None, BILLED))                  # each reading is kept or dropped on its own
        self.assertEqual(hs.read("h", READ, lambda host: self.reply(anthropic={"billed_usd": -1})), (GOOD, None))

    def test_older_oracle_stats_without_the_keys_read_as_unknown(self):
        import json
        old = json.dumps({"observed_at": "2026-11-23T18:39:00Z"}).encode()
        self.assertEqual(hs.read("h", READ, lambda host: old), (None, None))


class OracleCard(unittest.TestCase):
    """LIFE-975 step 4d: Oracle's honcho and anthropic readings are rows of the System table (Honcho · Backup, Honcho · Monthly cap, Anthropic · Promo credit)."""

    def rows(self, **over):
        reading = card.validate(__import__("json").dumps(stats(**over)).encode(), NOW)
        built = system_table.readings(reading, NOW, None, None, None, NOW)
        return {r[0][0][0]: r for r in system_table.finish(built, {}, None, NOW)[1:]}

    def test_stats_that_report_honcho_are_accepted_and_become_the_backup_and_cap_rows(self):
        rows = self.rows(honcho=GOOD, anthropic=BILLED)
        backup = rows["Honcho · Backup"]
        self.assertEqual((backup[4], backup[1][0][1]), ([("OK", "green")], "green"))
        self.assertIn("healthy", backup[2][0][0])
        self.assertTrue(rows["Honcho · Monthly cap"][2][0][0].startswith("$"))
        self.assertTrue(rows["Anthropic · Promo credit"][2][0][0].startswith("$"))

    def test_older_stats_without_honcho_leave_those_rows_unread_and_stale_never_zero(self):
        rows = self.rows()
        for label in ("Honcho · Backup", "Honcho · Monthly cap", "Anthropic · Promo credit"):
            self.assertEqual(rows[label][4][0][1], "red", label)
            self.assertEqual(rows[label][2], [("unavailable", None)], label)

    def test_a_synthetic_FAILED_marker_reaches_the_table_as_a_red_down(self):
        failed = deepcopy(GOOD)
        failed["failed"] = True
        rows = self.rows(honcho=failed, anthropic=BILLED)
        self.assertEqual(rows["Honcho · Backup"][4], [("DOWN", "red")])

    def test_an_off_contract_honcho_reading_refuses_the_whole_reply(self):
        with self.assertRaises(card.OracleCardError):
            card.validate(__import__("json").dumps(stats(honcho={"health": True})).encode(), NOW)


if __name__ == "__main__":
    unittest.main()
