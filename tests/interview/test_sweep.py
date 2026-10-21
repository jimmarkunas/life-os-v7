"""LIFE-88 (Jim, Oct 21): every opportunity page, Active and Retired, becomes the new page: classified by shape, converted a few per run through the existing writers, Jim's content proven unchanged,
the sweep stopped by the first failure. Everything here is synthetic."""
import copy
import unittest
from lifeos.interview import create, notion, stage, template
from lifeos.platform.notion_client import NotionError
from lifeos.sources import interview_sweep as sweep
from tests.interview.test_create import ColumnFake
from tests.interview.test_notion import ENV, block, page

CONVERT = {**ENV, "INTERVIEW_CONVERT": "all"}


def human(client, page_id, title, notes=("My own notes", "Second note")):
    client.pages[page_id] = page(title)
    client.blocks[page_id] = [block(notes[0], id=f"{page_id}-a"), block("Recruiter", "heading_3", f"{page_id}-h"), block(notes[1], id=f"{page_id}-b", has_children=True)]
    client.blocks[f"{page_id}-b"] = [block("nested detail", id=f"{page_id}-n")]


def fold_marker(client, page_id, lines):
    fold = dict(template.fold(lines[0]), id=f"{page_id}-fold", has_children=True)
    client.blocks[f"{page_id}-fold"] = [block(line, id=f"{page_id}-fl{i}") for i, line in enumerate(lines)]
    return fold


class SweepFake(ColumnFake):
    def __init__(self):
        super().__init__()
        human(self, "opportunity", "Synthetic — Manager")
        human(self, "retired", "Retired — Manager", ("Old notes", "More old notes"))
        self.pages["opportunity"]["parent"] = {"type": "block_id", "block_id": "left"}
        self.pages["retired"]["parent"] = {"type": "block_id", "block_id": "right"}


def scan_of(client):
    return notion.parent_scan(client, "root")


def sweep_run(client, environ=CONVERT, live=True):
    return sweep.run(environ, live, client, scan_of(client), lambda company, role: "synthetic-key", lambda c, page_id, context: None)


class ClassifyTests(unittest.TestCase):
    def test_every_shape_is_told_apart_by_the_page_itself(self):
        client = SweepFake()
        marker = notion.MARKERS["opportunity"]
        client.blocks["skel"] = [block(marker, id="s0"), block("Live Notes", "heading_2", "s1"), block("Raw Notes", "heading_2", "s2"), block("Interview Rounds", "heading_2", "s3")]
        client.blocks["pre"] = [block(marker, id="p0"), block("Stage", id="p1"), block(notion.JIM_NOTES, "heading_2", "p2"), block("mine", id="p3")]
        client.blocks["ui3b"] = [fold_marker(client, "ui3b", [marker]), block("Canonical opportunity parent", "callout", "u1"), block(notion.LEGACY_NOTES, "heading_2", "u2")]
        client.blocks["adopt"] = [block(notion.ADOPT, id="a0"), block("my notes", id="a1")]
        client.blocks["odd"] = [block("v7-interview:1;owner=someone-else", id="o0")]
        client.blocks["empty"] = []
        got = {key: sweep.classify(client, key) for key in ("skel", "pre", "ui3b", "adopt", "odd", "empty", "opportunity")}
        self.assertEqual(got, {"skel": "skeleton", "pre": "prefold", "ui3b": "ui3b", "adopt": "adopt_line", "odd": "unrecognized", "empty": "human", "opportunity": "human"})

    def test_a_current_page_is_current(self):
        from tests.interview.test_ui3c import written
        client, page_id = written()
        self.assertEqual(sweep.classify(client, page_id), "current")


class SweepTests(unittest.TestCase):
    def test_inventory_counts_every_page_active_and_retired_and_writes_nothing(self):
        client = SweepFake()
        before, start = copy.deepcopy(client.blocks), len(client.calls)
        counts = sweep_run(client, {**ENV, "INTERVIEW_CONVERT": "inventory"})
        self.assertEqual((counts["mode"], counts["inventory"], counts["retired"], counts["converted"], counts["waiting"], counts["writes"]), ("inventory", {"human": 2}, 1, 0, 2, 0))
        self.assertEqual(client.blocks, before)
        self.assertFalse([c for c in client.calls[start:] if c[0] != "GET"])

    def test_interview_pages_are_counted_by_who_wrote_them(self):
        client = SweepFake()
        cp = lambda id, title: {"id": id, "type": "child_page", "child_page": {"title": title}}
        client.blocks["opportunity"] += [cp("r1", "Interview 1: Jane Doe (9/10/26)"), cp("r2", "Prep scribbles")]
        for key in ("r1", "r2"):
            client.pages[key], client.blocks[key] = page(key, "opportunity"), [block("mine")]
        counts = sweep_run(client, {**ENV, "INTERVIEW_CONVERT": "inventory"})
        self.assertEqual(counts["rounds"], {"machine": 0, "human_titled": 1, "human_other": 1, "unread": 0})

    def test_off_means_no_mode(self):
        for value in ("", "off", "ALL?", None):
            self.assertEqual(sweep.mode({"INTERVIEW_CONVERT": value}), "")
        self.assertEqual((sweep.mode({"INTERVIEW_CONVERT": " All "}), sweep.cap({}), sweep.cap({"INTERVIEW_CONVERT_CAP": "5"}), sweep.cap({"INTERVIEW_CONVERT_CAP": "99"})), ("all", 2, 5, 2))
        self.assertEqual((sweep.mode({"INTERVIEW_CONVERT": "all:1"}), sweep.cap({"INTERVIEW_CONVERT": "all:1", "INTERVIEW_CONVERT_CAP": "5"}), sweep.mode({"INTERVIEW_CONVERT": "inventory:3"})), ("all", 1, ""))

    def test_a_targeted_run_converts_exactly_the_named_page(self):
        client = SweepFake()
        counts = sweep_run(client, {**CONVERT, "INTERVIEW_CONVERT": "all:1@" + "a" * 32})
        self.assertEqual((sweep.target({"INTERVIEW_CONVERT": "all:1@" + "a" * 32}), counts["converted"], counts["waiting"]), ("a" * 32, 0, 0))
        self.assertEqual((sweep.classify(client, "opportunity"), sweep.classify(client, "retired")), ("human", "human"))
        for bad in ("inventory:2", "inventory@" + "a" * 32, "all:1@xyz", "all@", "all:100"):
            self.assertEqual(sweep.mode({"INTERVIEW_CONVERT": bad}), "")

    def test_a_hand_made_page_is_adopted_with_every_one_of_jims_blocks_in_place_and_the_retired_one_too(self):
        client = SweepFake()
        kept = {key: [(b["id"], notion.text(b)) for b in client.blocks[key]] for key in ("opportunity", "retired")}
        counts = sweep_run(client)
        self.assertEqual((counts["converted"], counts["failed"], counts["waiting"], counts["writes_planned"] > 0), (2, 0, 0, True))
        self.assertEqual(counts["why"], {"parent_adopted": 2})
        for key in ("opportunity", "retired"):
            tops = client.blocks[key]
            self.assertTrue(notion.is_fold(tops[0]))
            self.assertNotIn(notion.ADOPT, [notion.text(b) for b in tops])                          # the adopt line is consumed
            tail = [(b["id"], notion.text(b)) for b in tops if b["id"] in dict(kept[key])]
            self.assertEqual(tail, kept[key])                                                       # Jim's blocks, same ids, same order
            self.assertEqual(sweep.classify(client, key), "current")                                 # and a second sweep finds nothing to do
        again = sweep_run(client)
        self.assertEqual((again["inventory"], again["converted"], again["writes"]), ({"current": 2}, 0, 0))

    def test_the_cap_limits_pages_per_run_and_the_rest_wait(self):
        client = SweepFake()
        counts = sweep_run(client, {**CONVERT, "INTERVIEW_CONVERT_CAP": "1"})
        self.assertEqual((counts["converted"], counts["waiting"]), (1, 1))
        self.assertEqual(sweep.classify(client, "retired"), "human")

    def test_the_first_failure_stops_the_sweep_and_leaves_the_next_page_alone(self):
        client = SweepFake()
        real = client.call
        def failing(method, path, body=None):
            if method == "PATCH" and path.endswith("/opportunity/children") and body and body.get("position"):
                raise NotionError("private error body")
            return real(method, path, body)
        client.call = failing
        before = copy.deepcopy(client.blocks)
        counts = sweep_run(client)
        self.assertEqual((counts["converted"], counts["failed"], counts["why"]), (0, 1, {"write_failed": 1}))
        self.assertEqual(client.blocks, before)                                                      # nothing was written to either page

    def test_a_page_that_changes_under_the_write_is_reported_and_stops_the_sweep(self):
        client = SweepFake()
        real = client.call
        def sneaky(method, path, body=None):
            out = real(method, path, body)
            if method == "PATCH" and body and body.get("position"):
                client.blocks["opportunity"][-1] = block("edited", id=client.blocks["opportunity"][-1]["id"])
            return out
        client.call = sneaky
        counts = sweep_run(client)
        self.assertEqual((counts["converted"], counts["failed"], counts["why"]), (0, 1, {"readback_protected_changed": 1}))
        self.assertEqual(sweep.classify(client, "retired"), "human")

    def test_a_dry_run_plans_and_writes_nothing(self):
        client = SweepFake()
        before = copy.deepcopy(client.blocks)
        counts = sweep_run(client, live=False)
        self.assertEqual((counts["writes"], counts["writes_planned"], counts["why"]), (0, 2, {"adopt_line_allowed": 2}))
        self.assertEqual(client.blocks, before)

    def test_a_page_with_any_life_os_line_is_never_given_an_adopt_line(self):
        client = SweepFake()
        client.blocks["opportunity"].insert(1, block("v7-interview:1;owner=machine;kind=round", id="x"))
        out = {}
        self.assertEqual(create.begin_adopt(client, "opportunity", True, out), "adopt_line_conflict")
        self.assertEqual(out, {})


class MigrationTests(unittest.TestCase):
    def test_a_page_with_the_plain_marker_line_is_migrated_once_with_jims_notes_and_rounds_untouched(self):
        client = SweepFake()
        marker = notion.MARKERS["opportunity"]
        client.blocks["opportunity"] = [block(marker, id="m0"), block("Stage: old", id="m1"), block("Role text", "callout", "m2"), block(notion.JIM_NOTES, "heading_2", "jh"),
                                        block("Your space", "callout", "jc"), block("Jim's private note", id="jn"), {"id": "r1", "type": "child_page", "child_page": {"title": "Interview 1"}}]
        client.pages["r1"] = page("Interview 1", "opportunity")
        client.blocks["r1"] = [block("round notes")]
        tail = notion.human_tail(client, "opportunity")
        counts = sweep_run(client, {**CONVERT, "INTERVIEW_CONVERT_CAP": "1"})
        self.assertEqual((counts["why"], counts["converted"]), ({"parent_refreshed": 1}, 1))
        self.assertEqual(sweep.classify(client, "opportunity"), "current")
        self.assertEqual(notion.human_tail(client, "opportunity"), tail)
        self.assertEqual([b["id"] for b in client.blocks["opportunity"]][-4:], ["jh", "jc", "jn", "r1"])
        self.assertNotIn("m1", [b["id"] for b in client.blocks["opportunity"]])                       # V7's own old region is replaced

    def test_a_prefold_page_with_a_round_page_inside_its_machine_region_is_not_recognized(self):
        client = SweepFake()
        marker = notion.MARKERS["opportunity"]
        client.blocks["opportunity"] = [block(marker, id="m0"), {"id": "r1", "type": "child_page", "child_page": {"title": "Interview 1"}}, block(notion.JIM_NOTES, "heading_2", "jh")]
        client.pages["r1"], client.blocks["r1"] = page("Interview 1", "opportunity"), [block("round notes")]
        before = copy.deepcopy(client.blocks)
        counts = sweep_run(client, {**CONVERT, "INTERVIEW_CONVERT_CAP": "1"})
        self.assertEqual((counts["failed"], counts["why"]), (1, {"parent_unrecognized": 1}))
        self.assertEqual(client.blocks["opportunity"], before["opportunity"])


if __name__ == "__main__":
    unittest.main()
