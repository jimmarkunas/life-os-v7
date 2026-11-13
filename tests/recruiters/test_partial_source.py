"""LIFE-646: one unread source (an Outlook account answering with server errors) no longer fails the Recruiters stage when another source was read."""
import contextlib
import io
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from lifeos.platform import mail_seen as seen
from lifeos.platform.outlook import Outlook, OutlookError
from lifeos.recruiters import outreach_card, scan, stage
from tests.recruiters.fakes import FakeRecruiters, ReadOnlyGmail, mail
from tests.recruiters.test_incremental import AFTERNOON, FakeLedger, after_a_full_scan_at_9, signed


class DownOutlook:
    """An account whose listing answers with a server error (what Microsoft returned on Nov 12 and 13 2026)."""

    def messages(self, *args, **kwargs):
        raise OutlookError("OUTLOOK_HTTP_502")


def run(records, outlook, ledger=None, live=True, notion=None, environ=None):
    notion = notion or FakeRecruiters()
    with contextlib.redirect_stdout(io.StringIO()):
        counts = stage.run(0, live, environ=environ or {}, gmail=ReadOnlyGmail(records), outlook_accounts=outlook, notion=notion, now=AFTERNOON, ledger=ledger)
    return counts, notion


class PartialSourceTests(unittest.TestCase):
    def test_gmail_is_written_while_one_outlook_account_is_down_and_the_run_does_not_fail(self):
        counts, notion = run([signed("g1")], [("personal", DownOutlook())])
        self.assertEqual((counts["sources_failed"], counts["unread"], counts["created"], counts["failed"], counts["notion"]), (1, ["Outlook personal"], 1, 0, "ok"))
        self.assertEqual(len(notion.pages), 1)

    def test_nothing_is_concluded_from_the_unread_source(self):
        counts, notion = run([], [("personal", DownOutlook())])                         # Gmail read fine and holds no recruiter mail
        self.assertEqual((counts["created"], counts["updated"], notion.writes), (0, 0, []))

    def test_a_client_that_could_not_even_be_built_is_unread_too(self):
        counts, _ = run([signed("g1")], [("work", None)])
        self.assertEqual((counts["sources_failed"], counts["unread"], counts["created"]), (1, ["Outlook work"], 1))

    def test_every_source_unread_still_fails_closed_and_writes_nothing(self):
        notion = FakeRecruiters()
        with mock.patch.object(stage.mail, "gmail_records", mock.Mock(side_effect=stage.mail.MailError("MAIL_SOURCE"))):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(stage.store.RecruitersError) as caught:
                stage.run(0, True, environ={}, gmail=ReadOnlyGmail([signed("g1")]), outlook_accounts=[("personal", DownOutlook())], notion=notion, now=AFTERNOON, ledger=None)
        self.assertEqual(str(caught.exception), "RECRUITERS_DEGRADED_SOURCE_INCOMPLETE")
        self.assertEqual(notion.writes, [])

    def test_a_dry_run_with_a_source_down_writes_nothing(self):
        counts, notion = run([signed("g1")], [("personal", DownOutlook())], live=False)
        self.assertEqual((counts["would_create"], counts["created"], notion.writes), (1, 0, []))

    def test_the_ledger_records_only_what_was_read_and_the_next_read_is_not_a_full_scan(self):
        ledger = FakeLedger()                                                            # nothing recorded yet: this is the 14-day scan
        run([signed("g1")], [("personal", DownOutlook())], ledger=ledger)
        keys, kind, _ = ledger.commits[0]
        self.assertEqual((keys, kind), ({seen.key("gmail", "g1")}, seen.FULL_SCAN))
        row = {"every_hours": 3, "full_scan_ct": ["09:00"]}
        later = AFTERNOON + timedelta(hours=4)
        self.assertEqual(scan.plan(later, row, (AFTERNOON, seen.FULL_SCAN, AFTERNOON), True)[:2], ("read", seen.NEW_MAIL))

    def test_the_unread_source_is_read_as_new_once_it_answers_again(self):
        ledger = after_a_full_scan_at_9(["g1"])
        counts, _ = run([signed("g1")], [("personal", DownOutlook())], ledger=ledger)
        self.assertEqual(counts["scan"], "new mail")
        self.assertEqual(ledger.commits[0][0], set())                                    # nothing new from Gmail, and none from the failed account

    def test_the_heading_names_the_unread_source_in_fixed_words(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "out"
            run([signed("g1")], [("personal", DownOutlook())], environ={"GITHUB_OUTPUT": str(out)})
            self.assertIn("scan_kind=14-day scan (Outlook personal unread)\n", out.read_text())
        for fine in ("new mail", "14-day scan", "14-day scan (Outlook personal unread)", "new mail (Gmail, Outlook work unread)"):
            self.assertTrue(outreach_card.KIND.fullmatch(fine), fine)
        for bad in ("14-day scan (pat@example.com unread)", "14-day scan (Outlook Personal Name unread)", "yesterday"):
            self.assertFalse(outreach_card.KIND.fullmatch(bad), bad)


class OutlookOutageTests(unittest.TestCase):
    """The shared client: after one mailbox has answered with a server error (after its own retries) every later request to it in the same job fails at once."""

    def client(self, token="refresh-1"):
        return Outlook("client", token, sleep=lambda s: None)

    def test_a_second_request_in_the_same_job_makes_no_network_call(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", {"RUNNER_TEMP": folder}):
            with mock.patch("lifeos.platform.outlook.refresh", return_value={"access_token": "t"}), \
                    mock.patch("lifeos.platform.rest.urllib.request.urlopen", side_effect=OSError("down")) as net:
                with self.assertRaises(OutlookError) as first:
                    self.client().get("/me/messages")
                calls_after_first = net.call_count
                with self.assertRaises(OutlookError) as second:                          # a different step, a new client for the same mailbox
                    self.client().get("/me/messages")
            self.assertEqual((str(first.exception), calls_after_first, net.call_count, str(second.exception)), ("OUTLOOK_NETWORK", 4, 4, "OUTLOOK_UNAVAILABLE_THIS_JOB"))

    def test_another_mailbox_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", {"RUNNER_TEMP": folder}):
            with mock.patch("lifeos.platform.outlook.refresh", return_value={"access_token": "t"}), \
                    mock.patch("lifeos.platform.rest.urllib.request.urlopen", side_effect=OSError("down")):
                with self.assertRaises(OutlookError):
                    self.client("refresh-1").get("/me/messages")
            self.assertTrue(self.client("refresh-1")._down and Path(self.client("refresh-1")._down).exists())
            self.assertFalse(Path(self.client("refresh-2")._down).exists())

    def test_outside_a_runner_nothing_is_written_and_nothing_is_skipped(self):
        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("RUNNER_TEMP", None)
            self.assertIsNone(self.client()._down)

    def test_a_client_error_such_as_a_denied_request_is_not_an_outage(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", {"RUNNER_TEMP": folder}):
            import urllib.error
            denied = urllib.error.HTTPError("https://graph.microsoft.com/v1.0/me", 403, "x", {}, None)
            with mock.patch("lifeos.platform.outlook.refresh", return_value={"access_token": "t"}), \
                    mock.patch("lifeos.platform.rest.urllib.request.urlopen", side_effect=denied):
                with self.assertRaises(OutlookError) as error:
                    self.client().get("/me/messages")
            self.assertEqual(str(error.exception), "OUTLOOK_HTTP_403")
            self.assertFalse(Path(self.client()._down).exists())


if __name__ == "__main__":
    unittest.main()
