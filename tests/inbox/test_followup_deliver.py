"""LIFE-716: Followup open loops delivered to the Agent Inbox as items. A fake Notion: no network, no token."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from lifeos.inbox import agent_inbox, followup_deliver as D, followup_stage as S
from lifeos.inbox.followup_store import thread_id
from tests.inbox.test_followup_stage import ACCOUNT, Rig, T0, mail, promise

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)          # Nov 17, 9:00 AM CT
SOURCE = "a1" * 16
TID = "ab" * 32


def thread(tid=TID, title="Send the signed quote", state="upcoming", due="2026-11-20T15:00:00Z", url="https://mail.google.com/mail/u/0/#all/m1", notes="checkpoint source-backed; evidence 1"):
    return {"id": tid, "title": title, "state": state, "next_checkpoint": due, "url": url, "notes": notes, "owner": "", "status": "open"}


def payload(threads=(), workspace="personal", closed=(), review=()):
    return {"component": "followup", "state": "PASS", "workspace": workspace, "account": "a", "threads": list(threads), "review": list(review), "closed": list(closed)}


def _store(prop):
    for kind in ("title", "rich_text", "select", "date", "url"):
        if kind in prop:
            value = prop[kind]
            if kind in ("title", "rich_text"):
                value = [{**piece, "plain_text": piece["text"]["content"]} for piece in value]               # Notion returns plain_text on every piece
            if kind == "date" and value:
                value = {"start": value["start"].replace("Z", ".000+00:00") if "T" in value["start"] else value["start"]}       # and spells a date-time its own way
            return {"type": kind, kind: value}
    return {"type": "unknown"}


def _plain(prop):
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join((p.get("text", {}).get("content") or p.get("plain_text", "")) for p in value or ())
    if kind == "select":
        return (value or {}).get("name", "")
    if kind == "date":
        return (value or {}).get("start") or ""
    return value or ""


class FakeNotion:
    def __init__(self):
        self.pages, self.writes = [], []

    def call(self, method, path, body=None):
        if path.startswith("/blocks/"):                                                  # LIFE-988: the page body of a row (none kept here)
            return {"results": [], "has_more": False}
        if path == "/search":
            return {"results": [{"object": "data_source", "id": SOURCE, "title": [{"plain_text": agent_inbox.DB_TITLE}]}], "has_more": False}
        raise AssertionError(f"unexpected call {method} {path}")

    def call_once(self, method, path, body=None):
        self.writes.append((method, path, body))
        if method == "POST" and path == "/pages":
            assert body["parent"]["data_source_id"] == SOURCE, "a write left the inbox data source"
            self.pages.append({"id": f"p{len(self.pages)}", "properties": {k: _store(v) for k, v in body["properties"].items()}})
        elif method == "PATCH" and path.startswith("/blocks/"):
            pass
        elif method == "PATCH":
            page = next(p for p in self.pages if f"/pages/{p['id']}" == path)
            page["properties"].update({k: _store(v) for k, v in body["properties"].items()})
        return {}

    def query_data_source(self, source, body):
        assert source == SOURCE
        flt = body["filter"]
        parts = flt.get("and", [flt])
        hits = []
        for page in self.pages:
            ok = True
            for part in parts:
                if "timestamp" in part or "or" in part:
                    continue
                if part.get("property") == "Due":
                    ok = ok and not _plain(page["properties"].get("Due", {}))
                    continue
                name = part["property"]
                want = part["select"]["equals"] if "select" in part else part["rich_text"]["equals"]
                ok = ok and _plain(page["properties"].get(name, {})) == want
            if ok:
                hits.append(page)
        return {"results": hits[: body.get("page_size", 100)], "has_more": False}

    def row(self, key=None):
        found = [p for p in self.pages if key is None or _plain(p["properties"]["Key"]) == key]
        return {k: _plain(v) for k, v in found[0]["properties"].items()} if found else None

    def set_status(self, key, status):
        page = next(p for p in self.pages if _plain(p["properties"]["Key"]) == key)
        page["properties"]["Status"] = {"type": "select", "select": {"name": status}}


class Delivery(unittest.TestCase):
    def setUp(self):
        self.n, self.key = FakeNotion(), D.key_of(TID)

    def go(self, *payloads, now=NOW):
        return D.deliver(list(payloads), now=now, client=self.n)

    def test_an_active_loop_becomes_one_followup_item_with_its_evidence_link(self):
        counts = self.go(payload([thread()]))
        self.assertEqual((counts["created"], len(self.n.pages)), (1, 1))
        row = self.n.row()
        self.assertEqual((row["From"], row["Subject"], row["Workspace"], row["Status"], row["Urgency"]), ("Followup", "Send the signed quote", "Personal", "New", "FYI"))
        self.assertEqual((D._moment(row["Due"]), row["Link"], row["Key"]), (D._moment("2026-11-20T15:00:00Z"), "https://mail.google.com/mail/u/0/#all/m1", self.key))
        self.assertLessEqual(len(row["Key"]), 64)

    def test_urgency_follows_the_state_and_the_checkpoint(self):
        cases = [("needs_action_today", "2026-11-15T15:00:00Z", "Now"), ("needs_action_today", "2026-11-17T20:00:00Z", "Today"), ("waiting_aging", "2026-11-12", "This week"), ("upcoming", "2026-11-25", "FYI")]
        for state, due, urgency in cases:
            with self.subTest(state=state, due=due):
                self.assertEqual(D.urgency_of(state, due, NOW), urgency)

    def test_a_rerun_adds_no_second_row_and_writes_nothing(self):
        self.go(payload([thread()]))
        before = len(self.n.writes)
        counts = self.go(payload([thread()]), now=NOW + timedelta(hours=1))
        self.assertEqual((counts["created"], counts["unchanged"], len(self.n.pages), len(self.n.writes)), (0, 1, 1, before))

    def test_an_updated_loop_updates_its_row_and_never_touches_status(self):
        self.go(payload([thread()]))
        self.n.set_status(self.key, "Snoozed")
        counts = self.go(payload([thread(state="needs_action_today", due="2026-11-16T15:00:00Z", title="Send the revised quote")]))
        row = self.n.row()
        self.assertEqual((counts["updated"], len(self.n.pages)), (1, 1))
        self.assertEqual((row["Subject"], row["Urgency"], D._moment(row["Due"]), row["Status"]), ("Send the revised quote", "Now", D._moment("2026-11-16T15:00:00Z"), "Snoozed"))

    def test_a_row_jim_already_decided_is_never_edited_or_reopened_or_duplicated(self):
        for status in ("Done", "Dismissed", "Promoted"):
            with self.subTest(status=status):
                n = FakeNotion()
                D.deliver([payload([thread()])], now=NOW, client=n)
                self.n = n
                n.set_status(self.key, status)
                writes = len(n.writes)
                counts = D.deliver([payload([thread(title="Changed", due="2026-12-01")])], now=NOW, client=n)
                self.assertEqual((counts["kept"], counts["created"], len(n.pages), len(n.writes), n.row()["Status"], n.row()["Subject"]), (1, 0, 1, writes, status, "Send the signed quote"))

    def test_review_needed_suggestions_are_never_sent(self):
        counts = self.go(payload([], review=[thread(state="review_needed")]))
        self.assertEqual((counts["created"], self.n.pages), (0, []))

    def test_a_loop_the_store_closed_marks_its_open_row_done_and_nothing_else_does(self):
        self.go(payload([thread()]))
        counts = self.go(payload([], closed=[TID]))
        self.assertEqual((counts["done"], self.n.row()["Status"]), (1, "Done"))
        self.assertEqual(self.go(payload([], closed=[TID]))["done"], 0)                      # already Done: untouched
        self.assertEqual(self.go(payload([], closed=["cd" * 32]))["done"], 0)                  # no such row: nothing happens
        self.n.set_status(self.key, "New")
        self.go(payload([]))                                                                   # a loop merely absent from a run is NOT proof: the row stays open
        self.assertEqual(self.n.row()["Status"], "New")

    def test_only_followup_rows_are_ever_touched(self):
        self.n.pages.append({"id": "other", "properties": {"Key": {"type": "rich_text", "rich_text": [{"plain_text": self.key}]}, "From": {"type": "rich_text", "rich_text": [{"plain_text": "claude-code"}]},
                                                          "Status": {"type": "select", "select": {"name": "New"}}, "Subject": {"type": "title", "title": [{"plain_text": "x"}]}}})
        self.go(payload([], closed=[TID]))
        self.assertEqual(self.n.pages[0]["properties"]["Status"]["select"]["name"], "New")
        self.assertEqual(self.n.writes, [])

    def test_unsafe_items_are_skipped_by_reason_and_never_written(self):
        bad = [thread(url="https://example.test/m1"), thread(tid="cd" * 32, title="Call jim@example.com about the quote"), thread(tid="ef" * 32, title="token=abcdef123456789 quote")]
        counts = self.go(payload(bad))
        self.assertEqual((counts["created"], counts["skipped"], self.n.pages), (0, 3, []))

    def test_tgp_and_unknown_workspaces_are_never_delivered(self):
        counts = self.go(payload([thread()], workspace="tgp"), payload([thread(tid="cd" * 32)], workspace="other"))
        self.assertEqual((counts["created"], counts["skipped"], self.n.pages), (0, 0, []))                  # dropped before any validation or call, not merely refused by it

    def test_the_sender_rate_limit_stops_a_flood_and_counts_it(self):
        threads = [thread(tid=f"{i:02x}" + "0" * 62, title=f"Loop {i} outcome") for i in range(agent_inbox.RATE_PER_HOUR + 3)]
        counts = self.go(payload(threads))
        self.assertEqual((counts["created"], counts["skipped"]), (agent_inbox.RATE_PER_HOUR, 3))

    def test_no_token_means_nothing_is_written_and_a_fixed_word_is_the_only_output(self):
        with mock.patch("builtins.print") as shown:
            counts = D.deliver([payload([thread()])], now=NOW, environ={})
        self.assertEqual((counts["failed"], counts["created"]), (1, 0))
        self.assertEqual([c.args[0] for c in shown.call_args_list], ["followup-inbox failed config_missing"])

    def test_a_loop_key_is_stable_per_thread_and_distinct_between_threads(self):
        self.assertEqual(D.key_of(TID), D.key_of(TID))
        self.assertNotEqual(D.key_of(TID), D.key_of("cd" * 32))


class Stage(unittest.TestCase):
    def production(self, rig):
        rig.wiring = S.Wiring(**{**rig.wiring.__dict__, "production": True, "env": {"X": "1"}})

    def test_a_live_run_delivers_the_published_payloads_once_and_a_dry_run_delivers_nothing(self):
        rig = Rig()
        self.production(rig)
        with mock.patch.object(S, "deliver_loops") as deliver, mock.patch.object(S, "refresh_tiles"):
            rig.run(live=False)
            deliver.assert_not_called()
            rig.run()
            self.assertEqual(deliver.call_count, 1)
            self.assertEqual(deliver.call_args.args[0], rig.published)

    def test_the_switch_off_delivers_nothing(self):
        called = mock.Mock()
        with mock.patch.object(S.switches, "state", return_value=False):
            self.assertIsNone(S.deliver_loops([payload([thread()])], NOW, {}, deliver=called))
        called.assert_not_called()

    def test_a_closed_outcome_is_listed_in_the_payload_by_its_thread_id(self):
        rig = Rig()
        rig.run()
        (stored,) = rig.store.threads("personal", ACCOUNT)
        rig.rows["gmail"] = [mail("m2", "2026-11-17T09:00:00+00:00", "done")]
        rig.wiring = S.Wiring(**{**rig.wiring.__dict__, "classify": lambda item: {"kind": "outcome_ended", "outcome_key": "Send the signed quote", "owner": "jim", "who": "", "title": "done"}})
        rig.run(at=T0 + timedelta(days=1))
        self.assertEqual(rig.published[-1]["closed"], [stored["thread_id"]])
        self.assertEqual(rig.published[-1]["closed"], [thread_id("personal", ACCOUNT, "Send the signed quote")])


if __name__ == "__main__":
    unittest.main()
