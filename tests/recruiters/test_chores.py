"""LIFE-973, synthetic only: Action Status is the only source of truth for Done / Active / Needs review; every Needs Action row has a reply-by date (a set one is never replaced)."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.recruiters import notion as store, stage
from tests.recruiters.fakes import FakeRecruiters, ReadOnlyGmail, page

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=ZoneInfo("America/Chicago"))


def row(notion, pid):
    return store.read(next(p for p in notion.pages if p["id"] == pid))


class StatusSync(unittest.TestCase):
    def go(self, notion, live=True):
        return stage._chores({}, live, NOW, notion)

    def test_a_closed_status_makes_the_row_done_inactive_and_not_in_review(self):
        for status in ("Completed", "Dismissed", "Declined"):
            notion = FakeRecruiters([page("A B", pid="r1", action=status, done=False, active=True, review=True)])
            counts = self.go(notion)
            r = row(notion, "r1")
            self.assertEqual((r["done"], r["active"], r["needs_review"], counts["status_synced"], counts["failed"]), (True, False, False, 1, 0), status)

    def test_needs_action_reopens_done_and_inactive_rows_and_leaves_review_alone(self):
        notion = FakeRecruiters([page("A B", pid="r1", action="Needs Action", done=True, active=False, review=True, follow_up="2026-11-30")])
        self.go(notion)
        r = row(notion, "r1")
        self.assertEqual((r["done"], r["active"], r["needs_review"]), (False, True, True))

    def test_waiting_and_blank_rows_and_agreeing_rows_are_not_written(self):
        notion = FakeRecruiters([page("A B", pid="r1", action="Waiting", done=True), page("C D", pid="r2", done=True, active=False), page("E F", pid="r3", action="Completed", done=True, active=False),
                                 page("G H", pid="r4", action="Needs Action", follow_up="2026-11-30")])
        counts = self.go(notion)
        self.assertEqual((notion.writes, counts["status_synced"]), ([], 0))

    def test_a_dry_run_counts_and_writes_nothing(self):
        notion = FakeRecruiters([page("A B", pid="r1", action="Dismissed", active=True)])
        counts = self.go(notion, live=False)
        self.assertEqual((counts["would_sync"], counts["status_synced"], notion.writes), (1, 0, []))

    def test_a_write_that_does_not_read_back_is_counted_failed(self):
        notion = FakeRecruiters([page("A B", pid="r1", action="Dismissed", active=True)], drop_writes=True)
        counts = self.go(notion)
        self.assertEqual((counts["failed"], counts["status_synced"]), (1, 0))


class FollowUpDate(unittest.TestCase):
    def test_a_needs_action_row_without_a_date_gets_one_business_day_after_its_first_contact_else_its_last_contact(self):
        a = page("A B", pid="r1", action="Needs Action", last="2026-11-20T15:00:00+00:00")          # a Friday: the next business day is Monday
        b = page("C D", pid="r2", action="Needs Action", last="2026-11-24T15:00:00+00:00")
        b["properties"]["First Contact"] = {"type": "date", "date": {"start": "2026-11-18T15:00:00+00:00"}}
        notion = FakeRecruiters([a, b])
        counts = stage._chores({}, True, NOW, notion)
        self.assertEqual((row(notion, "r1")["follow_up"], row(notion, "r2")["follow_up"], counts["dates_set"]), ("2026-11-23", "2026-11-19", 2))

    def test_a_date_that_is_set_is_never_replaced_and_waiting_rows_get_none(self):
        notion = FakeRecruiters([page("A B", pid="r1", action="Needs Action", follow_up="2026-12-01"), page("C D", pid="r2", action="Waiting")])
        counts = stage._chores({}, True, NOW, notion)
        self.assertEqual((notion.writes, counts["dates_set"], row(notion, "r1")["follow_up"]), ([], 0, "2026-12-01"))

    def test_the_chores_run_when_the_mail_read_is_skipped(self):
        import unittest.mock
        from datetime import timedelta
        from lifeos.platform import mail_seen as seen
        from lifeos.recruiters import scan
        from tests.recruiters.test_incremental import AFTERNOON, MORNING, ROW, FakeLedger
        ledger = FakeLedger({seen.key("gmail", "a")}, last_read=AFTERNOON - timedelta(hours=1), kind=seen.NEW_MAIL, last_full=MORNING)
        notion = FakeRecruiters([page("A B", pid="r1", action="Dismissed", active=True)])
        with unittest.mock.patch.object(scan, "step_row", lambda: ROW):
            counts = stage.run(0, True, environ={"GITHUB_EVENT_NAME": "workflow_run"}, gmail=ReadOnlyGmail([]), outlook_accounts=[], notion=notion, now=AFTERNOON, ledger=ledger)
        self.assertEqual((counts["scan"], counts["status_synced"]), ("skipped", 1))


if __name__ == "__main__":
    unittest.main()
