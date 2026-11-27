"""the V2_OFFICIAL setter writes one allowed row, refuses every other name, reads back, and is idempotent."""
import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import official_setter as o, promotion

SOURCE = "41e3c5a0-5926-8167-9b53-000b6c6947f0"


def page(name, value, revision=1, active=True, pid=None):
    return {"id": pid or f"id-{name}", "properties": {"Name": {"type": "title", "title": [{"plain_text": name}]}, "Value": {"type": "rich_text", "rich_text": [{"plain_text": value}]},
                                                       "Kind": {"type": "select", "select": {"name": "switch"}}, "Revision": {"type": "number", "number": revision},
                                                       "Active": {"type": "checkbox", "checkbox": active}}}


class Fake:
    def __init__(self, pages, drop_write=False):
        self.pages, self.writes, self.drop_write = pages, [], drop_write

    def call(self, method, path, body=None):
        return {"results": list(self.pages), "has_more": False}

    def update_page_properties(self, page_id, properties):
        self.writes.append((page_id, properties))
        if self.drop_write:
            return
        for p in self.pages:
            if p["id"] == page_id:
                p["properties"]["Value"]["rich_text"] = properties["Value"]["rich_text"] and [{"plain_text": properties["Value"]["rich_text"][0]["text"]["content"]}]
                p["properties"]["Revision"]["number"] = properties["Revision"]["number"]


class OfficialSetter(unittest.TestCase):
    def test_live_write_changes_value_and_bumps_revision(self):
        c = Fake([page("V2_OFFICIAL", "off", 3), page("V2_PROMOTION", "on")])
        self.assertEqual(o.set_value(c, SOURCE, "V2_OFFICIAL", "on", live=True)["status"], "written")
        self.assertEqual(len(c.writes), 1)
        self.assertEqual(c.writes[0][0], "id-V2_OFFICIAL")
        self.assertEqual(c.writes[0][1]["Revision"], {"number": 4})
        self.assertEqual(set(c.writes[0][1]), {"Value", "Revision"})

    def test_second_run_is_idempotent(self):
        c = Fake([page("V2_OFFICIAL", "off")])
        o.set_value(c, SOURCE, "V2_OFFICIAL", "on", live=True)
        self.assertEqual(o.set_value(c, SOURCE, "V2_OFFICIAL", "on", live=True)["status"], "unchanged")
        self.assertEqual(len(c.writes), 1)

    def test_dry_run_writes_nothing(self):
        c = Fake([page("V2_OFFICIAL", "off")])
        self.assertEqual(o.set_value(c, SOURCE, "V2_OFFICIAL", "on")["status"], "dry_run")
        self.assertEqual(c.writes, [])

    def test_every_other_name_is_refused_before_any_read(self):
        class Boom(Fake):
            def call(self, *a, **k):
                raise AssertionError("read before the allow-list check")
        for name in ("V2_PROMOTION", "JIRA_CARD_BLOCK_ID", "v2_official", "", "V2_OFFICIAL "):
            with self.assertRaises(o.WriteError) as ctx:
                o.set_value(Boom([]), SOURCE, name, "on", live=True)
            self.assertEqual(ctx.exception.code, o.NOT_ALLOWED)

    def test_test_only_row_name_works_only_through_the_allowed_argument(self):
        c = Fake([page("THROWAWAY_TEST_ROW", "off")])
        self.assertEqual(o.set_value(c, SOURCE, "THROWAWAY_TEST_ROW", "on", live=True, allowed=("THROWAWAY_TEST_ROW",))["status"], "written")
        with self.assertRaises(o.WriteError):
            o.set_value(c, SOURCE, "THROWAWAY_TEST_ROW", "off", live=True)
        self.assertEqual(o.ALLOWED, ("V2_OFFICIAL",))

    def test_read_back_mismatch_fails(self):
        c = Fake([page("V2_OFFICIAL", "off")], drop_write=True)
        with self.assertRaises(o.WriteError) as ctx:
            o.set_value(c, SOURCE, "V2_OFFICIAL", "on", live=True)
        self.assertEqual(ctx.exception.code, o.VERIFY_FAILED)

    def test_missing_inactive_and_repeated_rows_fail_closed(self):
        for pages, code in (([], o.ROW_MISSING), ([page("V2_OFFICIAL", "off", active=False)], o.ROW_MISSING),
                            ([page("V2_OFFICIAL", "off", pid="a"), page("V2_OFFICIAL", "off", pid="b")], o.ROW_REPEATED)):
            c = Fake(pages)
            with self.assertRaises(o.WriteError) as ctx:
                o.set_value(c, SOURCE, "V2_OFFICIAL", "on", live=True)
            self.assertEqual((ctx.exception.code, c.writes), (code, []))


class WithPromotion(unittest.TestCase):
    """The setter as the promotion check's `setters`: gates stay in run_if_due."""
    BEFORE = datetime(2026, 12, 31, 23, 59, tzinfo=ZoneInfo("America/Chicago"))
    AFTER = datetime(2027, 1, 1, 8, 0, tzinfo=ZoneInfo("America/Chicago"))

    def run_check(self, now, promotion_on=True, official=False, live=True, verdict_pass=True):
        c = Fake([page("V2_OFFICIAL", "on" if official else "off")])
        alerts = []
        readers = (lambda: (_ for _ in ()).throw(promotion.PromotionError(promotion.INPUT_INVALID))) if not verdict_pass else None
        with redirect_stdout(io.StringIO()) as out:
            if verdict_pass:
                orig = promotion.evaluate
                promotion.evaluate = lambda inputs: {"verdict": "PASS", "reasons": [], "quarter": "2027Q1", "boards": {}}
                try:
                    result = promotion.run_if_due(now, {"V2_PROMOTION": promotion_on, "V2_OFFICIAL": official}, lambda: {}, o.official_setter(c, SOURCE, live=live), alerts.append)
                finally:
                    promotion.evaluate = orig
            else:
                result = promotion.run_if_due(now, {"V2_PROMOTION": promotion_on, "V2_OFFICIAL": official}, readers, o.official_setter(c, SOURCE, live=live), alerts.append)
        return result, c, alerts, out.getvalue()

    def test_pass_after_boundary_writes(self):
        result, c, alerts, out = self.run_check(self.AFTER)
        self.assertEqual((result["status"], len(c.writes), alerts), ("promoted", 1, []))
        self.assertIn("V2_OFFICIAL written", out)

    def test_dry_run_logs_the_intended_write_and_writes_nothing(self):
        result, c, _, out = self.run_check(self.AFTER, live=False)
        self.assertEqual((c.writes, "V2_OFFICIAL dry_run" in out), ([], True))

    def test_before_boundary_kill_switch_and_fail_do_not_write(self):
        for kwargs in ({"now": self.BEFORE}, {"now": self.AFTER, "promotion_on": False}, {"now": self.AFTER, "verdict_pass": False}, {"now": self.AFTER, "official": True}):
            result, c, _, _ = self.run_check(**kwargs)
            self.assertEqual(c.writes, [], kwargs)

    def test_boundary_is_chicago_not_utc(self):
        just_before_utc = datetime(2027, 1, 1, 7, 59, tzinfo=ZoneInfo("America/Chicago"))
        self.assertEqual(self.run_check(just_before_utc)[0]["status"], "not_due")
        self.assertEqual(self.run_check(datetime(2027, 1, 1, 14, 0, tzinfo=ZoneInfo("UTC")))[0]["status"], "promoted")  # 8:00 AM CT
