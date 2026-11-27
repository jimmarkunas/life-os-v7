"""the V2_OFFICIAL setter writes one allowed row, refuses every other name, reads back, and is idempotent."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lifeos.accountability import official_setter as o, promotion

SOURCE = "[removed]"


def page(name, value, revision=1, active=True, pid=None):
    return {
        "id": pid or f"id-{name}",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": name}]},
            "Value": {"type": "rich_text", "rich_text": [{"plain_text": value}]},
            "Kind": {"type": "select", "select": {"name": "switch"}},
            "Revision": {"type": "number", "number": revision},
            "Active": {"type": "checkbox", "checkbox": active},
        },
    }


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
                p["properties"]["Value"]["rich_text"] = properties["Value"]["rich_text"] and [
                    {"plain_text": properties["Value"]["rich_text"][0]["text"]["content"]}
                ]
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
        self.assertEqual(
            o.set_value(c, SOURCE, "THROWAWAY_TEST_ROW", "on", live=True, allowed=("THROWAWAY_TEST_ROW",))["status"],
            "written",
        )
        with self.assertRaises(o.WriteError):
            o.set_value(c, SOURCE, "THROWAWAY_TEST_ROW", "off", live=True)
        self.assertEqual(o.ALLOWED, ("V2_OFFICIAL",))

    def test_read_back_mismatch_fails(self):
        c = Fake([page("V2_OFFICIAL", "off")], drop_write=True)
        with self.assertRaises(o.WriteError) as ctx:
            o.set_value(c, SOURCE, "V2_OFFICIAL", "on", live=True)
        self.assertEqual(ctx.exception.code, o.VERIFY_FAILED)

    def test_missing_inactive_and_repeated_rows_fail_closed(self):
        for pages, code in (
            ([], o.ROW_MISSING),
            ([page("V2_OFFICIAL", "off", active=False)], o.ROW_MISSING),
            ([page("V2_OFFICIAL", "off", pid="a"), page("V2_OFFICIAL", "off", pid="b")], o.ROW_REPEATED),
        ):
            c = Fake(pages)
            with self.assertRaises(o.WriteError) as ctx:
                o.set_value(c, SOURCE, "V2_OFFICIAL", "on", live=True)
            self.assertEqual((ctx.exception.code, c.writes), (code, []))


class SameValueProof(unittest.TestCase):
    """Option A: one same-value update of V2_OFFICIAL (off over off), refused or unverified on any drift."""

    def fake(self, **kw):
        return Fake([page("V2_OFFICIAL", "off", 7), page("V2_PROMOTION", "on", 2)], **kw)

    def test_a_dry_run_reads_and_sends_nothing(self):
        c = self.fake()
        self.assertEqual(o.prove_same_value(c, SOURCE, expected_revision=7)["status"], "dry_run")
        self.assertEqual(c.writes, [])

    def test_a_live_proof_sends_exactly_one_same_value_request_and_changes_nothing(self):
        c = self.fake()
        before = [p["properties"] for p in c.pages]
        out = o.prove_same_value(c, SOURCE, live=True, expected_revision=7)
        self.assertEqual((out["status"], out["value"], out["revision"]), ("proved", "off", 7))
        self.assertEqual(len(c.writes), 1)
        self.assertEqual(c.writes[0][0], "id-V2_OFFICIAL")
        self.assertEqual(
            c.writes[0][1],
            {"Value": {"rich_text": [{"type": "text", "text": {"content": "off"}}]}, "Revision": {"number": 7}},
        )
        self.assertEqual([p["properties"] for p in c.pages], before)

    def test_a_row_that_is_not_off_or_at_another_revision_is_refused_before_any_request(self):
        for pages, revision in (([page("V2_OFFICIAL", "on", 7)], 7), ([page("V2_OFFICIAL", "off", 8)], 7)):
            c = Fake(pages)
            with self.assertRaises(o.WriteError) as ctx:
                o.prove_same_value(c, SOURCE, live=True, expected_revision=revision)
            self.assertEqual((ctx.exception.code, c.writes), (o.PROOF_EXPECTATION, []))

    def test_missing_and_repeated_rows_are_refused(self):
        for pages, code in (
            ([], o.ROW_MISSING),
            ([page("V2_OFFICIAL", "off", pid="a"), page("V2_OFFICIAL", "off", pid="b")], o.ROW_REPEATED),
        ):
            with self.assertRaises(o.WriteError) as ctx:
                o.prove_same_value(Fake(pages), SOURCE, live=True)
            self.assertEqual(ctx.exception.code, code)

    def test_a_drifted_read_back_is_unverified_and_the_request_is_never_repeated(self):
        class Changes(Fake):
            def update_page_properties(self, page_id, properties):
                super().update_page_properties(page_id, properties)
                self.pages[0]["properties"]["Revision"]["number"] = 8

        c = Changes([page("V2_OFFICIAL", "off", 7)])
        with self.assertRaises(o.WriteError) as ctx:
            o.prove_same_value(c, SOURCE, live=True, expected_revision=7)
        self.assertEqual((ctx.exception.code, len(c.writes)), (o.PROOF_UNVERIFIED, 1))

    def test_a_refused_request_is_unverified_with_no_second_attempt(self):
        class Refuses(Fake):
            def update_page_properties(self, page_id, properties):
                self.writes.append((page_id, properties))
                raise RuntimeError("403 secret_token_body")

        c = Refuses([page("V2_OFFICIAL", "off", 7)])
        with self.assertRaises(o.WriteError) as ctx:
            o.prove_same_value(c, SOURCE, live=True, expected_revision=7)
        self.assertEqual((ctx.exception.code, len(c.writes)), (o.PROOF_UNVERIFIED, 1))
        self.assertNotIn("secret", str(ctx.exception))

    def test_the_synthetic_off_on_off_path_and_a_refusal_use_the_ordinary_setter_on_a_test_row(self):
        c = Fake([page("THROWAWAY_TEST_ROW", "off", 1)])
        for value, revision in (("on", 2), ("off", 3)):
            o.set_value(c, SOURCE, "THROWAWAY_TEST_ROW", value, live=True, allowed=("THROWAWAY_TEST_ROW",))
            self.assertEqual((c.pages[0]["properties"]["Revision"]["number"], len(c.writes)), (revision, revision - 1))
        with self.assertRaises(o.WriteError):
            o.set_value(c, SOURCE, "V2_PROMOTION", "on", live=True)

    def test_the_command_is_manual_only_and_needs_the_expected_revision(self):
        from lifeos.accountability import setter_proof

        c = self.fake()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = setter_proof.main(["--revision", "7"], {"V7_RUNTIME_CONFIG_SOURCE_ID": SOURCE}, c)
        self.assertEqual((code, c.writes), (0, []))
        self.assertIn("dry_run", buffer.getvalue())
        with redirect_stdout(buffer):
            self.assertEqual(
                setter_proof.main(["--live", "--revision", "9"], {"V7_RUNTIME_CONFIG_SOURCE_ID": SOURCE}, c), 1
            )
        self.assertEqual(c.writes, [])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            setter_proof.main([], {}, c)


class WithPromotion(unittest.TestCase):
    """The setter as the promotion check's `setters`: gates stay in run_if_due."""

    BEFORE = datetime(2027, 1, 5, 23, 59, tzinfo=ZoneInfo("America/Chicago"))
    AFTER = datetime(2027, 1, 6, 8, 0, tzinfo=ZoneInfo("America/Chicago"))

    class Stable:
        """A window whose first pass was recorded on the previous date, so this pass is the second."""

        def __init__(self, now):
            self.record = {"first": now - timedelta(days=1, minutes=5), "last": now - timedelta(days=1, minutes=5)}

        def load(self, key):
            return self.record

        def save(self, key, first, last):
            raise AssertionError("a promoting pass records nothing")

        def reset(self):
            pass

    def run_check(self, now, promotion_on=True, official=False, live=True, verdict_pass=True):
        c = Fake([page("V2_OFFICIAL", "on" if official else "off")])
        alerts = []
        readers = (
            (lambda: (_ for _ in ()).throw(promotion.PromotionError(promotion.INPUT_INVALID)))
            if not verdict_pass
            else None
        )
        with redirect_stdout(io.StringIO()) as out:
            if verdict_pass:
                orig = promotion.evaluate
                promotion.evaluate = lambda inputs: {
                    "verdict": "PASS",
                    "reasons": [],
                    "quarter": "2027-q1",
                    "boards": {},
                }
                try:
                    result = promotion.run_if_due(
                        now,
                        {"V2_PROMOTION": promotion_on, "V2_OFFICIAL": official},
                        lambda: {},
                        o.official_setter(c, SOURCE, live=live),
                        alerts.append,
                        self.Stable(now),
                    )
                finally:
                    promotion.evaluate = orig
            else:
                result = promotion.run_if_due(
                    now,
                    {"V2_PROMOTION": promotion_on, "V2_OFFICIAL": official},
                    readers,
                    o.official_setter(c, SOURCE, live=live),
                    alerts.append,
                    self.Stable(now),
                )
        return result, c, alerts, out.getvalue()

    def test_pass_after_boundary_writes(self):
        result, c, alerts, out = self.run_check(self.AFTER)
        self.assertEqual((result["status"], len(c.writes), alerts), ("promoted", 1, []))
        self.assertIn("V2_OFFICIAL written", out)

    def test_dry_run_logs_the_intended_write_and_writes_nothing(self):
        result, c, _, out = self.run_check(self.AFTER, live=False)
        self.assertEqual((c.writes, "V2_OFFICIAL dry_run" in out), ([], True))

    def test_before_boundary_kill_switch_and_fail_do_not_write(self):
        for kwargs in (
            {"now": self.BEFORE},
            {"now": self.AFTER, "promotion_on": False},
            {"now": self.AFTER, "verdict_pass": False},
            {"now": self.AFTER, "official": True},
        ):
            result, c, _, _ = self.run_check(**kwargs)
            self.assertEqual(c.writes, [], kwargs)

    def test_boundary_is_chicago_not_utc(self):
        just_before_utc = datetime(2027, 1, 6, 7, 59, tzinfo=ZoneInfo("America/Chicago"))
        self.assertEqual(self.run_check(just_before_utc)[0]["status"], "not_due")
        self.assertEqual(
            self.run_check(datetime(2027, 1, 6, 14, 0, tzinfo=ZoneInfo("UTC")))[0]["status"], "promoted"
        )  # 8:00 AM CT
