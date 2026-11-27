"""The promotion check reads real inputs: the approved Q1 key list, Jira's changelog, closures and the Q4 figures."""

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import promotion as p, promotion_inputs as pi
from tests.accountability.test_mej_goal_links import child, status_change

CT = ZoneInfo("America/Chicago")
NOW = datetime(2027, 1, 6, 8, 5, tzinfo=CT)
ENV = {"MEJ_AUTOMATION_SINCE": "2026-11-25T14:30:00-06:00"}
Q1 = [f"MEJ-{n}" for n in (300, 301, 302)]
Q4 = ["MEJ-10", "MEJ-11"]


def task(goal):
    return {"parent": goal, "goal": goal, "reason": "baseline", "approval": "SYNTHETIC", "effective": "2026-10-07"}


def manifest(q1_extra=None, snapshot=None):
    row = lambda tasks, **extra: {"approved": True, "baseline_id": "B", "approval_ref": "A", "tasks": tasks, **extra}
    return {
        "schema_version": 2,
        "quarters": {
            "2026-q4": {
                "boards": {
                    "MEJ": row(
                        {k: task("MEJ-1") for k in Q4},
                        snapshot=snapshot or {"version": "v1", "figures": {"MEJ-1": [1, 2]}},
                    )
                }
            },
            "2027-q1": {"boards": {"MEJ": row({k: task("MEJ-5") for k in Q1}, **(q1_extra or {}))}},
        },
    }


class World:
    """Synthetic Jira: Q1 members (one done by hand on Jan 7... no: before the date used here), Q4 members, legacy epics."""

    def __init__(
        self, legacy=Q1, done_q1=("MEJ-300",), done_date="2027-01-07T09:00:00.000-0600", labels=None, resolution="Done"
    ):
        self.rows = {}
        for key in Q1:
            self.rows[key] = child(
                key, "MEJ-5", done=key in done_q1, resolved=done_date[:10] if key in done_q1 else None
            )
            self.rows[key]["fields"]["resolution"] = {"name": resolution} if key in done_q1 else None
            self.rows[key]["fields"]["labels"] = list((labels or {}).get(key, []))
            self.rows[key]["fields"]["statuscategorychangedate"] = done_date if key in done_q1 else None
        for key, resolved in (("MEJ-10", "2026-11-20"), ("MEJ-11", None)):
            self.rows[key] = child(key, "MEJ-1", done=bool(resolved), resolved=resolved)
        self.histories = {k: [status_change(done_date)] for k in done_q1}
        self.histories["MEJ-10"] = [status_change("2026-11-20T10:00:00.000-0600")]
        self.legacy = list(legacy)

    def get(self, path, params):
        if path.endswith("/changelog"):
            rows = self.histories.get(path.split("/")[-2], [])
            return {"values": rows, "isLast": True, "total": len(rows)}
        jql = params["jql"]
        if jql.startswith("issuetype = Epic"):
            return {"issues": [{"key": "MEJ-5", "fields": {"summary": "Career Q1 2027"}}], "isLast": True}
        if jql.startswith("key in ("):
            keys = jql.split("(", 1)[1].split(")", 1)[0].split(",")
            return {"issues": [self.rows[k] for k in keys if k in self.rows], "isLast": True}
        if jql.startswith("parent in ("):
            return {"issues": [self.rows[k] for k in self.legacy if k in self.rows], "isLast": True}
        raise AssertionError(jql)


def read_board(world, mf=None, name="MEJ", environ=ENV):
    return pi.board(world, name, 2027, 1, environ, date(2027, 1, 6), mf or manifest())


class RealInputs(unittest.TestCase):
    def test_a_clean_board_reads_every_input_and_passes_the_check(self):
        data = read_board(World())
        self.assertEqual((data["frozen"], data["counted"]), (Q1, Q1))
        self.assertEqual(
            data["completions"], {"MEJ-300": {"date": "2027-01-07", "source": "changelog", "ambiguous": False}}
        )
        self.assertEqual(
            data["recurring"], {"manual": 1, "auto": 0, "total": 1, "manual_keys": ["MEJ-300"], "auto_keys": []}
        )
        self.assertEqual(data["history"], {"before": {"MEJ-1": [1, 2]}, "after": {"MEJ-1": [1, 2]}})
        self.assertEqual(data["discrepancies"], [])
        verdict = p.evaluate({"quarter": "2027-q1", "boards": {"JFM": data, "MEJ": data}})
        self.assertEqual((verdict["verdict"], verdict["reasons"]), ("PASS", []))

    def test_the_shipped_manifest_has_no_q1_list_so_every_board_reads_empty(self):
        out = pi.readers(World(), ENV, date(2027, 1, 6))()
        self.assertEqual(out, {"quarter": "2027-q1", "boards": {"JFM": {}, "MEJ": {}}})
        verdict = p.evaluate(out)
        self.assertEqual(verdict["verdict"], "FAIL")
        self.assertEqual(p.failing_boards(verdict), ["JFM", "MEJ"])

    def test_either_board_missing_or_without_a_published_baseline_blocks_both(self):
        both = pi.readers(World(), ENV, date(2027, 1, 6), manifest=manifest())()
        self.assertEqual(both["boards"]["JFM"], {})
        verdict = p.evaluate(both)
        self.assertEqual((verdict["verdict"], p.failing_boards(verdict)), ("FAIL", ["JFM"]))
        no_snapshot = manifest()
        del no_snapshot["quarters"]["2026-q4"]["boards"]["MEJ"]["snapshot"]
        self.assertIn(
            p.INPUT_INVALID,
            p.evaluate({"quarter": "2027-q1", "boards": {"JFM": {}, "MEJ": read_board(World(), no_snapshot)}})[
                "reasons"
            ],
        )

    def test_a_key_jira_does_not_return_leaves_the_list_incomplete(self):
        world = World()
        del world.rows["MEJ-302"]
        data = read_board(world)
        self.assertEqual(
            p.evaluate({"quarter": "2027-q1", "boards": {"JFM": data, "MEJ": data}})["reasons"],
            [p.MEMBERSHIP_INCOMPLETE],
        )

    def test_a_completion_with_no_changelog_or_dated_outside_the_quarter_is_not_verified(self):
        world = World()
        del world.histories["MEJ-300"]
        data = read_board(world)
        self.assertEqual(data["completions"]["MEJ-300"], {"date": None, "source": "unverified"})
        self.assertIn(
            p.COMPLETION_DATE_UNVERIFIED,
            p.evaluate({"quarter": "2027-q1", "boards": {"MEJ": data, "JFM": data}})["reasons"],
        )
        early = read_board(World(done_date="2026-12-30T09:00:00.000-0600"))
        self.assertTrue(early["completions"]["MEJ-300"]["ambiguous"])

    def test_a_drifted_q4_figure_is_a_history_failure(self):
        mf = manifest(snapshot={"version": "v1", "figures": {"MEJ-1": [2, 2]}})
        data = read_board(World(), mf)
        self.assertEqual(
            p.evaluate({"quarter": "2027-q1", "boards": {"MEJ": data, "JFM": data}})["reasons"], [p.HISTORY_DRIFT]
        )

    def test_manual_and_automatic_closures_stay_apart_and_a_cancelled_one_is_neither(self):
        world = World(done_q1=("MEJ-300", "MEJ-301", "MEJ-302"), labels={"MEJ-301": ["auto-closed"]})
        world.rows["MEJ-302"]["fields"]["resolution"] = {"name": "Won't Do"}
        world.rows["MEJ-302"]["fields"]["labels"] = ["superseded"]
        data = read_board(world)
        self.assertEqual(data["recurring"]["manual_keys"], ["MEJ-300"])
        self.assertEqual(data["recurring"]["auto_keys"], ["MEJ-301"])
        self.assertEqual(data["recurring"]["total"], 2)
        self.assertNotIn("MEJ-302", data["completions"])

    def test_a_closure_before_tracking_cannot_be_told_apart_so_it_fails_the_recurring_check(self):
        data = read_board(World(), environ={})
        self.assertIn(
            p.RECURRING_NOT_DISTINCT,
            p.evaluate({"quarter": "2027-q1", "boards": {"MEJ": data, "JFM": data}})["reasons"],
        )

    def test_discrepancies_with_the_legacy_goal_list_are_counted_and_explained_ones_are_not(self):
        world = World(legacy=Q1[:2])  # the legacy Epic does not list MEJ-302
        data = read_board(world)
        self.assertEqual(data["discrepancies"], [{"key": "MEJ-302", "explanation": None}])
        explained = {"by": "MC01", "what": "added after the freeze", "source": "approval"}
        data = read_board(world, manifest({"explanations": {"MEJ-302": explained}}))
        self.assertEqual(data["discrepancies"], [{"key": "MEJ-302", "explanation": explained}])
        two = read_board(World(legacy=Q1[:1]))
        self.assertEqual(
            p.evaluate({"quarter": "2027-q1", "boards": {"MEJ": two, "JFM": two}})["reasons"], [p.TOLERANCE_EXCEEDED]
        )

    def test_any_jira_failure_leaves_the_board_empty_not_guessed(self):
        class Down(World):
            def get(self, path, params):
                raise __import__("lifeos.platform.jira", fromlist=["JiraError"]).JiraError("JIRA_DOWN")

        self.assertEqual(read_board(Down()), {})


if __name__ == "__main__":
    unittest.main()
