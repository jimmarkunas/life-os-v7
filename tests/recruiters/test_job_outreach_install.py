"""LIFE-47 / D222 TEMPORARY installer, synthetic only: the Hiring Pipeline test page with a Recruiter Outreach heading + five-row table right after the Hiring table.
Removed with lifeos/sources/job_outreach_install.py after the one production install."""
import unittest

from lifeos.platform import report_region as R
from lifeos.platform.notion_client import NotionError
from lifeos.sources import job_outreach as J, job_outreach_install as I
from tests.hiring.test_hiring import ENV as HIRING_ENV, page as hiring_page, writes

HIRING_TABLE = "0000000000000000000000000000a003"
ENV = {**HIRING_ENV, "HIRING_TABLE_BLOCK_ID": HIRING_TABLE, "GITHUB_EVENT_NAME": "workflow_dispatch", "INPUT_LIVE": "false", "INPUT_ALL": "false"}


class InsertTree:
    """Wraps the shared test tree with Notion's insert-after append (nested table rows included). `lose` = "before" fails the call without writing; "after"
    writes and then loses the reply."""
    def __init__(self, tree, lose=None):
        self.tree, self.lose, self.inserts = tree, lose, []

    def __getattr__(self, name):
        return getattr(self.tree, name)

    def call(self, method, path, body=None):
        return self.tree.call(method, path, body)

    def _make(self, parent, block, index=None):
        t = self.tree
        t.n += 1
        kind = block["type"]
        body = {k: v for k, v in block[kind].items() if k != "children"}
        if "rich_text" in body:
            body["rich_text"] = [{"plain_text": p["text"]["content"], "text": {"link": None, **p["text"]}} for p in body["rich_text"]]
        if kind == "table_row":
            body = {"cells": [[{"plain_text": p["text"]["content"], "text": {"link": None, **p["text"]}} for p in cell] for cell in body["cells"]]}
        made = t.add(f"{t.n:032x}", kind, parent, content=body, index=index)
        for child in block[kind].get("children") or []:
            self._make(made["id"], child)
        return made

    def call_once(self, method, path, body=None):
        self.tree.log.append((method, path))
        self.inserts.append(body)
        if self.lose == "before":
            raise NotionError("NOTION_HTTP_502")
        parts = path.strip("/").split("/")
        assert method == "PATCH" and parts[2] == "children" and "after" in body
        at = self.tree.kids[parts[1]].index(body["after"]) + 1
        made = [self._make(parts[1], b, at + n) for n, b in enumerate(body["children"])]
        self.tree._tripped(method, path)
        if self.lose == "after":
            raise NotionError("NOTION_HTTP_504")
        return {"results": made}


def report(between=None, job_outreach=False):
    t = hiring_page(extra_rows=1)
    at = t.kids["right"].index(HIRING_TABLE) + 1
    if between:
        t.add("between", between, "right", index=at)
        at += 1
    t.add("rec-h", "heading_3", "right", text="🤝 Recruiter Outreach (Updated 11:47 PM CT)", index=at)
    t.table("rec-t", "right", [["Person — Employer", "Channel · Role", "Initial outreach · Conversation", "Next step · Due"]] + [[f"p{n}", "c", "i", "n"] for n in range(5)], index=at + 1)
    if job_outreach:
        t.add("old-jo", "heading_3", "left", text=J.TITLE)
    return t


class Installer(unittest.TestCase):
    def run_it(self, t, mode="install", env=ENV, lose=None):
        client = InsertTree(t, lose)
        return I.run(0, False, environ={**env, "JOB_OUTREACH_INSTALL": mode}, client=client), client

    def test_without_the_manual_mode_nothing_is_read_or_written(self):
        t = report()
        for mode in ("", "none", "INSTALL", "all"):
            self.assertEqual(self.run_it(t, mode)[0], {"job_outreach_install": "NOT_REQUESTED"})
        self.assertEqual(t.log, [])

    def test_a_scheduled_or_tick_run_is_refused(self):
        for event in ("workflow_run", "schedule", "push"):
            with self.assertRaises(I.InstallError) as caught:
                self.run_it(report(), env={**ENV, "GITHUB_EVENT_NAME": event})
            self.assertIn("MANUAL_ONLY", str(caught.exception))

    def test_a_missing_event_is_refused_before_any_read(self):
        t = report()
        with self.assertRaises(I.InstallError) as caught:
            self.run_it(t, env={k: v for k, v in ENV.items() if k != "GITHUB_EVENT_NAME"})
        self.assertIn("MANUAL_ONLY", str(caught.exception))
        self.assertEqual(t.log, [])

    def test_actually_write_or_run_all_ticked_or_unknown_is_refused_before_any_read(self):
        for change in ({"INPUT_LIVE": "true"}, {"INPUT_ALL": "true"}, {"INPUT_LIVE": ""}, {"INPUT_ALL": "TRUE"}):
            for mode in ("validate", "install"):
                t = report()
                with self.assertRaises(I.InstallError) as caught:
                    self.run_it(t, mode, env={**ENV, **change})
                self.assertIn("CONFLICTING_INPUTS", str(caught.exception))
                self.assertEqual(t.log, [])
        for name in ("INPUT_LIVE", "INPUT_ALL"):
            with self.assertRaises(I.InstallError):
                self.run_it(report(), env={k: v for k, v in ENV.items() if k != name})

    def test_a_copied_or_wrong_page_is_refused_before_any_write(self):
        t = report()
        t.add("0000000000000000000000000000b004", "callout", "right")
        t.blocks["0000000000000000000000000000b004"]["parent"] = {"type": "page_id", "page_id": "page-2"}       # the JIRA anchor lives on another page
        for mode in ("validate", "install"):
            with self.assertRaises(I.InstallError) as caught:
                self.run_it(t, mode, env={**ENV, "JIRA_CARD_BLOCK_ID": "0000000000000000000000000000b004"})
            self.assertIn("WRONG_PAGE", str(caught.exception))
        with self.assertRaises(I.InstallError) as caught:
            self.run_it(t, env={k: v for k, v in ENV.items() if k != "JIRA_CARD_BLOCK_ID"})
        self.assertIn("NOT_CONFIGURED:JIRA_CARD_BLOCK_ID", str(caught.exception))
        self.assertEqual(writes(t), [])

    def test_a_command_center_change_during_the_write_fails_the_proof(self):
        t = report()
        t.trip = ("/children", lambda tree: tree.blocks["dcc-p"]["paragraph"]["rich_text"][0].update(plain_text="chatgpt edited"))
        with self.assertRaises(I.InstallError) as caught:
            self.run_it(t)
        self.assertIn("PROTECTED_REGION_CHANGED", str(caught.exception))

    def test_validate_reads_only_and_reports_the_preflight(self):
        t = report()
        before = t.snapshot()
        done, client = self.run_it(t, "validate")
        self.assertEqual((done["job_outreach_install"], done["writes"], done["recruiter_rows"], done["recruiter_outreach_next"], done["job_outreach_present"]),
                         ("validated", 0, 5, True, False))
        self.assertEqual((writes(t), client.inserts, t.snapshot()), ([], [], before))

    def test_install_is_one_insert_after_the_hiring_table_and_the_ongoing_stage_accepts_it(self):
        t = report()
        others = t.snapshot()
        done, client = self.run_it(t)
        self.assertEqual((done["job_outreach_install"], done["writes"], done["reply"]), ("installed", 1, "ok"))
        self.assertEqual(len(client.inserts), 1)
        self.assertEqual(client.inserts[0]["after"], HIRING_TABLE)
        self.assertEqual([b["type"] for b in client.inserts[0]["children"]], ["heading_3", "paragraph", "table"])
        right = t.kids["right"]
        at = right.index(HIRING_TABLE)
        head, status, table = right[at + 1:at + 4]
        self.assertEqual((head, table), (done["JOB_OUTREACH_CARD_BLOCK_ID"], done["JOB_OUTREACH_TABLE_BLOCK_ID"]))
        self.assertEqual(right[at + 4:at + 6], ["rec-h", "rec-t"])
        self.assertEqual((R.plain(t.blocks[head]), R.plain(t.blocks[status])), ("🤝 Job Outreach", J.EMPTY))
        self.assertEqual([R.cells_of(t.blocks[r]) for r in t.kids[table]], [["Job", "Outreach", "Next follow-up", "Notes"]])
        self.assertEqual({k: v for k, v in t.snapshot().items() if k in others}, others)        # every block that existed is byte-for-byte unchanged
        env = {**ENV, "JOB_OUTREACH_CARD_BLOCK_ID": head, "JOB_OUTREACH_TABLE_BLOCK_ID": table}
        self.assertTrue(J.present(t, env, "Updated 2:00 PM CT", J.cells([]), True, J.status_line([]))["verified"])
        with self.assertRaises(I.InstallError) as caught:                                       # once configured, the installer refuses
            self.run_it(t, env=env)
        self.assertIn("ALREADY_CONFIGURED", str(caught.exception))

    def test_a_second_install_finds_it_present_and_writes_nothing(self):
        t = report()
        self.run_it(t)
        count = len(writes(t))
        with self.assertRaises(I.InstallError) as caught:
            self.run_it(t)
        self.assertIn("ALREADY_PRESENT", str(caught.exception))
        self.assertEqual(len(writes(t)), count)

    def test_preconditions_stop_before_any_write(self):
        for tree, code in ((report(between="divider"), "RECRUITER_OUTREACH_NOT_NEXT"), (report(job_outreach=True), "ALREADY_PRESENT")):
            with self.assertRaises(I.InstallError) as caught:
                self.run_it(tree)
            self.assertIn(code, str(caught.exception))
            self.assertEqual(writes(tree), [])
        with self.assertRaises(I.InstallError) as caught:
            self.run_it(report(), env={**ENV, "HIRING_TABLE_BLOCK_ID": "0000000000000000000000000000a009"})
        self.assertIn("PINNED_BLOCK_MISMATCH", str(caught.exception))
        with self.assertRaises(I.InstallError) as caught:
            self.run_it(report(), env={k: v for k, v in ENV.items() if k != "HIRING_TABLE_BLOCK_ID"})
        self.assertIn("NOT_CONFIGURED", str(caught.exception))

    def test_a_lost_reply_is_read_back_never_retried(self):
        t = report()
        done, client = self.run_it(t, lose="after")                                             # the write landed, the reply did not
        self.assertEqual((done["job_outreach_install"], done["reply"], len(client.inserts)), ("installed", "NOTION_HTTP_504", 1))
        t = report()
        with self.assertRaises(I.InstallError) as caught:                                       # nothing landed: reported, not retried
            self.run_it(t, lose="before")
        self.assertIn("NOT_VERIFIED", str(caught.exception))
        self.assertEqual(len([w for w in t.log if w[0] == "PATCH"]), 1)

    def test_a_neighbour_changed_during_the_write_fails_the_proof(self):
        t = report()
        t.trip = ("/children", lambda tree: tree.blocks["rec-t-r3"]["table_row"]["cells"][0][0].update(plain_text="edited"))
        with self.assertRaises(I.InstallError) as caught:
            self.run_it(t)
        self.assertIn("PROTECTED_REGION_CHANGED", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
