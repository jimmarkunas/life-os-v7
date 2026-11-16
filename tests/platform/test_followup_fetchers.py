import inspect
import math
import re
import socket
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest import mock

from lifeos.platform import followup_fetchers as F
from lifeos.platform import followup_readers as readers
from lifeos.platform import gcal, notion_client, todoist

NOW = datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)
ACCOUNT = "jim@example.com"
JIRA_BASE = "https://sample.atlassian.net"
TGP = "tgp" + "international.com"                    # built at run time: no TGP address is written into the repo
NAMES = F.CREDENTIAL_NAMES


class FakeTokens:
    """The private token store: label -> refresh token."""
    def __init__(self, tokens):
        self.tokens, self.saved = dict(tokens), []

    def load(self, label):
        return self.tokens.get(label)

    def save(self, label, token):
        self.saved.append((label, token))
        self.tokens[label] = token


def build(environ, factories=None, clock=None, tokens=None):
    return F.build_fetchers(environ, factories=factories, clock=clock, token_store=tokens or FakeTokens({"followup-personal-1": "r" * 20, "followup-personal-2": "s" * 20, "followup-personal-3": "u" * 20}))


def N(source, tenant="personal", slot=1):
    """The reference a grant names."""
    return F.GRANT_REFS[(source, tenant, slot)]


def E(source, tenant="personal", slot=1):
    """The environment name that holds the value."""
    return NAMES[(source, tenant, slot)]
SA = '{"client_email": "sa@example.com", "private_key": "placeholder"}'


def env(**extra):
    base = {E("jira"): "t" * 20, "JIRA_EMAIL": ACCOUNT, "JIRA_BASE_URL": JIRA_BASE, E("calendar"): SA, E("gmail"): "r" * 20,
            E("todoist"): "t" * 20, E("notion"): "n" * 20, E("interview_os"): "i" * 20, "FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID": "cal@example.com", "GMAIL_OAUTH_CLIENT_ID": "cid", "GMAIL_OAUTH_CLIENT_SECRET": "csecret",
            "OUTLOOK_CLIENT_ID": "oid"}
    base.update(extra)
    return {k: v for k, v in base.items() if v is not None}


class FakeJira:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, path, params=None):
        self.calls.append((path, params))
        return self.pages.pop(0)


class FakeCal:
    def __init__(self, events):
        self.events, self.calls = events, []

    def list_events(self, time_min, time_max):
        self.calls.append((time_min, time_max))
        return self.events


class FakeGmail:
    def __init__(self, replies):
        self.replies, self.queries = replies, []

    def list_ids(self, query, limit):
        self.queries.append((query, limit))
        return list(self.replies)

    def message_metadata(self, message_id):
        return self.replies[message_id]


class FakeOutlook:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def messages(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


class FakeTodoist:
    def __init__(self, tasks):
        self.rows = tasks

    def tasks(self, limit):
        return self.rows[:limit]


class FakeNotion:
    """Answers POST /search newest first, in pages of `size`."""
    def __init__(self, pages, size=100):
        self.data, self.size, self.calls = pages, size, []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        start = int((body or {}).get("start_cursor") or 0)
        chunk = self.data[start:start + self.size]
        more = start + self.size < len(self.data)
        return {"results": chunk, "has_more": more, "next_cursor": str(start + self.size) if more else None}


def page(page_id, edited, title):
    return {"id": page_id, "url": "https://www.notion.so/" + page_id.replace("-", ""), "last_edited_time": edited, "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}, "Notes": {"type": "rich_text"}}}


def run(source, client, environ=None, since=None, tenant="personal", **factory_kwargs):
    fetchers = build(environ if environ is not None else env(), factories={source: lambda *args: client}, clock=lambda: NOW)
    return fetchers[source](N(source, tenant), since)


def delta(source, client, since=None):
    """Run the real reader over a fetcher backed by a fake client: the rows must pass the normalizer."""
    fetchers = build(env(), factories={source: lambda *args: client}, clock=lambda: NOW)
    grant = readers.Grant(source, "personal", ACCOUNT, N(source), NOW + timedelta(days=30))
    receipts = []
    return readers.read_delta(source, "personal", ACCOUNT, since, (grant,), fetchers[source], receipts.append, NOW, "req-1", JIRA_BASE)


class Mapping(unittest.TestCase):
    def test_jira_rows_pass_the_normalizer(self):
        client = FakeJira([{"issues": [{"key": "LIFE-7", "fields": {"summary": "Sample ticket", "updated": "2026-11-16T14:00:40.594-0600"}}], "isLast": True}])
        out = delta("jira", client)
        self.assertEqual((out.status, [(i["native_id"], i["link"], i["title"], i["observed_at"]) for i in out.items]),
                         ("ok", [("LIFE-7", JIRA_BASE + "/browse/LIFE-7", "Sample ticket", "2026-11-16T20:00:40.594000+00:00")]))
        self.assertEqual(client.calls[0][0], "/rest/api/3/search/jql")

    def test_jira_follows_the_page_token(self):
        client = FakeJira([{"issues": [{"key": "LIFE-1", "fields": {"updated": "2026-11-16T10:00:00.000+0000"}}], "nextPageToken": "tok", "isLast": False},
                           {"issues": [{"key": "LIFE-2", "fields": {"updated": "2026-11-16T11:00:00.000+0000"}}], "isLast": True}])
        rows = run("jira", client)
        self.assertEqual([r["key"] for r in rows], ["LIFE-1", "LIFE-2"])
        self.assertEqual(client.calls[1][1]["nextPageToken"], "tok")

    def test_calendar_rows_and_no_attendees_or_description(self):
        client = FakeCal([{"id": "ev1", "htmlLink": "https://calendar.example.com/ev1", "updated": "2026-11-16T15:00:00.000Z", "summary": "Planning call",
                           "description": "private notes", "attendees": [{"email": "x@example.com"}]}])
        out = delta("calendar", client)
        self.assertEqual((out.status, out.items[0]["native_id"], out.items[0]["title"]), ("ok", "ev1", "Planning call"))
        self.assertNotIn("attendees", repr(run("calendar", client)))
        self.assertNotIn("private notes", repr(run("calendar", client)))

    def test_gmail_uses_metadata_only(self):
        client = FakeGmail({"m1": {"id": "m1", "internalDate": "1794860000000", "payload": {"headers": [{"name": "Subject", "value": "Sample subject"}, {"name": "From", "value": "a@example.com"}]}}})
        out = delta("gmail", client)
        self.assertEqual((out.status, out.items[0]["native_id"], out.items[0]["title"]), ("ok", "m1", "Sample subject"))
        self.assertTrue(out.items[0]["link"].startswith("https://mail.google.com/"))
        self.assertNotIn("a@example.com", repr(run("gmail", client)))

    def test_outlook_rows(self):
        client = FakeOutlook([{"id": "o1", "webLink": "https://outlook.example.com/o1", "receivedDateTime": "2026-11-16T16:00:00Z", "subject": "Sample"}])
        out = delta("outlook", client)
        self.assertEqual((out.status, out.items[0]["native_id"], out.items[0]["title"]), ("ok", "o1", "Sample"))
        self.assertEqual(client.calls[0]["fields"], F.OUTLOOK_FIELDS)

    def test_todoist_rows(self):
        client = FakeTodoist([{"id": "t1", "content": "Send the sample summary", "updated_at": "2026-11-16T17:00:00Z", "description": "private"}])
        out = delta("todoist", client)
        self.assertEqual((out.status, out.items[0]["native_id"], out.items[0]["title"], out.items[0]["link"]), ("ok", "t1", "Send the sample summary", "https://app.todoist.com/app/task/t1"))
        self.assertNotIn("private", repr(run("todoist", client)))

    def test_notion_and_interview_rows_cover_every_shared_page(self):
        client = FakeNotion([page("22222222-2222-2222-2222-222222222222", "2026-11-16T13:00:00.000Z", "Page two"), page("11111111-1111-1111-1111-111111111111", "2026-11-16T12:00:00.000Z", "Page one")], size=1)
        out = delta("notion", client)
        self.assertEqual((out.status, sorted(i["title"] for i in out.items)), ("ok", ["Page one", "Page two"]))
        self.assertEqual([(c[0], c[1]) for c in client.calls], [("POST", "/search")] * 2)
        self.assertEqual(client.calls[0][2]["sort"], {"direction": "descending", "timestamp": "last_edited_time"})
        out = delta("interview_os", FakeNotion([page("33333333-3333-3333-3333-333333333333", "2026-11-16T14:00:00.000Z", "Interview page")]))
        self.assertEqual((out.status, out.items[0]["title"]), ("ok", "Interview page"))

    def test_notion_search_stops_at_the_checkpoint(self):
        client = FakeNotion([page("22222222-2222-2222-2222-222222222222", "2026-11-16T20:30:00.000Z", "New"), page("11111111-1111-1111-1111-111111111111", "2026-11-16T10:00:00.000Z", "Old"),
                             page("44444444-4444-4444-4444-444444444444", "2026-11-16T09:00:00.000Z", "Older")], size=1)
        rows = run("notion", client, since=NOW - timedelta(hours=1))
        self.assertEqual([r["title"] for r in rows], ["New"])
        self.assertEqual(len(client.calls), 2)                                       # it did not page past the first older page

    def test_notion_search_is_row_capped_and_bounded(self):
        rows = [page(f"{i:08d}-0000-0000-0000-000000000000", "2026-11-16T20:30:00.000Z", "p") for i in range(F.MAX_ROWS + 5)]
        self.assertEqual(len(run("notion", FakeNotion(rows, size=100))), F.MAX_ROWS + 1)
        endless = FakeNotion([page("55555555-5555-5555-5555-555555555555", "2026-11-16T20:30:00.000Z", "p")] * (F.MAX_PAGES * 100 + 1), size=100)
        self.assertEqual(len(run("notion", endless)), F.MAX_ROWS + 1) if F.MAX_ROWS < F.MAX_PAGES * 100 else None


class Bounds(unittest.TestCase):
    def test_since_is_honored(self):
        since = NOW - timedelta(hours=2)
        start = since - F.MARGIN
        jira = FakeJira([{"issues": [], "isLast": True}])
        run("jira", jira, since=since)
        self.assertIn(f"updated >= -{math.ceil((NOW - start).total_seconds() / 60)}m", jira.calls[0][1]["jql"])
        gmail = FakeGmail({})
        run("gmail", gmail, since=since)
        self.assertEqual(gmail.queries[0], (f"after:{int(start.timestamp())}", F.MAX_ROWS + 1))
        outlook = FakeOutlook([])
        run("outlook", outlook, since=since)
        self.assertEqual(outlook.calls[0]["since"], start.strftime("%Y-%m-%dT%H:%M:%SZ"))
        notion = FakeNotion([page("66666666-6666-6666-6666-666666666666", (start - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "old")])
        self.assertEqual(run("notion", notion, since=since), [])

    def test_a_first_read_looks_back_a_week(self):
        jira = FakeJira([{"issues": [], "isLast": True}])
        run("jira", jira)
        self.assertIn(f"updated >= -{F.LOOKBACK_DAYS * 24 * 60}m", jira.calls[0][1]["jql"])

    def test_the_row_cap_makes_the_reader_see_a_truncated_answer(self):
        tasks = [{"id": f"t{n}", "content": "x", "updated_at": "2026-11-16T17:00:00Z"} for n in range(700)]
        self.assertEqual(len(run("todoist", FakeTodoist(tasks))), F.MAX_ROWS + 1)
        out = delta("todoist", FakeTodoist(tasks))
        self.assertEqual((out.status, out.reason), ("degraded", "truncated"))

    def test_jira_paging_is_bounded(self):
        pages = [{"issues": [{"key": "LIFE-1", "fields": {"updated": "2026-11-16T10:00:00.000+0000"}}], "nextPageToken": "t", "isLast": False}] * (F.MAX_PAGES + 2)
        with self.assertRaises(F.FetchError) as caught:
            run("jira", FakeJira(pages))
        self.assertEqual(str(caught.exception), "FOLLOWUP_LISTING_INCOMPLETE")

    def test_a_broken_listing_is_incomplete_not_empty(self):
        for source, client in (("jira", FakeJira([{"oops": 1}])),):
            with self.assertRaises(F.FetchError) as caught:
                run(source, client)
            self.assertEqual(str(caught.exception), "FOLLOWUP_LISTING_INCOMPLETE")


class Codes(unittest.TestCase):
    def code(self, call):
        with self.assertRaises(F.FetchError) as caught:
            call()
        return str(caught.exception)


class Credentials(Codes):

    def test_a_missing_credential_is_a_fixed_code_and_degrades_only_that_source(self):
        fetchers = build(env(**{E("todoist"): None}), factories={"todoist": lambda *a: FakeTodoist([])})
        self.assertEqual(self.code(lambda: fetchers["todoist"](N("todoist", "personal"), None)), "FOLLOWUP_CREDENTIAL_MISSING")
        out = delta_with(fetchers, "todoist")
        self.assertEqual((out.status, out.reason), ("degraded", "fetch_failed"))

    def test_a_name_outside_the_table_or_for_another_source_is_refused(self):
        fetchers = build(env(JIRA_API_TOKEN="x" * 20, DB_PASSWORD="y" * 20))
        for name in ("JIRA_API_TOKEN", "DB_PASSWORD", N("todoist", "personal"), ""):
            self.assertEqual(self.code(lambda n=name: fetchers["jira"](n, None)), "FOLLOWUP_CREDENTIAL_NOT_ALLOWED")

    def test_a_missing_setting_is_a_fixed_code(self):
        for source, drop in (("jira", "JIRA_BASE_URL"), ("calendar", "FOLLOWUP_GCAL_PERSONAL_CALENDAR_ID"), ("gmail", "GMAIL_OAUTH_CLIENT_ID"), ("outlook", "OUTLOOK_CLIENT_ID")):
            fetchers = build(env(**{drop: None}), factories={source: lambda *a: None})
            self.assertEqual(self.code(lambda f=fetchers[source], s=source: f(N(s), None)), "FOLLOWUP_CONFIG_MISSING", source)

    def test_bytalos_calendar_uses_its_own_calendar_id_and_gmail_its_own_token(self):
        fetchers = build(env(), factories={"calendar": lambda *a: FakeCal([])})
        self.assertEqual(self.code(lambda: fetchers["calendar"](N("calendar", "bytalos"), None)), "FOLLOWUP_CONFIG_MISSING")      # the shared service account is set; the Bytalos calendar id is a separate setting
        fetchers = build(env(FOLLOWUP_GCAL_BYTALOS_CALENDAR_ID="b@example.com"), factories={"calendar": lambda *a: FakeCal([])})
        self.assertEqual(fetchers["calendar"](N("calendar", "bytalos"), None), [])
        gmail_fetchers = build(env(), factories={"gmail": lambda *a: FakeGmail({})})
        self.assertEqual(self.code(lambda: gmail_fetchers["gmail"](N("gmail", "bytalos"), None)), "FOLLOWUP_CREDENTIAL_MISSING")     # never falls back to the personal refresh token
        self.assertEqual(self.code(lambda: gmail_fetchers["gmail"](N("calendar", "personal"), None)), "FOLLOWUP_CREDENTIAL_NOT_ALLOWED")

    def test_the_name_table(self):
        expected = {(s, t, 1) for s in F.SOURCES for t in F.TENANTS} - {("outlook", "bytalos", 1), ("todoist", "bytalos", 1)} | {("outlook", "personal", 2), ("outlook", "personal", 3)}
        self.assertEqual(set(NAMES), expected)
        for name in NAMES.values():
            self.assertNotIn("tgp", name.lower())
        reused = {"JIRA_API_TOKEN", "NOTION_API_TOKEN", "GCAL_SERVICE_ACCOUNT_JSON", "GMAIL_OAUTH_REFRESH_TOKEN"}
        self.assertEqual(set(F.REUSED_NAMES), reused)
        self.assertEqual(F.NEW_NAMES, ["FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH", "FOLLOWUP_TODOIST_PERSONAL_TOKEN"])      # the only new secrets
        self.assertEqual({NAMES[("jira", t, 1)] for t in F.TENANTS}, {"JIRA_API_TOKEN"})
        self.assertEqual({NAMES[(s, t, 1)] for s in ("notion", "interview_os") for t in F.TENANTS}, {"NOTION_API_TOKEN"})
        self.assertEqual({NAMES[("calendar", t, 1)] for t in F.TENANTS}, {"GCAL_SERVICE_ACCOUNT_JSON"})
        self.assertEqual(NAMES[("gmail", "personal", 1)], "GMAIL_OAUTH_REFRESH_TOKEN")
        self.assertEqual([NAMES[("outlook", "personal", n)] for n in (1, 2, 3)], ["followup-personal-1", "followup-personal-2", "followup-personal-3"])   # labels in v7_outlook_token
        self.assertEqual(len(set(F.GRANT_REFS.values())), len(F.GRANT_REFS))         # every grant names its own reference even when a secret is shared
        self.assertFalse([n for n in NAMES.values() if n.startswith("FOLLOWUP_OUTLOOK") or "SOURCE_IDS" in n or "_SA_JSON" in n or "READONLY_TOKEN" in n])
        self.assertEqual(F.ACCOUNT_NAMES[("outlook", "personal", 2)], "FOLLOWUP_ACCOUNT_OUTLOOK_PERSONAL_2")
        self.assertEqual(sorted(set(F.ACCOUNT_NAMES.values())), sorted(["FOLLOWUP_ACCOUNT_" + s for s in (
            "JIRA_PERSONAL", "JIRA_BYTALOS", "CALENDAR_PERSONAL", "CALENDAR_BYTALOS", "GMAIL_PERSONAL", "GMAIL_BYTALOS", "OUTLOOK_PERSONAL_1", "OUTLOOK_PERSONAL_2", "OUTLOOK_PERSONAL_3",
            "TODOIST_PERSONAL", "NOTION_PERSONAL", "NOTION_BYTALOS", "INTERVIEW_PERSONAL", "INTERVIEW_BYTALOS")]))
        self.assertEqual(F.missing({}), sorted(set(F.REUSED_NAMES) | set(F.NEW_NAMES)))
        self.assertEqual(F.missing(env()), ["FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH"])
        self.assertEqual(F.TENANTS, ("personal", "bytalos"))                         # no TGP tenant anywhere

    def test_there_is_no_bytalos_outlook_or_todoist_credential(self):
        fetchers = build(env(FOLLOWUP_TODOIST_BYTALOS_TOKEN="t" * 20), tokens=FakeTokens({"followup-bytalos-1": "r" * 20}))
        self.assertEqual(self.code(lambda: fetchers["outlook"]("followup-bytalos-1", None)), "FOLLOWUP_CREDENTIAL_NOT_ALLOWED")
        self.assertEqual(self.code(lambda: fetchers["todoist"]("FOLLOWUP_TODOIST_BYTALOS_TOKEN", None)), "FOLLOWUP_CREDENTIAL_NOT_ALLOWED")

    def test_a_secret_shaped_value_in_a_reply_is_refused(self):
        client = FakeTodoist([{"id": "t1", "content": "to" + "ken=" + "abcdefgh12345678", "updated_at": "2026-11-16T17:00:00Z"}])
        self.assertEqual(self.code(lambda: run("todoist", client)), "FOLLOWUP_SECRET_IN_REPLY")

class Workspaces(Codes):
    def test_jira_reads_only_the_workspaces_own_projects(self):
        for tenant, keys in (("personal", "JFM, LIFE, LOS"), ("bytalos", "BAD, BPP")):
            client = FakeJira([{"issues": [], "isLast": True}])
            run("jira", client, tenant=tenant, environ=env())
            self.assertTrue(client.calls[0][1]["jql"].startswith(f"project in ({keys}) AND "), client.calls[0][1]["jql"])

    def test_a_jira_key_outside_the_workspace_is_never_returned(self):
        client = FakeJira([{"issues": [{"key": "LIFE-1", "fields": {"updated": "2026-11-16T10:00:00.000+0000"}}, {"key": "BAD-9", "fields": {"updated": "2026-11-16T10:00:00.000+0000"}},
                                       {"key": "OTHER-3", "fields": {"updated": "2026-11-16T10:00:00.000+0000"}}], "isLast": True}])
        self.assertEqual([r["key"] for r in run("jira", client)], ["LIFE-1"])

    def test_the_two_jira_workspaces_share_the_existing_secrets(self):
        self.assertEqual((E("jira", "personal"), E("jira", "bytalos")), ("JIRA_API_TOKEN", "JIRA_API_TOKEN"))
        self.assertEqual((N("jira", "personal"), N("jira", "bytalos")), ("JIRA_API_TOKEN:jira.personal", "JIRA_API_TOKEN:jira.bytalos"))
        self.assertFalse([n for n in list(F.CREDENTIAL_NAMES.values()) + list(F.ACCOUNT_NAMES.values()) if n.startswith("FOLLOWUP_JIRA")])
        self.assertEqual(F.JIRA_ENV, ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))

    def test_jira_basic_auth_is_built_from_the_two_existing_secrets(self):
        seen = []
        build(env(), factories={"jira": lambda *a: seen.append(a) or FakeJira([{"issues": [], "isLast": True}])}, clock=lambda: NOW)["jira"](N("jira", "bytalos"), None)
        self.assertEqual(seen, [(JIRA_BASE, ACCOUNT, "t" * 20)])

    def test_the_jira_fetcher_only_ever_calls_get_on_its_client(self):
        import inspect
        source = inspect.getsource(F._read_jira)
        self.assertEqual(sorted(set(re.findall(r"client\.(\w+)", source))), ["get"])

    def test_three_personal_outlook_accounts_each_use_their_own_credential(self):
        seen = []
        fetchers = build(env(), factories={"outlook": lambda client_id, refresh, save=None: seen.append(refresh) or FakeOutlook([])})
        for slot in (1, 2, 3):
            fetchers["outlook"](N("outlook", "personal", slot), None)
        self.assertEqual(seen, ["r" * 20, "s" * 20, "u" * 20])

    def test_a_rotated_outlook_token_is_written_back_under_its_label_and_nothing_else_is(self):
        tokens = FakeTokens({"followup-personal-2": "old" + "x" * 20})
        saves = []
        fetchers = build(env(), factories={"outlook": lambda client_id, refresh, save=None: saves.append((refresh, save)) or FakeOutlook([])}, tokens=tokens)
        fetchers["outlook"](N("outlook", "personal", 2), None)
        refresh, save = saves[0]
        self.assertEqual(refresh, "old" + "x" * 20)
        save("new" + "y" * 20)
        self.assertEqual(tokens.saved, [("followup-personal-2", "new" + "y" * 20)])
        self.assertEqual(F.OUTLOOK_SCOPES, "offline_access Mail.Read Calendars.Read")
        self.assertEqual(self.code(lambda: build(env(), tokens=FakeTokens({}))["outlook"](N("outlook", "personal", 1), None)), "FOLLOWUP_CREDENTIAL_MISSING")

    def test_gmail_takes_the_bare_refresh_token_or_the_helpers_json(self):
        seen = []
        for value in ("r" * 20, '{"refresh_token": "' + "r" * 20 + '", "tenant": "personal"}'):
            fetchers = build(env(**{E("gmail"): value}), factories={"gmail": lambda cid, cs, refresh: seen.append(refresh) or FakeGmail({})})
            fetchers["gmail"](N("gmail"), None)
        self.assertEqual(seen, ["r" * 20, "r" * 20])
        self.assertEqual(self.code(lambda: build(env(**{E("gmail"): "{not json"}), factories={"gmail": lambda *a: FakeGmail({})})["gmail"](N("gmail"), None)), "FOLLOWUP_CREDENTIAL_MISSING")


class TgpExcluded(unittest.TestCase):
    def gmail_reply(self, message_id, **headers):
        rows = [{"name": "Subject", "value": "Sample"}] + [{"name": name.replace("_", "-"), "value": value} for name, value in headers.items()]
        return {"id": message_id, "internalDate": "1794860000000", "payload": {"headers": rows}}

    def test_gmail_drops_tgp_mail_however_it_arrived(self):
        client = FakeGmail({"ok": self.gmail_reply("ok", From="Pat <pat@example.com>", To="jim@example.com"),
                            "from": self.gmail_reply("from", From="Sam <sam@" + TGP + ">"),
                            "to": self.gmail_reply("to", From="pat@example.com", To="Sam <sam@mail." + TGP + ">"),
                            "fwd": self.gmail_reply("fwd", From="pat@example.com", X_Forwarded_For="sam@" + TGP + " jim@example.com"),
                            "orig": self.gmail_reply("orig", From="pat@example.com", X_Original_To="sam@" + TGP.upper()),
                            "near": self.gmail_reply("near", From="pat@not" + TGP + ".example.org")})
        self.assertEqual([r["id"] for r in run("gmail", client)], ["ok", "near"])

    def test_outlook_drops_tgp_mail_however_it_arrived(self):
        def msg(message_id, **extra):
            return {"id": message_id, "webLink": "https://outlook.example.com/" + message_id, "receivedDateTime": "2026-11-16T16:00:00Z", "subject": "Sample", **extra}
        who = lambda address: {"emailAddress": {"address": address}}      # noqa: E731
        client = FakeOutlook([msg("ok", **{"from": who("pat@example.com")}), msg("from", **{"from": who("sam@" + TGP)}),
                              msg("to", toRecipients=[who("jim@example.com"), who("sam@" + TGP)]), msg("cc", ccRecipients=[who("sam@" + TGP)]),
                              msg("fwd", **{"from": who("pat@example.com")}, internetMessageHeaders=[{"name": "X-Forwarded-For", "value": "sam@" + TGP}])])
        self.assertEqual([r["id"] for r in run("outlook", client)], ["ok"])

    def test_tgp_rows_never_reach_the_reader_or_a_count(self):
        client = FakeOutlook([{"id": "x", "webLink": "https://outlook.example.com/x", "receivedDateTime": "2026-11-16T16:00:00Z", "subject": "Sample", "from": {"emailAddress": {"address": "a@" + TGP}}}])
        out = delta("outlook", client)
        self.assertEqual((out.status, out.items), ("ok", ()))


def delta_with(fetchers, source):
    grant = readers.Grant(source, "personal", ACCOUNT, N(source), NOW + timedelta(days=30))
    return readers.read_delta(source, "personal", ACCOUNT, None, (grant,), fetchers[source], lambda receipt: None, NOW, "req-2", JIRA_BASE)


class ReadOnly(unittest.TestCase):
    def build(self):
        return {"jira": F.ReadOnlyJira("https://api.atlassian.com/ex/jira/c1", ACCOUNT, "t" * 20), "calendar": F.ReadOnlyCalendar(SA, "cal@example.com"), "gmail": F.ReadOnlyGmail("c", "s", "r" * 20),
                "outlook": F.ReadOnlyOutlook("oid", "r" * 20), "notion": F.ReadOnlyNotion({"T": "n" * 20, "S": "search"}, token_name="T", source_name="S"), "todoist": todoist.Todoist("t" * 20)}

    def test_construction_never_touches_the_network(self):
        def no_network(*_a, **_k):
            raise AssertionError("network used")
        with mock.patch.object(urllib.request, "urlopen", no_network), mock.patch.object(socket.socket, "connect", no_network):
            self.build()

    def test_every_write_path_is_refused_before_any_request(self):
        def no_network(*_a, **_k):
            raise AssertionError("network used")
        c = self.build()
        calls = [lambda: c["jira"].post("/x", {}), lambda: c["jira"].request("DELETE", "/x"), lambda: c["jira"].set_sprint_state(1, "active"), lambda: c["jira"].move_issues(1, ["LIFE-1"]), lambda: c["jira"].create_sprint(1, "s", NOW, NOW),
                 lambda: c["jira"].request("PUT", "/x", payload={}), lambda: c["jira"].request("POST", "/x", payload={}),
                 lambda: c["calendar"].insert({}), lambda: c["calendar"].update("1", {}), lambda: c["calendar"].delete("1"),
                 lambda: c["gmail"].trash("1"), lambda: c["gmail"].relabel(["1"], add=["x"]), lambda: c["gmail"].apply_amazon("1", "l"),
                 lambda: c["outlook"].move("m", "f"), lambda: c["notion"].create({}, []), lambda: c["notion"].update_page_properties("a" * 32, {"x": 1}), lambda: c["notion"].call("PATCH", "/pages/x", {})]
        with mock.patch.object(urllib.request, "urlopen", no_network), mock.patch.object(socket.socket, "connect", no_network):
            for call in calls:
                with self.assertRaises(F.FetchError) as caught:
                    call()
                self.assertEqual(str(caught.exception), "FOLLOWUP_READ_ONLY")

    def test_notion_allows_exactly_search_and_database_query_posts(self):
        client = F.ReadOnlyNotion({"T": "n" * 20, "S": "search"}, token_name="T", source_name="S")
        for method, path in (("POST", "/pages"), ("POST", "/search/x"), ("POST", "/data_sources/abc/update"), ("PATCH", "/search"), ("DELETE", "/blocks/x"), ("PUT", "/search")):
            with self.assertRaises(F.FetchError, msg=(method, path)):
                client.call(method, path, {})
        with mock.patch.object(notion_client.Client, "call", return_value="read"):
            self.assertEqual([client.call("POST", "/search", {}), client.call("POST", "/data_sources/abc/query", {}), client.call("GET", "/users/me")], ["read"] * 3)

    def test_the_calendar_scope_is_read_only(self):
        self.assertEqual(self.build()["calendar"]._scope, gcal.SCOPE_READONLY)
        self.assertTrue(gcal.SCOPE_READONLY.endswith("calendar.readonly"))

    def test_the_todoist_client_has_no_write_call(self):
        self.assertEqual(sorted(n for n in dir(todoist.Todoist) if not n.startswith("_")), ["get", "tasks"])
        self.assertEqual(re.findall(r'method="([A-Z]+)"', inspect.getsource(todoist)), ["GET"])
        for word in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertNotIn(word, inspect.getsource(todoist))

    def test_the_fetcher_module_never_imports_a_provider_write_helper(self):
        source = inspect.getsource(F)
        for banned in ("create_sprint", "move_issues", "apply_amazon", ".trash(", ".relabel(", ".insert(", ".delete(", "update_page_properties", ".move("):
            self.assertNotIn(banned, source.replace("def ", ""), banned)


if __name__ == "__main__":
    unittest.main()
