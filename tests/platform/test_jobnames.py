"""every reader of GitHub's job names accepts the plain name and the names a job gets when it calls a reusable workflow."""

import datetime as dt
import unittest

from lifeos.platform import chain_clocks, due, gate, jobnames, pipeline_ran

OLD = ["gate (tick 55)", "jira", "hiring_outcomes", "report", "chain", "finish", "prep"]
NEW_DOMAINS = [
    "run / gate (tick 55) / gate",
    "run / jira / jira",
    "run / hiring_outcomes / hiring_outcomes",
    "run / report",
    "run / chain",
]
NEW_HOURLY = ["finish / finish", "prep / prep", "report", "manual_fit / manual_fit"]


def job(name, conclusion="success"):
    return {"name": name, "status": "completed", "conclusion": conclusion, "started_at": None, "completed_at": None}


class Label(unittest.TestCase):
    def test_a_plain_name_and_each_called_shape_give_the_one_plain_name(self):
        self.assertEqual([jobnames.label(n) for n in OLD], OLD)
        self.assertEqual(
            [jobnames.label(n) for n in NEW_DOMAINS],
            ["gate (tick 55)", "jira", "hiring_outcomes", "report", "chain"],
        )
        self.assertEqual([jobnames.label(n) for n in NEW_HOURLY], ["finish", "prep", "report", "manual_fit"])
        self.assertEqual(jobnames.label(None), "")


class Readers(unittest.TestCase):
    def test_the_due_table_finds_an_earlier_success_under_the_old_and_the_new_names(self):
        started = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)
        run = {"run_started_at": "2026-11-27T12:00:00Z", "created_at": "2026-11-27T12:00:00Z"}
        for names in (["jira", "mail"], ["run / jira / jira", "run / mail / mail"]):
            jobs = [job(n) for n in names]
            found = due.last_success([run], lambda r, jobs=jobs: jobs, {"jira", "mail"})
            self.assertEqual(found, {"jira": started, "mail": started}, names)
        failed = [job("run / jira / jira", "failure")]
        self.assertEqual(due.last_success([run], lambda r: failed, {"jira"}), {})

    def test_the_chain_receipt_skips_the_gate_and_its_own_job_under_both_names(self):
        for names in (
            ["gate (tick 55)", "chain", "hiring"],
            ["run / gate (tick 55) / gate", "run / chain", "run / hiring / hiring"],
        ):
            rows = chain_clocks._jobs([{"id": 1, "jobs": [job(n) for n in names]}])
            self.assertEqual([row[1] for row in rows], ["hiring"], names)
            tick = {"created_at": "2026-11-27T11:00:00Z", "updated_at": "2026-11-27T12:30:00Z", "status": "completed"}
            listing = [
                {
                    "id": 9,
                    "event": "workflow_run",
                    "created_at": "2026-11-27T12:00:00Z",
                    "display_title": gate.DOMAINS_TITLE,
                }
            ]
            found = chain_clocks.correlated_runs(55, tick, listing, lambda rid, names=names: [job(n) for n in names])
            self.assertEqual([r["id"] for r in found], [9], names)  # the run is tied to its tick by the gate's name

    def test_the_pipeline_ran_check_reads_the_finish_job_under_both_names(self):
        for name in ("finish", "finish / finish"):
            self.assertTrue(pipeline_ran.ran([{"name": name, "conclusion": "success"}]), name)
            self.assertFalse(pipeline_ran.ran([{"name": name, "conclusion": "skipped"}]), name)
        self.assertFalse(pipeline_ran.ran([{"name": "manual_fit / manual_fit", "conclusion": "success"}]))


if __name__ == "__main__":
    unittest.main()
