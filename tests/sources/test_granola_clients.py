import unittest
from unittest import mock

from lifeos.interview import granola_match
from lifeos.platform.notion_client import NotionError
from lifeos.sources import granola_auto as ga, granola_clients as gc
from tests.interview.test_granola_write import UUID, envelope
from tests.sources.test_granola_auto import Catalog, go, oracle

AT = chr(64)
CLIENT_PERSON = "pat" + AT + gc.CLIENT_DOMAIN
OWN_PERSON = "jim" + AT + gc.OWN_DOMAIN
PERSONAL = "jim" + AT + "gmail.com"
OTHER_CO = "sam" + AT + "othercompany.example"
SECOND = "9c3d2e1f-0a1b-4c2d-8e3f-1a2b3c4d5e6f"


def meeting(title="Weekly sync", attendees=(), start="2026-10-13T17:30:00Z", mid=UUID):
    return {"id": mid, "start": start, "title": title, "attendees": list(attendees)}


def M(**kw):
    m = meeting(**kw)
    return granola_match.Meeting(m["id"], m["start"], m["title"], "", tuple(m["attendees"]))


class Route(unittest.TestCase):
    def test_a_client_attendee_makes_it_a_client_meeting_whoever_else_is_on_it(self):
        self.assertEqual(gc.route(M(attendees=[CLIENT_PERSON, OWN_PERSON, PERSONAL, OTHER_CO])), (gc.CLIENT, "ROUTE_CLIENT_ATTENDEE"))

    def test_only_our_own_people_make_it_internal_and_a_personal_mailbox_does_not_count_as_external(self):
        self.assertEqual(gc.route(M(attendees=[OWN_PERSON, PERSONAL])), (gc.INTERNAL, "ROUTE_INTERNAL_ATTENDEE"))

    def test_another_company_on_an_internal_call_is_not_guessed(self):
        self.assertEqual(gc.route(M(attendees=[OWN_PERSON, OTHER_CO])), (None, "ROUTE_OTHER_ATTENDEES"))

    def test_with_no_attendee_evidence_the_title_decides(self):
        self.assertEqual(gc.route(M(title="TGP discovery call"))[0], gc.CLIENT)
        self.assertEqual(gc.route(M(title="Bytalos product review"))[0], gc.INTERNAL)
        self.assertEqual(gc.route(M(title="Bytalos prep for TGP"))[1], "ROUTE_TITLE_AMBIGUOUS")
        self.assertEqual(gc.route(M(title="Lunch"))[1], "ROUTE_NONE")

    def test_attendees_win_over_the_title(self):
        self.assertEqual(gc.route(M(title="Bytalos internal", attendees=[CLIENT_PERSON]))[0], gc.CLIENT)


class Pages:
    """The few Notion calls the client path makes: the project page, the callout, children lists, page create, block append."""
    def __init__(self, callout_type="callout", fail_link=False, drop_last=False):
        self.kids = {gc.CLIENT.parent: [], gc.INTERNAL.parent: [], gc.CLIENT.callout: [], gc.INTERNAL.callout: []}
        self.callout_type, self.fail_link, self.drop_last, self.calls = callout_type, fail_link, drop_last, []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/pages/"):
            return {"id": path.split("/")[2]}
        if method == "GET" and "/children" in path:
            blocks = self.kids[path.split("/")[2]]
            blocks = blocks[:-1] if self.drop_last and path.split("/")[2].startswith("page-") else blocks
            return {"results": blocks, "has_more": False, "next_cursor": None}
        if method == "GET" and path.startswith("/blocks/"):
            return {"id": path.split("/")[2], "type": self.callout_type}
        if method == "POST" and path == "/pages":
            pid = f"page-{len(self.kids)}"
            self.kids[pid] = [dict(b, id=f"b{i}") for i, b in enumerate(body["children"])]
            title = body["properties"]["title"]["title"][0]["text"]["content"]
            self.kids[body["parent"]["page_id"]].append({"id": pid, "type": "child_page", "child_page": {"title": title}})
            return {"id": pid}
        if method == "PATCH" and path.endswith("/children"):
            target = path.split("/")[2]
            if self.fail_link and target in (gc.CLIENT.callout, gc.INTERNAL.callout):
                raise NotionError("NOTION_NETWORK")
            self.kids[target].extend(dict(b, id=f"b{len(self.kids[target])}") for b in body["children"])
            return {"results": []}
        raise AssertionError((method, path))


class Write(unittest.TestCase):
    def test_a_client_meeting_becomes_one_page_under_the_project_listed_in_the_callout(self):
        notion = Pages()
        code, counts = gc.write(notion, gc.CLIENT, M(title="TGP kickoff"), envelope(), True)
        self.assertEqual((code, counts["readback_ok"], counts["listed"]), ("TRANSCRIPT_WRITTEN", 1, 1))
        page = next(b for b in notion.kids[gc.CLIENT.parent] if b["type"] == "child_page")
        self.assertEqual(page["child_page"]["title"], "2026-10-13 · TGP kickoff")
        link = notion.kids[gc.CLIENT.callout][0]
        self.assertEqual((link["type"], link["link_to_page"]["page_id"]), ("link_to_page", page["id"]))
        self.assertEqual(notion.kids[gc.INTERNAL.parent], [])

    def test_a_replay_changes_nothing_and_does_not_list_twice(self):
        notion = Pages()
        gc.write(notion, gc.CLIENT, M(), envelope(), True)
        before = len([c for c in notion.calls if c[0] in ("POST", "PATCH")])
        code, _ = gc.write(notion, gc.CLIENT, M(), envelope(), True)
        self.assertEqual(code, "TRANSCRIPT_NOOP")
        self.assertEqual(len([c for c in notion.calls if c[0] in ("POST", "PATCH")]), before)
        self.assertEqual(len(notion.kids[gc.CLIENT.callout]), 1)

    def test_a_page_of_that_title_that_is_not_ours_is_never_touched(self):
        notion = Pages()
        notion.kids["theirs"] = [{"id": "x", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Jim wrote this"}, "plain_text": "Jim wrote this"}]}}]
        notion.kids[gc.CLIENT.parent].append({"id": "theirs", "type": "child_page", "child_page": {"title": "2026-10-13 · Weekly sync"}})
        self.assertEqual(gc.write(notion, gc.CLIENT, M(), envelope(), True)[0], "CLIENT_PAGE_NOT_OURS")
        self.assertFalse([c for c in notion.calls if c[0] in ("POST", "PATCH")])

    def test_not_live_plans_and_writes_nothing(self):
        notion = Pages()
        self.assertEqual(gc.write(notion, gc.CLIENT, M(), envelope(), False)[0], "TRANSCRIPT_PLANNED")
        self.assertFalse([c for c in notion.calls if c[0] in ("POST", "PATCH")])

    def test_a_failed_listing_does_not_fail_a_written_and_read_back_page(self):
        code, counts = gc.write(Pages(fail_link=True), gc.INTERNAL, M(), envelope(), True)
        self.assertEqual((code, counts["listed"]), ("TRANSCRIPT_WRITTEN", 0))

    def test_a_page_that_does_not_read_back_is_a_mismatch(self):
        self.assertEqual(gc.write(Pages(drop_last=True), gc.CLIENT, M(), envelope(), True)[0], "TRANSCRIPT_READBACK_MISMATCH")

    def test_the_target_check_wants_a_real_callout(self):
        self.assertEqual(gc.check(Pages(), gc.CLIENT), "target_ok")
        self.assertEqual(gc.check(Pages(callout_type="paragraph"), gc.CLIENT), "target_mismatch")


class Auto(unittest.TestCase):
    def test_an_unmatched_client_meeting_is_carried_catalogued_and_only_then_purged(self):
        seen, notion, catalog = [], Pages(), Catalog()
        out = go(oracle([meeting(title="TGP discovery")], sink=seen), notion, catalog, rounds=())
        c = out["counts"]
        self.assertEqual((out["code"], c["client_routed"], c["carried"], c["purged"], c["unmatched"], c["left_on_oracle"], c["failed"]), ("GRAN_AUTO_DONE", 1, 1, 1, 0, 0, 0))
        self.assertEqual([op for op, _ in seen], ["sync", "list", "show", "purge"])
        self.assertEqual([s[2] for s in catalog.saved], ["carried", "carried_purged"])
        self.assertIsNone(catalog.saved[0][5])

    def test_a_meeting_that_matches_a_round_still_goes_to_the_round_not_a_client_page(self):
        row = granola_match.Round("round-page", "2026-10-13", "Becton Dickinson", "Senior Technical Program Manager", "")
        seen, notion, catalog = [], Pages(), Catalog()
        mock_write = mock.Mock(return_value=("TRANSCRIPT_WRITTEN", {}))
        with mock.patch.object(ga.granola_write, "write", mock_write):
            out = go(oracle([meeting(title="Becton Dickinson TGP", attendees=[CLIENT_PERSON])], sink=seen), notion, catalog, rounds=(row,))
        self.assertEqual((out["counts"]["client_routed"], mock_write.call_count), (0, 1))

    def test_an_unrelated_meeting_stays_on_oracle_exactly_as_before(self):
        seen, catalog = [], Catalog()
        out = go(oracle([meeting(title="Lunch")], sink=seen), Pages(), catalog, rounds=())
        c = out["counts"]
        self.assertEqual((c["client_routed"], c["unmatched"], c["left_on_oracle"], c["carried"], c["why"]), (0, 1, 1, 0, {"MATCH_NONE": 1}))
        self.assertEqual([op for op, _ in seen], ["sync", "list"])

    def test_an_ambiguous_route_is_counted_and_left_alone(self):
        out = go(oracle([meeting(title="Bytalos prep for TGP")]), Pages(), Catalog(), rounds=())
        self.assertEqual((out["counts"]["carried"], out["counts"]["why"]), (0, {"ROUTE_TITLE_AMBIGUOUS": 1, "MATCH_NONE": 1}))

    def test_a_target_that_cannot_be_read_leaves_the_meeting_on_oracle(self):
        seen, catalog = [], Catalog()
        out = go(oracle([meeting(title="TGP call")], sink=seen), Pages(callout_type="paragraph"), catalog, rounds=())
        c = out["counts"]
        self.assertEqual((c["carried"], c["left_on_oracle"], c["why"]), (0, 1, {"GRAN_CLIENT_TARGET_NOT_OK": 1}))
        self.assertNotIn("show", [op for op, _ in seen])
        self.assertEqual(catalog.rows, {})

    def test_a_dry_run_plans_the_client_page_and_purges_nothing(self):
        seen, notion, catalog = [], Pages(), Catalog()
        out = go(oracle([meeting(title="TGP call")], sink=seen), notion, catalog, live=False, rounds=())
        self.assertEqual((out["counts"]["carried"], out["counts"]["why"], catalog.saved), (0, {"TRANSCRIPT_PLANNED": 1}, []))
        self.assertNotIn("purge", [op for op, _ in seen])
        self.assertFalse([c for c in notion.calls if c[0] in ("POST", "PATCH")])

    def test_client_meetings_share_the_per_run_cap(self):
        extra = [meeting(title="TGP call", mid=f"00000000-0000-4000-8000-00000000000{i}", start=f"2026-10-13T1{i}:00:00Z") for i in range(4)]
        out = go(oracle(extra), Pages(), Catalog(), rounds=(), limit=2)
        self.assertEqual((out["counts"]["carried"], out["counts"]["why"].get("GRAN_AUTO_LATER_RUN")), (2, 2))


if __name__ == "__main__":
    unittest.main()


def go_with(value, send, client, catalog, live):
    """The shared Granola harness with GRAN_UNMATCHED_TO set for this run."""
    from tests.sources import test_granola_auto as t
    with mock.patch.object(t, "ENV", {**t.ENV, "GRAN_UNMATCHED_TO": value}):
        return t.go(send, client, catalog, live=live, rounds=())


class Instruction(unittest.TestCase):
    def run_with(self, value, live=True, extra=()):
        seen, notion, catalog = [], Pages(), Catalog()
        send = oracle([meeting(title="AI Update"), meeting(title="Lunch", mid=SECOND, start="2026-10-14T17:30:00Z"), *extra], sink=seen)
        return go_with(value, send, notion, catalog, live), seen, notion, catalog


class InstructionTests(Instruction):
    def test_with_the_instruction_unmatched_meetings_are_filed_as_asked(self):
        out, seen, notion, catalog = self.run_with("client")
        c = out["counts"]
        self.assertEqual((c["client_by_instruction"], c["carried"], c["purged"], c["unmatched"], c["failed"]), (2, 2, 2, 0, 0))
        self.assertEqual(len([b for b in notion.kids[gc.CLIENT.parent] if b["type"] == "child_page"]), 2)

    def test_internal_files_them_on_the_bytalos_page(self):
        out, _, notion, _ = self.run_with("internal")
        self.assertEqual(out["counts"]["carried"], 2)
        self.assertEqual(len([b for b in notion.kids[gc.INTERNAL.parent] if b["type"] == "child_page"]), 2)

    def test_without_it_or_with_a_value_that_is_not_one_of_the_two_nothing_changes(self):
        for value in ("", "elsewhere"):
            out, seen, notion, catalog = self.run_with(value)
            self.assertEqual((out["counts"]["carried"], out["counts"]["unmatched"], out["counts"]["client_by_instruction"]), (0, 2, 0))
            self.assertNotIn("show", [op for op, _ in seen])

    def test_the_instruction_never_overrides_a_decision_the_rules_made(self):
        out, _, _, _ = self.run_with("internal", extra=[meeting(title="Bytalos prep for TGP", mid="00000000-0000-4000-8000-000000000009", start="2026-10-15T17:30:00Z")])
        self.assertEqual(out["counts"]["why"].get("ROUTE_TITLE_AMBIGUOUS"), 1)
        self.assertEqual(out["counts"]["carried"], 2)

    def test_a_dry_run_with_the_instruction_shows_the_count_and_writes_nothing(self):
        out, seen, notion, catalog = self.run_with("client", live=False)
        self.assertEqual((out["counts"]["client_by_instruction"], out["counts"]["carried"], catalog.saved), (2, 0, []))
        self.assertNotIn("purge", [op for op, _ in seen])
