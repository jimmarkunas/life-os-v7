"""LIFE-693: the proof run after every deploy: pass is recorded with its commit and says nothing; fail raises ONE page with both commits and the failing code and pauses releases until a proof passes;
two deploys close together each get an entry (the first is superseded when unfinished); nothing rolls back by itself."""
import datetime as dt
import io
import unittest
import zipfile

from lifeos.platform import release_proof as rp

NOW = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc)
A, B, C = "a" * 40, "b" * 40, "c" * 40


def at(minutes_ago):
    return rp.stamp(NOW - dt.timedelta(minutes=minutes_ago))


def run(run_id, minutes_ago, status="completed", conclusion="success"):
    return {"id": run_id, "created_at": at(minutes_ago), "updated_at": at(minutes_ago - 2), "status": status, "conclusion": conclusion}


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        for name, text in files.items():
            handle.writestr(name, text)
    return buffer.getvalue()


class Record(unittest.TestCase):
    def test_the_record_round_trips_and_ignores_notes_a_person_adds(self):
        entries = [rp.Entry(B, rp.FAIL, at(5), at(3), 7, A, "RUN/BAD_THING"), rp.Entry(A, rp.PASS, at(60), at(58), 6)]
        body = rp.render_record(entries) + "\n\nJim: looking at this"
        self.assertEqual(rp.parse_record(body), entries)
        self.assertEqual(rp.parse_record(None), [])

    def test_the_record_keeps_only_the_newest_entries(self):
        many = [rp.Entry("%040x" % i, rp.PASS, at(i)) for i in range(1, 40)]
        self.assertEqual(len(rp.parse_record(rp.render_record(many))), rp.KEEP)


class Pass(unittest.TestCase):
    def test_a_new_deploy_starts_one_proof_run_and_nothing_else(self):
        d = rp.decide(NOW, B, A, [], [], False)
        self.assertEqual((d.dispatch.commit, d.dispatch.status, d.dispatch.previous), (B, rp.PENDING, A))
        self.assertIsNone(d.failed)
        self.assertIsNone(d.flag)
        self.assertEqual(rp.decide(NOW, B, A, list(d.entries), [], False).dispatch, None)        # the next watch starts nothing more

    def test_a_passing_proof_is_recorded_with_its_commit_and_raises_nothing(self):
        pending = rp.Entry(B, rp.PENDING, at(10), previous=A)
        d = rp.decide(NOW, B, A, [pending], [run(11, 9)], False)
        self.assertEqual((d.entries[0].status, d.entries[0].commit, d.entries[0].run), (rp.PASS, B, 11))
        self.assertTrue(d.entries[0].finished)
        self.assertEqual((d.failed, d.recovered, d.flag, d.dispatch), (None, None, None, None))

    def test_a_proof_still_running_waits(self):
        pending = rp.Entry(B, rp.PENDING, at(10), previous=A)
        d = rp.decide(NOW, B, A, [pending], [run(11, 9, "in_progress", None)], False)
        self.assertEqual((d.entries[0].status, d.entries[0].run, d.failed, d.flag), (rp.PENDING, 11, None, None))

    def test_an_unknown_deployed_commit_changes_nothing(self):
        d = rp.decide(NOW, None, None, [], [], False)
        self.assertEqual((d.entries, d.dispatch, d.flag, d.failed), ((), None, None, None))


class Fail(unittest.TestCase):
    def test_a_failing_proof_raises_one_page_with_both_commits_the_code_and_the_revert(self):
        pending = rp.Entry(B, rp.PENDING, at(10), previous=A)
        d = rp.decide(NOW, B, A, [pending], [run(11, 9, conclusion="failure")], False, lambda run_id: "TICK/PICK_FAILED")
        self.assertEqual((d.failed.commit, d.failed.code, d.flag), (B, "TICK/PICK_FAILED", True))
        title, body = rp.failure_text(d.failed)
        self.assertIn(B[:12], title)
        self.assertIn(A[:12], title)
        for needle in ("TICK/PICK_FAILED", f"git revert --no-edit {A}..{B}", "wait until a proof run passes"):
            self.assertIn(needle, body)

    def test_the_alert_is_raised_once_while_the_pause_stays(self):
        failed = rp.Entry(B, rp.FAIL, at(30), at(28), 11, A, rp.CODE_FAILED)
        d = rp.decide(NOW, B, A, [failed], [run(11, 29, conclusion="failure")], True)
        self.assertEqual((d.failed, d.flag, d.dispatch), (None, True, None))                    # no second page, no new run yet (under an hour)

    def test_a_failed_proof_without_a_log_falls_back_to_the_fixed_code(self):
        pending = rp.Entry(B, rp.PENDING, at(10))
        d = rp.decide(NOW, B, None, [pending], [run(11, 9, conclusion="failure")], False)
        self.assertEqual(d.failed.code, rp.CODE_FAILED)
        self.assertIn("none known", rp.failure_text(d.failed)[0])
        self.assertEqual(rp.revert_line(d.failed), f"git revert --no-edit {B}")

    def test_a_proof_that_never_appears_or_never_finishes_fails_after_the_wait(self):
        lost = rp.Entry(B, rp.PENDING, at(rp.WAIT_MINUTES + 1))
        self.assertEqual(rp.decide(NOW, B, A, [lost], [], False).failed.code, rp.CODE_NOT_FOUND)
        stuck = rp.Entry(B, rp.PENDING, at(rp.WAIT_MINUTES + 1))
        self.assertEqual(rp.decide(NOW, B, A, [stuck], [run(11, rp.WAIT_MINUTES, "in_progress", None)], False).failed.code, rp.CODE_TIMED_OUT)
        self.assertIsNone(rp.decide(NOW, B, A, [rp.Entry(B, rp.PENDING, at(rp.WAIT_MINUTES - 1))], [], False).failed)


class Pause(unittest.TestCase):
    def test_while_paused_a_new_proof_starts_at_most_hourly_so_the_pause_cannot_deadlock(self):
        failed = rp.Entry(B, rp.FAIL, at(61), at(59), 11, A, rp.CODE_FAILED)
        d = rp.decide(NOW, B, A, [failed], [], True)
        self.assertEqual((d.dispatch.commit, d.flag), (B, None))                                # the flag stays open untouched until this run finishes
        self.assertEqual(rp.decide(NOW, B, A, [failed], [], False).dispatch, None)             # not paused: a failed commit is not retried

    def test_the_first_pass_lifts_the_pause_with_one_recovered_push(self):
        failed = rp.Entry(B, rp.FAIL, at(120), at(118), 11, A, rp.CODE_FAILED)
        retry = rp.Entry(C, rp.PENDING, at(10), previous=A)
        d = rp.decide(NOW, C, B, [retry, failed], [run(12, 9)], True)
        self.assertEqual((d.recovered.commit, d.flag, d.failed), (C, False, None))

    def test_a_failure_while_paused_stays_paused_and_pages_nothing_more(self):
        failed = rp.Entry(B, rp.FAIL, at(120), at(118), 11, A, rp.CODE_FAILED)
        retry = rp.Entry(C, rp.PENDING, at(10), previous=A)
        d = rp.decide(NOW, C, B, [retry, failed], [run(12, 9, conclusion="failure")], True)
        self.assertEqual((d.failed, d.recovered, d.flag), (None, None, True))


class TwoDeploys(unittest.TestCase):
    def test_two_deploys_within_ten_minutes_each_get_their_own_entry_and_the_first_is_superseded(self):
        first = rp.decide(NOW - dt.timedelta(minutes=8), B, A, [], [], False)
        second = rp.decide(NOW, C, B, list(first.entries), [], False)
        by_commit = {e.commit: e for e in second.entries}
        self.assertEqual(by_commit[B].status, rp.SUPERSEDED)
        self.assertEqual(by_commit[B].note, "superseded_by_" + C[:12])
        self.assertEqual((by_commit[C].status, second.dispatch.commit), (rp.PENDING, C))
        self.assertIsNone(second.failed)

    def test_each_pending_entry_claims_its_own_run(self):
        one, two = rp.Entry(B, rp.PENDING, at(8)), rp.Entry(C, rp.PENDING, at(4))
        d = rp.decide(NOW, C, B, [two, one], [run(21, 7), run(22, 3)], False)
        self.assertEqual({e.commit: (e.run, e.status) for e in d.entries}, {B: (21, rp.PASS), C: (22, rp.PASS)})


class FailingCode(unittest.TestCase):
    def test_the_failing_stage_and_code_come_from_the_job_log_and_nothing_else_is_kept(self):
        data = archive({"1_lint.txt": "all fine", "2_tick.txt": "noise\nTICK FAILED: PICK_FAILED extra words and data\n", "readme.md": "x"})
        self.assertEqual(rp.failing_code_in(data), "TICK/PICK_FAILED")
        self.assertEqual(rp.failing_code_in(archive({"1_a.txt": "ok"})), "")


if __name__ == "__main__":
    unittest.main()
