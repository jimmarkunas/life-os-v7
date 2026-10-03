import json
import re
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lifeos.calendar_bridge import sync
from lifeos.calendar_bridge.sync import BridgeError
from lifeos.platform import gcal
from lifeos.platform.gcal import GcalError, GoogleCalendar

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
ENV = {"OUTLOOK_CLIENT_ID": "cid"}


def ev(uid, start="2026-10-08T15:00:00.0000000", end="2026-10-08T16:00:00.0000000", subject="Private subject", **over):
    event = {"id": "o-" + uid, "iCalUId": uid, "subject": subject, "start": {"dateTime": start}, "end": {"dateTime": end},
             "isAllDay": False, "isCancelled": False, "showAs": "busy", "responseStatus": {"response": "accepted"},
             "location": {"displayName": "Private place"}, "webLink": "https://example.invalid/e"}
    event.update(over)
    return event


class FakeOutlook:
    def __init__(self, events):
        self._events = events

    def __call__(self, client_id, token, save):
        return self

    def events(self, start, end):
        return self._events


class FakeGcal:
    def __init__(self, existing=()):
        self.items = {e["id"]: e for e in existing}
        self.log = []
        self.conflict = set()
        self.foreign = []                                     # events V7 did not write

    def list_events(self, a, b, prop=None):
        assert prop is None                                   # everything in the window: V7's own copies and the person's events
        return list(self.items.values()) + self.foreign

    def insert(self, body):
        self.log.append("insert")
        if body["id"] in self.conflict:
            raise GcalError("GCAL_HTTP_409")
        self.items[body["id"]] = body

    def update(self, gid, body):
        self.log.append("update")
        self.items[gid] = {"id": gid, **body}

    def delete(self, gid):
        self.log.append("delete")
        self.items.pop(gid, None)


class Db:
    def __enter__(self): return self
    def __exit__(self, *e): return False
    def cursor(self): return self


def connect():
    return Db()


def go(events, g, live=True, token="R"):
    with patch.object(sync.outlook_tokens, "ensure_schema"), patch.object(sync.outlook_tokens, "load", return_value=token), \
            patch.object(sync.outlook_tokens, "save"):
        return sync.run(1, live, environ=ENV, connect=connect, outlook_factory=FakeOutlook(events), gcal=g, now=NOW)


class MirrorTests(unittest.TestCase):
    def test_cancelled_declined_and_identityless_events_are_not_mirrored(self):
        self.assertTrue(sync.mirrors(ev("a")))
        self.assertFalse(sync.mirrors(ev("a", isCancelled=True)))
        self.assertFalse(sync.mirrors(ev("a", responseStatus={"response": "declined"})))
        self.assertFalse(sync.mirrors(ev("")))

    def test_ids_are_deterministic_valid_google_ids_and_per_occurrence(self):
        one = sync.google_id("personal", "uid", "2026-10-08T15:00:00")
        self.assertEqual(one, sync.google_id("personal", "uid", "2026-10-08T15:00:00"))
        self.assertNotEqual(one, sync.google_id("personal", "uid", "2026-10-15T15:00:00"))      # a series: one id per occurrence
        self.assertNotEqual(one, sync.google_id("work", "uid", "2026-10-08T15:00:00"))
        self.assertRegex(one, r"^[a-v0-9]{5,1024}$")

    def test_body_shapes_for_timed_all_day_free_and_tentative(self):
        _, timed = sync.to_google("personal", ev("t"))
        self.assertEqual(timed["start"], {"dateTime": "2026-10-08T15:00:00", "timeZone": "UTC"})
        _, day = sync.to_google("personal", ev("d", "2026-10-09T00:00:00.0000000", "2026-10-10T00:00:00.0000000", isAllDay=True))
        self.assertEqual((day["start"], day["end"]), ({"date": "2026-10-09"}, {"date": "2026-10-10"}))
        _, free = sync.to_google("personal", ev("f", showAs="free"))
        self.assertEqual(free["transparency"], "transparent")
        _, tentative = sync.to_google("personal", ev("x", showAs="tentative"))
        self.assertEqual(tentative["status"], "tentative")
        self.assertEqual(timed["extendedProperties"]["private"]["v7_src"], "outlook")


class SyncTests(unittest.TestCase):
    def test_dry_run_counts_and_writes_nothing_and_leaks_nothing(self):
        g = FakeGcal()
        out = go([ev("a"), ev("b"), ev("c", isCancelled=True)], g, live=False)
        self.assertEqual((out["outlook"], out["skipped"], out["create"], out["applied"], g.log), (3, 1, 2, 0, []))
        text = json.dumps(out)
        for private in ("Private subject", "Private place", "example.invalid"):
            self.assertNotIn(private, text)

    def test_live_creates_then_a_second_run_changes_nothing(self):
        g = FakeGcal()
        first = go([ev("a"), ev("b")], g)
        self.assertEqual((first["create"], first["applied"]), (2, 2))
        again = go([ev("a"), ev("b")], g)
        self.assertEqual((again["create"], again["update"], again["delete"], again["same"], again["applied"]), (0, 0, 0, 2, 0))

    def test_a_changed_event_is_updated_and_a_vanished_one_is_deleted(self):
        g = FakeGcal()
        go([ev("a"), ev("b")], g)
        out = go([ev("a", subject="New title")], g)
        self.assertEqual((out["update"], out["delete"], out["same"]), (1, 1, 0))
        self.assertEqual(len(g.items), 1)

    def test_a_declined_or_cancelled_event_removes_its_mirror(self):
        g = FakeGcal()
        go([ev("a")], g)
        out = go([ev("a", responseStatus={"response": "declined"})], g)
        self.assertEqual((out["delete"], len(g.items)), (1, 0))

    def test_an_insert_that_hits_an_existing_id_becomes_an_update(self):
        g = FakeGcal()
        gid, _ = sync.to_google("personal", ev("a"))
        g.conflict = {gid}
        out = go([ev("a")], g)
        self.assertEqual((out["applied"], g.log), (1, ["insert", "update"]))

    def test_a_half_empty_outlook_answer_cannot_wipe_the_mirror(self):
        g = FakeGcal()
        go([ev(str(i), start=f"2026-10-{8 + i:02d}T15:00:00.0000000", end=f"2026-10-{8 + i:02d}T16:00:00.0000000") for i in range(8)], g)
        with self.assertRaises(BridgeError) as error:
            go([], g)
        self.assertEqual(str(error.exception), "CALENDAR_MASS_DELETE_GUARD")
        self.assertEqual(len(g.items), 8)

    def test_events_v7_did_not_write_are_never_edited_or_deleted(self):
        g = FakeGcal()
        g.foreign = [{"id": "mine", "summary": "Dentist", "start": {"dateTime": "2026-10-08T09:00:00-05:00"}}]
        out = go([ev("a")], g)
        self.assertEqual((out["create"], out["delete"], out["update"]), (1, 0, 0))
        self.assertNotIn("delete", g.log)

    def test_an_invite_that_already_reached_google_another_way_is_not_copied_again(self):
        g = FakeGcal()
        g.foreign = [{"id": "from-gmail", "iCalUID": "dup", "start": {"dateTime": "2026-10-08T10:00:00-05:00"}},
                     {"id": "all-day", "iCalUID": "day", "start": {"date": "2026-10-09"}}]
        out = go([ev("dup"), ev("new"), ev("day", "2026-10-09T00:00:00.0000000", "2026-10-10T00:00:00.0000000", isAllDay=True),
                  ev("dup", start="2026-10-15T15:00:00.0000000", end="2026-10-15T16:00:00.0000000")], g, live=False)
        # 10:00-05:00 is 15:00Z: the first occurrence and the all-day event are already there; a later occurrence and "new" are not
        self.assertEqual((out["already_in_google"], out["create"]), (2, 2))

    def test_missing_sign_in_or_config_fails_closed(self):
        with self.assertRaises(BridgeError):
            go([ev("a")], FakeGcal(), token=None)
        with self.assertRaises(BridgeError):
            sync.run(1, False, environ={}, connect=connect, outlook_factory=FakeOutlook([]), gcal=FakeGcal(), now=NOW)


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *e): return False


class GcalClientTests(unittest.TestCase):
    KEY = json.dumps({"client_email": "service-account-name", "private_key": "KEY"})

    def client(self, **kw):
        return GoogleCalendar(self.KEY, "calendar-id", sleep=lambda s: None, sign=lambda key, msg: b"SIG", clock=lambda: 1000, **kw)

    def test_config_errors_are_fixed_codes(self):
        for bad in (("not json", "c"), (json.dumps({"client_email": "x"}), "c"), (self.KEY, "")):
            with self.assertRaises(GcalError):
                GoogleCalendar(*bad)
        with self.assertRaises(GcalError) as error:
            GoogleCalendar.from_env({})
        self.assertEqual(str(error.exception), "GCAL_CONFIG_MISSING:GCAL_SERVICE_ACCOUNT_JSON,GCAL_CALENDAR_ID")

    def test_token_exchange_uses_a_signed_jwt_and_every_call_is_bearer_authorised(self):
        seen = []

        def fake(request, timeout=0):
            seen.append((request.full_url, request.data, dict(request.headers)))
            if "oauth2" in request.full_url:
                return Response({"access_token": "TOKEN"})
            return Response({"items": [{"id": "e1"}]})

        with patch("urllib.request.urlopen", side_effect=fake):
            items = self.client().list_events("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", "v7_src=outlook")
        self.assertEqual(items, [{"id": "e1"}])
        self.assertIn(b"jwt-bearer", seen[0][1])
        self.assertEqual(seen[1][2]["Authorization"], "Bearer TOKEN")
        self.assertIn("privateExtendedProperty=v7_src%3Doutlook", seen[1][0])
        self.assertIn("calendar-id", seen[1][0])

    def test_errors_never_leak_bodies_or_urls_transient_reads_retry_and_delete_of_a_missing_event_is_fine(self):
        calls = []

        def flaky(request, timeout=0):
            if "oauth2" in request.full_url:
                return Response({"access_token": "T"})
            calls.append(request.get_method())
            if request.get_method() == "GET" and len(calls) < 3:
                raise urllib.error.HTTPError(request.full_url, 503, "secret body", {}, None)
            if request.get_method() == "DELETE":
                raise urllib.error.HTTPError(request.full_url, 404, "secret body", {}, None)
            return Response({"items": []})

        with patch("urllib.request.urlopen", side_effect=flaky):
            c = self.client()
            self.assertEqual(c.list_events("a", "b"), [])
            c.delete("gone")                                                    # 404: already gone
        self.assertEqual(calls.count("GET"), 3)

        def denied(request, timeout=0):
            if "oauth2" in request.full_url:
                return Response({"access_token": "T"})
            raise urllib.error.HTTPError(request.full_url, 403, "secret body", {}, None)

        with patch("urllib.request.urlopen", side_effect=denied):
            with self.assertRaises(GcalError) as error:
                self.client().insert({"id": "x"})
        self.assertEqual(str(error.exception), "GCAL_HTTP_403")

    def test_auth_failure_is_a_fixed_code(self):
        def bad(request, timeout=0):
            raise urllib.error.HTTPError(request.full_url, 400, "secret", {}, None)
        with patch("urllib.request.urlopen", side_effect=bad):
            with self.assertRaises(GcalError) as error:
                self.client().list_events("a", "b")
        self.assertEqual(str(error.exception), "GCAL_AUTH_FAILED")

    def test_openssl_signing_uses_a_private_temp_file_that_is_removed(self):
        import subprocess
        calls = {}

        def run(cmd, input=None, **kw):
            calls["cmd"] = cmd
            calls["mode"] = oct(__import__("os").stat(cmd[-1]).st_mode & 0o777)
            calls["exists"] = Path(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, stdout=b"SIG")

        with patch("subprocess.run", side_effect=run):
            self.assertEqual(gcal.openssl_sign("PEM", b"msg"), b"SIG")
        self.assertEqual(calls["mode"], "0o600")
        self.assertFalse(calls["exists"].exists())


class WorkflowTests(unittest.TestCase):
    text = (ROOT / ".github/workflows/calendar.yml").read_text()

    def test_manual_only_with_only_the_calendar_outlook_and_database_secrets(self):
        self.assertNotRegex(self.text, r"\n  (schedule|push|pull_request):")
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", self.text))),
                         sorted(["OUTLOOK_CLIENT_ID", "GCAL_SERVICE_ACCOUNT_JSON", "GCAL_CALENDAR_ID"] + [f"LIFEOS_ACQ_{n}" for n in (
                             "SSH_PRIVATE_KEY", "DB_PASSWORD", "SSH_HOST", "SSH_PORT", "SSH_USER", "SSH_KNOWN_HOSTS", "DB_NAME", "DB_USER")]))

    def test_no_address_in_tracked_bridge_code(self):
        for path in list((ROOT / "lifeos" / "calendar_bridge").glob("*.py")) + [ROOT / "lifeos/platform/gcal.py"]:
            self.assertNotRegex(path.read_text(), r"[\w.+-]+@[\w-]+\.[\w.]+")


class HourlyJobTests(unittest.TestCase):
    text = (ROOT / ".github/workflows/hourly.yml").read_text()

    def job(self):
        return self.text[self.text.index("\n  outlook:\n"):self.text.index("\n  report:\n")]

    def test_runs_on_scheduled_and_tick_runs_only_and_never_fails_the_jobs_run(self):
        job = self.job()
        self.assertIn("github.event_name != 'workflow_dispatch' || inputs.tick", job.split("runs-on")[0])
        self.assertEqual(job.count("continue-on-error: true"), 2)
        self.assertNotIn("\n    continue-on-error:", job)
        self.assertIn("::warning", job)

    def test_other_credentials_are_blank_and_only_the_one_step_gets_the_calendar_outlook_and_database_secrets(self):
        job = self.job()
        head, steps = job.split("    steps:")
        for name in ("NOTION_API_TOKEN", "GMAIL_OAUTH_REFRESH_TOKEN", "TINYFISH_API_KEY", "FIT_PROFILE_JSON", "HIRING_PIPELINE_PAGE_ID"):
            self.assertIn(f'{name}: ""', head, name)
        self.assertNotIn("secrets.", head)
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", steps))),
                         sorted(["OUTLOOK_CLIENT_ID", "GCAL_SERVICE_ACCOUNT_JSON", "GCAL_CALENDAR_ID"] + [f"LIFEOS_ACQ_{n}" for n in (
                             "SSH_PRIVATE_KEY", "DB_PASSWORD", "SSH_HOST", "SSH_PORT", "SSH_USER", "SSH_KNOWN_HOSTS", "DB_NAME", "DB_USER")]))


if __name__ == "__main__":
    unittest.main()
