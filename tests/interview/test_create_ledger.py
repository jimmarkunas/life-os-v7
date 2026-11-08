"""LIFE-361 (REQ-5): a new interview pursuit finds or creates its Job Ledger row and its rounds go under the row page; no Active Opportunities container is needed.
Synthetic ExampleCo rows only."""
import unittest
from unittest.mock import patch
from lifeos.interview import notion, stage
from lifeos.interview.models import ParentQuery, RoundQuery
from lifeos.platform.notion_client import NotionError
from tests.interview.test_create import WritableFake
from tests.interview.test_notion import ENV, block

PURSUIT = ParentQuery("ExampleCo", "Program Manager")
ROUND = RoundQuery(True, "2026-10-01", ordinal=1)
KEY = "a" * 64


def no_container(client):
    client.blocks["root"] = [b for b in client.blocks["root"] if b.get("type") != "child_page"]      # no Active Opportunities container anywhere
    return client


def jobs_body(rid):
    """A Jobs OS row body: the hidden v7-jd marker paragraph and the two-column description."""
    return [block("⁠", id=f"{rid}-jd"), {"id": f"{rid}-cols", "type": "column_list", "has_children": False, "column_list": {}}]


def run(client, query=ROUND, live=True, pursuit=PURSUIT):
    return stage.run(10, live, environ=ENV, client=client, evidence=[(pursuit, query)])


class FindOrCreateRow(unittest.TestCase):
    def test_no_row_gets_exactly_one_manual_row_and_a_replay_creates_nothing(self):          # GWT-IC-2
        client = no_container(WritableFake())
        result = run(client)
        self.assertEqual((result["why"], result["parents_created"], result["rows_created"], result["readback_ok"]), ({"parent_created": 1}, 1, 1, 1))
        self.assertEqual(list(client.rows), ["created-1"])
        row = client.rows["created-1"]
        self.assertEqual([client._value(row[k]) for k in ("Job", "Company", "Stage")], ["Program Manager", "ExampleCo", "Applied"])
        self.assertEqual(row["Source Types"], {"multi_select": [{"name": "Manual"}]})
        self.assertEqual(notion.text(client.blocks["created-1"][0]), notion.FOLD)                   # REQ-5 row layout: the machine fold first
        self.assertIn(notion.JIM_NOTES, [notion.text(b) for b in client.blocks["created-1"]])
        posts = len(client.posts)
        result = run(client, RoundQuery(True))                                                      # the same pursuit again, no new round evidence
        self.assertEqual(result["writes"], 0)
        self.assertEqual((len(client.posts), list(client.rows)), (posts, ["created-1"]))

    def test_round_one_goes_under_the_row_page_and_the_protected_regions_are_unchanged(self):   # GWT-IC-1
        client = no_container(WritableFake())
        run(client)
        before = notion.protected_snapshot(client, "created-1", "opportunity")
        result = run(client)
        self.assertEqual((result["why"], result["rounds_created"]), ({"round_created": 1}, 1))
        self.assertEqual(client.posts[-1]["parent"], {"type": "page_id", "page_id": "created-1"})
        self.assertEqual(notion.protected_snapshot(client, "created-1", "opportunity"), before)
        self.assertEqual(notion.ownership(client, "created-2", "round").value, "MACHINE")
        self.assertEqual(list(client.rows), ["created-1"])

    def test_a_jobs_row_found_by_its_key_keeps_its_body_below_the_new_sections(self):
        client = no_container(WritableFake())
        client.add_row("other", "ExampleCo", "Program Manager", key="b" * 64)                      # same names: only the Stable Job Key tells them apart
        client.add_row("job", "ExampleCo", "Program Manager", key=KEY)
        client.blocks["job"] = jobs_body("job")
        result = run(client, RoundQuery(True, "2026-10-01", ordinal=1, stable_job_key=KEY))
        self.assertEqual(result["why"], {"parent_created": 1})
        self.assertEqual(result.get("rows_created", 0), 0)
        self.assertEqual(client.posts, [])                                                          # no new row, no new page
        self.assertEqual(client._value(client.rows["job"]["Stage"]), "Applied")
        self.assertNotIn("Source Types", client.rows["job"])                                        # a Jobs OS row's own fields are not touched
        top = client.blocks["job"]
        self.assertEqual(notion.text(top[0]), notion.FOLD)
        self.assertEqual([b["id"] for b in top[-2:]], ["job-jd", "job-cols"])                       # the description is still there, whole and in order

    def test_a_row_found_by_its_key_under_other_names_is_not_written(self):
        client = no_container(WritableFake())
        client.add_row("job", "ExampleCo, Inc.", "Senior Director of Engineering", key=KEY)
        result = run(client, RoundQuery(True, "2026-10-01", ordinal=1, stable_job_key=KEY))
        self.assertEqual((result["why"], result["writes"]), ({"ledger_row_name_mismatch": 1}, 0))
        self.assertEqual((client.posts, client.blocks["job"]), ([], []))

    def test_a_row_found_by_name_moves_from_found_to_applied(self):
        client = no_container(WritableFake())
        client.add_row("job", "ExampleCo", "Program Manager", "Found")
        self.assertEqual(run(client)["why"], {"parent_created": 1})
        self.assertEqual(client._value(client.rows["job"]["Stage"]), "Applied")
        self.assertEqual(client.posts, [])

    def test_an_interrupted_create_is_finished_by_the_next_run_never_doubled(self):
        client = no_container(WritableFake())
        real = notion.write_tree
        with patch.object(notion, "write_tree", side_effect=NotionError("NOTION_NETWORK")):
            self.assertEqual(run(client)["why"], {"write_failed": 1})
        self.assertEqual(list(client.rows), ["created-1"])
        self.assertIsNone(client._value(client.rows["created-1"]["Stage"]))                         # Stage is the commit point: no reader lists the half-made row
        self.assertEqual(notion.parents(client, ENV).ledger, 0)
        with patch.object(notion, "write_tree", side_effect=real):
            result = run(client)
        self.assertEqual((result["why"], result.get("rows_created", 0)), ({"parent_created": 1}, 0))
        self.assertEqual(list(client.rows), ["created-1"])
        self.assertEqual(client._value(client.rows["created-1"]["Stage"]), "Applied")

    def test_a_dry_run_and_mail_alone_create_nothing(self):
        client = no_container(WritableFake())
        result = run(client, live=False)
        self.assertEqual((result["why"], result["writes_planned"], result["writes"]), ({"parent_create_allowed": 1}, 1, 0))
        result = run(client, RoundQuery(False))                                                     # not confirmed by Jim: nothing is created from mail alone
        self.assertEqual(result["why"], {"pursuit_unconfirmed": 1})
        self.assertEqual((client.posts, client.rows), ([], {}))

    def test_ledger_row_lookup(self):
        client = WritableFake()
        self.assertEqual(notion.ledger_row(client, "ds-synthetic", "ExampleCo", "Program Manager"), ("ledger_row_missing", None))
        client.add_row("a", "ExampleCo", "Program Manager")
        code, row = notion.ledger_row(client, "ds-synthetic", "ExampleCo", "Program Manager")
        self.assertEqual((code, row["id"]), ("ledger_row_found", "a"))
        client.add_row("b", "ExampleCo", "Program Manager", key=KEY)
        self.assertEqual(notion.ledger_row(client, "ds-synthetic", "ExampleCo", "Program Manager")[0], "ledger_row_ambiguous")
        self.assertEqual(notion.ledger_row(client, "ds-synthetic", "ExampleCo", "Program Manager", KEY)[1]["id"], "b")    # the key decides
        client.fail.add("/data_sources/ds-synthetic/query")
        self.assertEqual(notion.ledger_row(client, "ds-synthetic", "ExampleCo", "Program Manager"), ("ledger_unreadable", None))
        self.assertEqual(notion.ledger_row(client, "ds-synthetic", "", "Program Manager"), ("ledger_unreadable", None))

    def test_container_helpers_are_gone(self):
        self.assertFalse(hasattr(notion, "active_target") or hasattr(notion, "active_shape"))


if __name__ == "__main__":
    unittest.main()
