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
THIRD = "00000000-0000-4000-8000-000000000009"


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


def _typed(props):
    out = {}
    for name, value in props.items():
        kind = next(iter(value))
        out[name] = {"type": kind, **value}
    return out


def _matches(row, flt):
    if "and" in flt:
        return all(_matches(row, f) for f in flt["and"])
    prop = row["properties"].get(flt["property"])
    if "rich_text" in flt:
        return gc._plain(prop) == flt["rich_text"]["equals"]
    cond = flt["select"]
    value = gc._plain(prop)
    return value == cond["equals"] if "equals" in cond else bool(value)


class Meetings:
    """The Meetings data source: rows with typed properties and block children, the calls granola_clients makes, and Jim's File as choices."""
    def __init__(self, schema_ok=True, drop_last=False, fail_append=False):
        self.rows, self.schema_ok, self.drop_last, self.fail_append, self.calls = {}, schema_ok, drop_last, fail_append, []

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/data_sources/"):
            props = {name: {"type": kind} for name, kind in gc.SCHEMA.items()}
            if not self.schema_ok:
                props.pop(gc.FILE_AS)
            return {"properties": props}
        if method == "POST" and path == "/pages":
            assert body["parent"] == {"type": "data_source_id", "data_source_id": gc.DATA_SOURCE}
            pid = f"row-{len(self.rows)}"
            self.rows[pid] = {"id": pid, "properties": _typed(body["properties"]), "children": [dict(b, id=f"b{i}") for i, b in enumerate(body.get("children", []))]}
            return {"id": pid}
        if method == "PATCH" and path.startswith("/pages/"):
            self.rows[path.split("/")[2]]["properties"].update(_typed(body["properties"]))
            return {}
        if method == "PATCH" and path.endswith("/children"):
            if self.fail_append:
                raise NotionError("NOTION_NETWORK")
            kids = self.rows[path.split("/")[2]]["children"]
            kids.extend(dict(b, id=f"b{len(kids) + i}") for i, b in enumerate(body["children"]))
            return {"results": []}
        if method == "GET" and "/children" in path:
            kids = self.rows[path.split("/")[2]]["children"]
            kids = kids[:-1] if self.drop_last and kids else kids
            return {"results": kids, "has_more": False, "next_cursor": None}
        raise AssertionError((method, path))

    def call_once(self, method, path, body=None, version=None):
        return self.call(method, path, body)

    def query_data_source(self, source_id, body):
        assert source_id == gc.DATA_SOURCE
        return {"results": [r for r in self.rows.values() if _matches(r, body["filter"])], "has_more": False, "next_cursor": None}

    def choose(self, key, choice):
        row = next(r for r in self.rows.values() if gc._plain(r["properties"][gc.KEY]) == key)
        row["properties"][gc.FILE_AS] = {"type": "select", "select": {"name": choice}}
        return row

    def by_status(self, status):
        return [r for r in self.rows.values() if gc._plain(r["properties"][gc.STATUS]) == status]


class Write(unittest.TestCase):
    def test_a_meeting_becomes_one_filed_row_whose_page_holds_the_transcript(self):
        notion = Meetings()
        code, counts = gc.write(notion, gc.CLIENT, M(title="TGP kickoff"), "k1", envelope(), True)
        self.assertEqual((code, counts["readback_ok"]), ("TRANSCRIPT_WRITTEN", 1))
        row = next(iter(notion.rows.values()))
        p = row["properties"]
        self.assertEqual((gc._plain(p[gc.TITLE]), gc._plain(p[gc.STATUS]), gc._plain(p[gc.FILED_TO]), gc._plain(p[gc.KEY]), p[gc.DATE]["date"]["start"], p[gc.LINES]["number"]),
                         ("2026-10-13 · TGP kickoff", "Filed", "TGP", "k1", "2026-10-13", 3))

    def test_a_replay_changes_nothing(self):
        notion = Meetings()
        gc.write(notion, gc.CLIENT, M(), "k1", envelope(), True)
        before = len([c for c in notion.calls if c[0] in ("POST", "PATCH")])
        code, _ = gc.write(notion, gc.CLIENT, M(), "k1", envelope(), True)
        self.assertEqual((code, len(notion.rows)), ("TRANSCRIPT_NOOP", 1))
        self.assertEqual(len([c for c in notion.calls if c[0] in ("POST", "PATCH")]), before)

    def test_an_unfiled_row_gets_its_transcript_on_the_same_page(self):
        notion = Meetings()
        self.assertEqual(gc.unfiled_row(notion, M(), "k1", True), "created")
        code, _ = gc.write(notion, gc.PERSONAL, M(), "k1", envelope(), True)
        self.assertEqual((code, len(notion.rows), gc._plain(next(iter(notion.rows.values()))["properties"][gc.FILED_TO])), ("TRANSCRIPT_WRITTEN", 1, "Personal"))

    def test_a_row_page_holding_something_that_is_not_ours_is_never_touched(self):
        notion = Meetings()
        gc.unfiled_row(notion, M(), "k1", True)
        notion.rows["row-0"]["children"] = [{"id": "x", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Jim wrote this"}, "plain_text": "Jim wrote this"}]}}]
        self.assertEqual(gc.write(notion, gc.CLIENT, M(), "k1", envelope(), True)[0], "CLIENT_PAGE_NOT_OURS")
        self.assertEqual(len(notion.rows["row-0"]["children"]), 1)

    def test_a_page_with_our_marker_but_not_the_whole_transcript_is_never_called_done(self):
        notion = Meetings()
        gc.write(notion, gc.CLIENT, M(), "k1", envelope(), True)
        notion.rows["row-0"]["children"].pop()
        self.assertEqual(gc.write(notion, gc.CLIENT, M(), "k1", envelope(), True)[0], "TRANSCRIPT_READBACK_MISMATCH")

    def test_not_live_plans_and_writes_nothing(self):
        notion = Meetings()
        self.assertEqual(gc.write(notion, gc.CLIENT, M(), "k1", envelope(), False)[0], "TRANSCRIPT_PLANNED")
        self.assertFalse([c for c in notion.calls if c[0] in ("POST", "PATCH")])

    def test_a_page_that_does_not_read_back_is_a_mismatch_and_stays_unfiled(self):
        notion = Meetings(drop_last=True)
        self.assertEqual(gc.write(notion, gc.CLIENT, M(), "k1", envelope(), True)[0], "TRANSCRIPT_READBACK_MISMATCH")
        self.assertEqual(len(notion.by_status("Filed")), 0)

    def test_a_failed_append_is_a_write_failure(self):
        notion = Meetings(fail_append=True)
        gc.unfiled_row(notion, M(), "k1", True)
        self.assertEqual(gc.write(notion, gc.CLIENT, M(), "k1", envelope(), True)[0], "TRANSCRIPT_WRITE_FAILED")

    def test_the_target_check_wants_the_columns_the_job_writes(self):
        self.assertEqual(gc.check(Meetings()), "target_ok")
        self.assertEqual(gc.check(Meetings(schema_ok=False)), "target_mismatch")

    def test_unfiled_rows_are_made_once_and_never_in_a_dry_run(self):
        notion = Meetings()
        self.assertEqual(gc.unfiled_row(notion, M(), "k1", False), "planned")
        self.assertEqual((gc.unfiled_row(notion, M(), "k1", True), gc.unfiled_row(notion, M(), "k1", True)), ("created", "exists"))
        self.assertEqual(len(notion.rows), 1)


def run(meetings, notion, catalog=None, live=True, value="", limit=3, **kw):
    seen = []
    catalog = catalog or Catalog()
    from tests.sources import test_granola_auto as t
    with mock.patch.object(t, "ENV", {**t.ENV, "GRAN_UNMATCHED_TO": value}):
        out = go(oracle(meetings, sink=seen, **kw), notion, catalog, live=live, rounds=(), limit=limit, patch_meetings=False)
    return out, seen, catalog


class Auto(unittest.TestCase):
    def test_a_client_titled_meeting_is_filed_on_a_row_catalogued_and_only_then_purged(self):
        notion = Meetings()
        out, seen, catalog = run([meeting(title="TGP discovery")], notion)
        c = out["counts"]
        self.assertEqual((out["code"], c["client_routed"], c["carried"], c["purged"], c["unmatched"], c["failed"]), ("GRAN_AUTO_DONE", 1, 1, 1, 0, 0))
        self.assertEqual([op for op, _ in seen], ["sync", "list", "show", "purge"])
        self.assertEqual([s[2] for s in catalog.saved], ["carried", "carried_purged"])
        self.assertEqual(len(notion.by_status("Filed")), 1)

    def test_a_meeting_nothing_can_place_waits_as_an_unfiled_row_and_stays_on_oracle(self):
        notion = Meetings()
        out, seen, catalog = run([meeting(title="Lunch")], notion)
        c = out["counts"]
        self.assertEqual((c["carried"], c["unmatched"], c["unfiled_created"], c["unfiled_waiting"], c["left_on_oracle"]), (0, 1, 1, 1, 1))
        self.assertEqual([op for op, _ in seen], ["sync", "list"])
        self.assertEqual((len(notion.by_status("Unfiled")), catalog.rows), (1, {}))
        again, _, _ = run([meeting(title="Lunch")], notion)
        self.assertEqual((again["counts"]["unfiled_created"], again["counts"]["unfiled_exists"], len(notion.rows)), (0, 1, 1))

    def test_jims_choice_files_the_transcript_on_that_row_purges_and_closes_it(self):
        notion = Meetings()
        run([meeting(title="Lunch")], notion)
        notion.choose(ga.key_of(UUID), "Personal")
        out, seen, catalog = run([meeting(title="Lunch")], notion)
        c = out["counts"]
        self.assertEqual((c["triage_filed"], c["carried"], c["purged"], c["failed"], len(notion.rows)), (1, 1, 1, 0, 1))
        row = next(iter(notion.rows.values()))
        self.assertEqual((gc._plain(row["properties"][gc.STATUS]), gc._plain(row["properties"][gc.FILED_TO])), ("Filed", "Personal"))
        self.assertEqual([s[2] for s in catalog.saved], ["carried", "carried_purged"])
        self.assertEqual(c["unfiled_waiting"], 0)

    def test_a_choice_of_tgp_or_bytalos_files_it_there(self):
        for choice in ("TGP", "Bytalos"):
            notion = Meetings()
            run([meeting(title="Lunch")], notion)
            notion.choose(ga.key_of(UUID), choice)
            run([meeting(title="Lunch")], notion)
            self.assertEqual(gc._plain(next(iter(notion.rows.values()))["properties"][gc.FILED_TO]), choice)

    def test_delete_removes_the_meeting_from_oracle_keeps_no_transcript_and_marks_the_row(self):
        notion = Meetings()
        run([meeting(title="Lunch")], notion)
        notion.choose(ga.key_of(UUID), "Delete")
        out, seen, catalog = run([meeting(title="Lunch")], notion)
        self.assertEqual((out["counts"]["triage_deleted"], out["counts"]["carried"], [op for op, _ in seen if op == "purge"]), (1, 0, ["purge"]))
        row = next(iter(notion.rows.values()))
        self.assertEqual((gc._plain(row["properties"][gc.STATUS]), row["children"]), ("Deleted", []))

    def test_delete_that_oracle_does_not_confirm_leaves_the_row_open(self):
        notion = Meetings()
        run([meeting(title="Lunch")], notion)
        notion.choose(ga.key_of(UUID), "Delete")
        out, _, _ = run([meeting(title="Lunch")], notion, scope="utterances")
        self.assertEqual((out["counts"]["triage_deleted"], out["counts"]["why"].get("GRAN_PURGE_SCOPE_NOT_CONFIRMED")), (0, 1))
        self.assertEqual(len(notion.by_status("Unfiled")), 1)

    def test_a_delete_in_a_dry_run_purges_nothing(self):
        notion = Meetings()
        run([meeting(title="Lunch")], notion)
        notion.choose(ga.key_of(UUID), "Delete")
        out, seen, _ = run([meeting(title="Lunch")], notion, live=False)
        self.assertEqual((out["counts"]["triage_delete_planned"], [op for op, _ in seen if op == "purge"]), (1, []))

    def test_an_interview_choice_waits_for_the_round_picker(self):
        notion = Meetings()
        run([meeting(title="Lunch")], notion)
        notion.choose(ga.key_of(UUID), "Interview")
        out, seen, _ = run([meeting(title="Lunch")], notion)
        self.assertEqual((out["counts"]["triage_interview_later"], out["counts"]["carried"], "purge" in [op for op, _ in seen]), (1, 0, False))

    def test_a_row_whose_meeting_is_gone_from_oracle_is_closed(self):
        notion = Meetings()
        run([meeting(title="Lunch"), meeting(title="Other", mid=SECOND, start="2026-10-14T17:30:00Z")], notion)
        notion.choose(ga.key_of(UUID), "TGP")
        out, _, _ = run([meeting(title="Other", mid=SECOND, start="2026-10-14T17:30:00Z")], notion)
        self.assertEqual(out["counts"]["triage_gone"], 1)
        self.assertEqual(len(notion.by_status("Deleted")), 1)

    def test_an_ambiguous_route_is_counted_and_waits_as_an_unfiled_row(self):
        notion = Meetings()
        out, _, _ = run([meeting(title="Bytalos prep for TGP")], notion)
        self.assertEqual((out["counts"]["carried"], out["counts"]["unfiled_created"], out["counts"]["why"].get("ROUTE_TITLE_AMBIGUOUS")), (0, 1, 1))

    def test_a_meetings_table_that_cannot_be_read_leaves_everything_on_oracle(self):
        notion = Meetings(schema_ok=False)
        out, seen, catalog = run([meeting(title="TGP call"), meeting(title="Lunch", mid=SECOND, start="2026-10-14T17:30:00Z")], notion)
        c = out["counts"]
        self.assertEqual((c["carried"], c["left_on_oracle"], c["why"].get("GRAN_CLIENT_TARGET_NOT_OK")), (0, 2, 2))
        self.assertNotIn("show", [op for op, _ in seen])
        self.assertEqual((catalog.rows, notion.rows), ({}, {}))

    def test_a_dry_run_plans_rows_and_writes_nothing(self):
        notion = Meetings()
        out, seen, catalog = run([meeting(title="TGP call"), meeting(title="Lunch", mid=SECOND, start="2026-10-14T17:30:00Z")], notion, live=False)
        c = out["counts"]
        self.assertEqual((c["carried"], c["unfiled_planned"], catalog.saved, notion.rows), (0, 1, [], {}))
        self.assertNotIn("purge", [op for op, _ in seen])

    def test_decisions_and_client_meetings_share_the_per_run_cap(self):
        extra = [meeting(title="TGP call", mid=f"00000000-0000-4000-8000-00000000000{i}", start=f"2026-10-13T1{i}:00:00Z") for i in range(4)]
        out, _, _ = run(extra, Meetings(), limit=2)
        self.assertEqual((out["counts"]["carried"], out["counts"]["why"].get("GRAN_AUTO_LATER_RUN")), (2, 2))

    def test_the_one_off_instruction_files_unmatched_meetings_as_asked(self):
        notion = Meetings()
        out, _, _ = run([meeting(title="AI Update"), meeting(title="Lunch", mid=SECOND, start="2026-10-14T17:30:00Z")], notion, value="client")
        c = out["counts"]
        self.assertEqual((c["client_by_instruction"], c["carried"], c["unmatched"]), (2, 2, 0))
        self.assertEqual({gc._plain(r["properties"][gc.FILED_TO]) for r in notion.rows.values()}, {"TGP"})

    def test_the_instruction_never_overrides_a_decision_the_rules_made(self):
        notion = Meetings()
        out, _, _ = run([meeting(title="Bytalos prep for TGP", mid=THIRD)], notion, value="internal")
        self.assertEqual((out["counts"]["carried"], out["counts"]["unfiled_created"]), (0, 1))

    def test_a_meeting_that_matches_a_round_still_goes_to_the_round(self):
        row = granola_match.Round("round-page", "2026-10-13", "Becton Dickinson", "Senior Technical Program Manager", "")
        called = mock.Mock(return_value=("TRANSCRIPT_WRITTEN", {}))
        with mock.patch.object(ga.granola_write, "write", called):
            out = go(oracle([meeting(title="Becton Dickinson TGP", attendees=[CLIENT_PERSON])]), Meetings(), Catalog(), rounds=(row,), patch_meetings=False)
        self.assertEqual((out["counts"]["client_routed"], called.call_count), (0, 1))


if __name__ == "__main__":
    unittest.main()


class Sweep(unittest.TestCase):
    def test_with_nothing_new_a_row_whose_meeting_is_gone_is_still_closed_and_the_waiting_count_is_honest(self):
        notion = Meetings()
        run([meeting(title="Lunch")], notion)                                # an Unfiled row exists for UUID
        gc.unfiled_row(notion, M(title="Orphan", mid=SECOND), "orphankey00000000", True)
        out, _, _ = run([], notion, catalog=Catalog(done=[]))                # Oracle now lists nothing: both rows are orphans, nothing is new
        self.assertEqual((out["code"], out["counts"]["triage_gone"], out["counts"]["unfiled_waiting"]), ("GRAN_AUTO_NOTHING_NEW", 2, 0))
        self.assertEqual(len(notion.by_status("Deleted")), 2)

    def test_in_a_dry_run_nothing_is_closed_but_the_count_says_what_would_be(self):
        notion = Meetings()
        run([meeting(title="Lunch")], notion)
        out, _, _ = run([], notion, live=False)
        self.assertEqual((out["counts"]["triage_gone"], out["counts"]["unfiled_waiting"], len(notion.by_status("Unfiled"))), (1, 0, 1))
