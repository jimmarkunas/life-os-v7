"""D194: Confirm Rejected. A confirmed action writes one exact Job Ledger row with read-back.

All company names, roles and ids here are invented. The chain under test is the Close screen's: hiring.decision routes the request to its Ledger row by page id and confirm_rejected writes it through the Ledger client interface;
only Notion (a stateful in-memory Ledger) is a stand-in. The compiler's own tests are in CompilerTests; the Hiring Pipeline projection is tested in test_hiring."""
import copy
import json
import re
import unittest
from datetime import timedelta
from unittest import mock

from lifeos.hiring import decision as D, models as M, render
from lifeos.jobs import ledger
from lifeos.platform.notion_client import NotionError
from tests.hiring import test_hiring as T
from tests.kit.tree import rich

TARGET, SIBLING, OTHER, LEDGER = "a1" * 16, "b2" * 16, "c3" * 16, "e5" * 16
COMPANY, ROLE = "Fabrikam Retail", "Senior Manager, Operations"
SIBLING_ROLE, OTHER_COMPANY = "Director, Delivery", "Contoso Freight"
OP = "op-0123456789"
NOW = T.NOW


_OUTCOME_SCAN = mock.patch.object(T.stage.outcome_mail, "load", lambda conn: ({}, (NOW - T.timedelta(hours=1)).isoformat()))     # the outcome-mail scan saved an hour ago: these tests are about the decision, not that stage


def setUpModule():
    _OUTCOME_SCAN.start()


def tearDownModule():
    _OUTCOME_SCAN.stop()


def request(**changes):
    return {"action": "CONFIRM_REJECTED", "confirmed": True, "id": TARGET, "company": COMPANY, "role": ROLE, "reason": "Employer rejection email confirmed", "op": OP, **changes}


class Source:
    """A stateful synthetic Job Ledger data source: the pages, their edit times and the exact calls made. Failure switches cover every step of the write."""
    source = "e5" * 16                                   # the canonical Ledger data source: every row's parent names it

    def __init__(self, rows=None, schema=True):
        rows = [(TARGET, COMPANY, ROLE), (SIBLING, COMPANY, SIBLING_ROLE), (OTHER, OTHER_COMPANY, "Program Manager")] if rows is None else rows
        self.pages = {pid: self.make(pid, c, r) for pid, c, r in rows}
        self.log, self.edits, self.gets = [], 0, 0
        self.schema, self.fail_write, self.drop_write, self.tamper, self.fail_read, self.fail_second_read = schema, False, False, False, False, False
        self.on_get = {}

    @staticmethod
    def make(page_id, company, role, decision=None, history="", applied=True, stage=None):
        props = {"Company": {"rich_text": rich(company)}, "Job": {"title": rich(role)}, "Applied": {"checkbox": applied}, "Stage": {"select": {"name": stage} if stage else None}, "Applied On": {"date": {"start": "2026-10-02", "end": None, "time_zone": None}},
                 "Stable Job Key": {"rich_text": rich(f"key-{page_id[:6]}")},
                 "Saturn Decision": {"select": {"name": "Submitted"}}, "Hiring Decision": {"select": {"name": decision} if decision else None},
                 "Hiring Decision History": {"rich_text": rich(history) if history else []}, "Dismissed Outcome Ref": {"rich_text": []}}
        return {"id": page_id, "archived": False, "in_trash": False, "last_edited_time": "2026-10-05T00:00:00.000Z", "properties": props,
                "parent": {"type": "data_source_id", "data_source_id": LEDGER, "database_id": "d4" * 16}}

    def call(self, method, path, body=None):
        self.log.append((method, path.split("/")[1]))
        if self.fail_read:
            raise NotionError("NOTION_HTTP_500")
        if path.startswith("/blocks/"):                                                    # a row page's body (the pipeline reader): these rows have none
            return {"results": [], "has_more": False}
        props = {name: {"type": kind} for name, kind in ledger.REQUIRED.items()}
        if self.schema:
            props["Hiring Decision"] = {"type": "select", "select": {"options": [{"name": "Rejected"}, {"name": "Passed"}]}}
            props["Hiring Decision History"] = {"type": "rich_text"}
            props["Dismissed Outcome Ref"] = {"type": "rich_text"}
        return {"properties": props}

    def query_data_source(self, source_id=None, body=None):
        flt = body["filter"]
        staged = lambda p: bool((p["properties"].get("Stage") or {}).get("select"))                       # noqa: E731
        if flt == {"property": "Stage", "select": {"is_not_empty": True}}:        # LIFE-352: the Ledger-first read (the pipeline reader); not part of the write path under test
            if self.fail_read:
                raise NotionError("NOTION_HTTP_500")
            return {"results": [copy.deepcopy(p) for p in self.pages.values() if staged(p)], "has_more": False}
        self.log.append(("QUERY", "data_sources"))
        if self.fail_read:
            raise NotionError("NOTION_HTTP_500")
        if flt == {"property": "Applied", "checkbox": {"equals": True}}:
            return {"results": [copy.deepcopy(p) for p in self.pages.values() if p["properties"]["Applied"]["checkbox"]], "has_more": False}
        assert flt == D.PURSUIT, flt                                                                         # LIFE-353: the decision writers' read, Applied ticked or Stage set
        return {"results": [copy.deepcopy(p) for p in self.pages.values() if p["properties"]["Applied"]["checkbox"] or staged(p)], "has_more": False}

    def get_page(self, page_id):
        self.gets += 1
        self.log.append(("GET", "pages"))
        if self.on_get.get(self.gets):
            self.on_get[self.gets](self)
        if self.fail_second_read and self.gets >= 3:
            raise NotionError("NOTION_NETWORK")
        return copy.deepcopy(self.pages[page_id])

    def update_page_properties(self, page_id, properties):
        self.log.append(("PATCH", page_id, tuple(sorted(properties))))
        if self.fail_write:
            raise NotionError("NOTION_HTTP_502")
        if self.drop_write:
            return {}
        page = self.pages[page_id]
        for name, value in properties.items():
            page["properties"][name] = {"select": value["select"]} if "select" in value else {"rich_text": [{"plain_text": r["text"]["content"]} for r in value["rich_text"]]}
        if callable(self.tamper):
            self.tamper(page)
        elif self.tamper:
            page["properties"]["Applied"] = {"checkbox": False}
        self.edits += 1
        page["last_edited_time"] = f"2026-10-06T00:00:{self.edits:02d}.000Z"
        return {}

    def patches(self):
        return [e for e in self.log if e[0] == "PATCH"]

    def decision(self, page_id=TARGET):
        return self.pages[page_id]["properties"]["Hiring Decision"]["select"]

    def history(self, page_id=TARGET):
        return "".join(r["plain_text"] for r in self.pages[page_id]["properties"]["Hiring Decision History"]["rich_text"])


class Chain:
    """What the Close screen does: `route` finds the Ledger row by page id, then `confirm_rejected` writes it with read-back."""

    def __init__(self, source=None):
        self.source = source or Source()

    def confirm(self, req=None, live=True, now=NOW):
        req = req or request()
        want = D.parse(req)
        if D._page_id(D.route(D.rows(D.ledger_pages(self.source)), want)["id"]) != want["id"]:
            raise D.fail("STALE_IDENTITY")
        return D.confirm_rejected(self.source, req, live, now)


class ConfirmRejectedTests(unittest.TestCase):
    def test_a_confirmed_rejection_writes_the_exact_row_after_read_back(self):
        chain = Chain()
        before = copy.deepcopy(chain.source.pages)
        done = chain.confirm()
        self.assertEqual((done["status"], done["verified"], done["entries"]), ("recorded", True, 1))
        self.assertEqual(chain.source.patches(), [("PATCH", TARGET, ("Hiring Decision", "Hiring Decision History"))])      # one bounded write, two properties, one row
        self.assertEqual(chain.source.decision(), {"name": "Rejected"})
        entry = json.loads(chain.source.history())
        self.assertEqual(entry, {"action": "CONFIRM_REJECTED", "at": NOW.isoformat(timespec="seconds"), "op": OP, "reason": "Employer rejection email confirmed", "outcome": "Rejected"})
        for page_id in (SIBLING, OTHER):                                                           # another opportunity at the same company, and another company: not touched
            self.assertEqual(chain.source.pages[page_id], before[page_id])
        for name in ("Applied", "Applied On", "Saturn Decision", "Company", "Job"):               # the historical application stays exactly as it was
            self.assertEqual(chain.source.pages[TARGET]["properties"][name], before[TARGET]["properties"][name], name)

    def test_retrying_the_same_confirmed_operation_writes_and_appends_nothing_twice(self):
        chain = Chain()
        chain.confirm()
        again = chain.confirm()
        self.assertEqual((again["status"], again["verified"], again["entries"]), ("already_recorded", True, 1))
        self.assertEqual(len(chain.source.patches()), 1)
        self.assertEqual(len(chain.source.history().splitlines()), 1)

    def test_a_different_operation_on_a_decided_row_is_a_conflict_and_never_replaces_the_decision(self):
        chain = Chain()
        chain.confirm()
        with self.assertRaises(D.DecisionError) as error:
            chain.confirm(request(op="op-9876543210"))
        self.assertEqual(str(error.exception), "DECISION_CONFLICT")
        self.assertEqual(len(chain.source.patches()), 1)
        chain.source.pages[TARGET]["properties"]["Hiring Decision"] = {"select": {"name": "Passed"}}                  # a different existing decision
        chain.source.pages[TARGET]["properties"]["Hiring Decision History"] = {"rich_text": []}
        with self.assertRaises(D.DecisionError) as error:
            chain.confirm(request(op="op-5555555555"))
        self.assertEqual(str(error.exception), "DECISION_CONFLICT")
        self.assertEqual(len(chain.source.patches()), 1)

    def test_earlier_history_is_preserved_and_the_new_entry_is_appended_after_it(self):
        chain = Chain()
        old = json.dumps({"op": "op-earlier-001", "action": "CONFIRM_REJECTED", "at": "2026-09-01T09:00:00-05:00", "reason": "earlier"}, separators=(",", ":"), sort_keys=True)
        chain.source.pages[TARGET] = Source.make(TARGET, COMPANY, ROLE, decision=None, history=old)              # an earlier closure that was reopened: no decision, history kept
        chain.confirm()
        lines = chain.source.history().splitlines()
        self.assertEqual((len(lines), lines[0]), (2, old))
        self.assertEqual(json.loads(lines[1])["op"], OP)

    def test_a_history_that_cannot_hold_the_entry_or_cannot_be_read_is_refused_not_cut_or_skipped(self):
        for history, code in (("x" * D.HISTORY_MAX, "DECISION_HISTORY_UNREADABLE"), ("\n".join(json.dumps({"op": f"op-{n:010d}", "pad": "p" * 250}) for n in range(22)), "DECISION_HISTORY_FULL"), ('{"op": 1}', "DECISION_HISTORY_UNREADABLE")):
            chain = Chain()
            chain.source.pages[TARGET] = Source.make(TARGET, COMPANY, ROLE, history=history)
            with self.assertRaises(D.DecisionError) as error:
                chain.confirm()
            self.assertEqual(str(error.exception), code)
            self.assertEqual(chain.source.patches(), [])

    def test_a_dry_run_validates_everything_and_writes_nothing(self):
        chain = Chain()
        done = chain.confirm(live=False)
        self.assertEqual((done["status"], done["verified"]), ("dry_run", False))
        self.assertEqual(chain.source.patches(), [])


class IdentityAndAuthorizationTests(unittest.TestCase):
    def refused(self, chain, req, code, **kw):
        with self.assertRaises(NotionError) as error:
            chain.confirm(req, **kw)
        self.assertEqual(str(error.exception), code)
        self.assertEqual(chain.source.patches(), [])
        for private in (COMPANY, ROLE, TARGET, OP, "Employer rejection"):                          # fixed codes only
            self.assertNotIn(private, str(error.exception))

    def test_company_alone_never_selects_a_row(self):
        self.refused(Chain(), request(role="Vice President, Something Else"), "DECISION_STALE_IDENTITY")      # LIFE-353: the row is found by its page id, and its names must still agree
        self.refused(Chain(), request(role="Director, Delivery"), "DECISION_STALE_IDENTITY")                  # the sibling role exists, but not under this page id
        self.refused(Chain(), request(id="f6" * 16, role="Vice President, Something Else"), "DECISION_NO_MATCH")

    def test_two_ledger_rows_for_one_company_and_role_are_told_apart_by_page_id(self):
        chain = Chain(Source([(TARGET, COMPANY, ROLE), (SIBLING, COMPANY, ROLE), (OTHER, OTHER_COMPANY, "Program Manager")]))
        chain.confirm()                                                                              # LIFE-353: the page id decides, as the Stable Job Key does for create
        self.assertEqual((chain.source.decision(TARGET), chain.source.decision(SIBLING)), ({"name": "Rejected"}, None))
        self.refused(Chain(Source([(TARGET, COMPANY, ROLE), (SIBLING, COMPANY, ROLE)])), request(id="f6" * 16), "DECISION_AMBIGUOUS")      # by names alone, two rows are never guessed

    def test_a_missing_row_or_a_row_that_is_no_longer_applied_has_no_match(self):
        chain = Chain(Source([(SIBLING, COMPANY, SIBLING_ROLE)]))
        self.refused(chain, request(), "DECISION_NO_MATCH")
        gone = Chain()
        gone.source.pages[TARGET]["properties"]["Applied"] = {"checkbox": False}
        self.refused(gone, request(), "DECISION_NO_MATCH")

    def test_only_an_explicit_complete_confirmation_is_accepted(self):
        for bad in ({"confirmed": False}, {"confirmed": "yes"}, {"action": "PASS_ON_JOB"}, {"action": None}):
            self.refused(Chain(), request(**bad), "DECISION_NOT_CONFIRMED")
        for bad in ({"reason": ""}, {"reason": "   "}, {"reason": "x" * (D.REASON_MAX + 1)}, {"reason": "two\nlines"}, {"op": "short"}, {"op": "bad op id!"}, {"id": "not-an-id"}, {"company": ""}, {"role": " "}):
            self.refused(Chain(), request(**bad), "DECISION_REQUEST_INVALID")
        missing = request()
        del missing["confirmed"]
        self.refused(Chain(), missing, "DECISION_NOT_CONFIRMED")
        self.refused(Chain(), "confirm everything", "DECISION_NOT_CONFIRMED")

    def test_a_reason_at_the_limit_is_accepted_whole(self):
        chain = Chain()
        chain.confirm(request(reason="r" * D.REASON_MAX))
        self.assertEqual(json.loads(chain.source.history())["reason"], "r" * D.REASON_MAX)

    def test_a_missing_or_wrong_schema_fails_closed_before_any_write(self):
        self.refused(Chain(Source(schema=False)), request(), "DECISION_SCHEMA_MISSING")
        chain = Chain()
        base = chain.source.call
        chain.source.call = lambda m, p, b=None: {"properties": {**base(m, p, b)["properties"], "Hiring Decision": {"type": "select", "select": {"options": [{"name": "Rejected"}]}}}}      # Passed option missing
        self.refused(chain, request(), "DECISION_SCHEMA_MISSING")

    def test_an_unreadable_notion_state_fails_closed_and_is_never_reported_as_success(self):
        chain = Chain()
        chain.source.fail_read = True
        with self.assertRaises(NotionError):
            chain.confirm()
        self.assertEqual(chain.source.patches(), [])

    def changed_after(self, call, edit, code="DECISION_STALE_IDENTITY"):
        chain = Chain()
        chain.source.on_get = {call: edit}
        with self.assertRaises(NotionError) as error:
            chain.confirm()
        self.assertEqual(str(error.exception), code)
        self.assertEqual(chain.source.patches(), [])

    def test_a_row_that_changes_after_the_query_but_before_the_exact_fetch_authorizes_nothing(self):
        def unapply(source):
            source.pages[TARGET]["properties"]["Applied"] = {"checkbox": False}
        def retitle(source):
            source.pages[TARGET]["properties"]["Job"] = {"title": rich("Vice President, Something Else")}
        def recompany(source):
            source.pages[TARGET]["properties"]["Company"] = {"rich_text": rich("Contoso Freight")}
        def trash(source):
            source.pages[TARGET]["in_trash"] = True
        for edit in (unapply, retitle, recompany, trash):
            self.changed_after(1, edit)                              # the first exact fetch already disagrees with what the query matched

    def test_a_row_that_disagrees_only_on_the_first_exact_fetch_is_refused_as_stale_not_merely_changed(self):
        chain = Chain()
        chain.source.on_get = {1: lambda source: source.pages[TARGET]["properties"].update(Applied={"checkbox": False}),
                               2: lambda source: source.pages[TARGET]["properties"].update(Applied={"checkbox": True})}          # a transient edit: the second read looks right again
        with self.assertRaises(NotionError) as error:
            chain.confirm()
        self.assertEqual(str(error.exception), "DECISION_STALE_IDENTITY")
        self.assertEqual(chain.source.patches(), [])

    def test_a_row_that_changes_between_the_first_fetch_and_the_write_authorizes_nothing_even_with_an_unchanged_edit_time(self):
        def unapply(source):
            source.pages[TARGET]["properties"]["Applied"] = {"checkbox": False}
        def retitle(source):
            source.pages[TARGET]["properties"]["Job"] = {"title": rich("Vice President, Something Else")}
        for edit in (unapply, retitle):
            self.changed_after(2, edit)                              # the second fetch, immediately before the write: two consistent reads can no longer authorize a wrong closure

    def test_a_fetched_page_that_is_not_a_row_of_the_canonical_ledger_is_refused_at_both_fetches(self):
        for parent in ({"type": "data_source_id", "data_source_id": "f6" * 16}, {"type": "page_id", "page_id": TARGET}, {"type": "data_source_id"}, {}, None):
            for call in (1, 2):
                self.changed_after(call, lambda source, parent=parent: source.pages[TARGET].update(parent=parent), "DECISION_NOT_LEDGER_ROW")
        chain = Chain()
        chain.source.pages[TARGET]["parent"] = {"type": "data_source_id", "data_source_id": ("E5" * 16)[:32].upper(), "database_id": "d4" * 16}      # the same id in another case is still the Ledger
        chain.confirm()
        self.assertEqual(chain.source.decision(), {"name": "Rejected"})

    def test_a_row_whose_stable_key_or_dismissal_ref_or_full_date_changes_before_the_write_is_not_written(self):
        edits = (lambda source: source.pages[TARGET]["properties"].update({"Stable Job Key": {"rich_text": rich("other-key")}}),
                 lambda source: source.pages[TARGET]["properties"].update({"Dismissed Outcome Ref": {"rich_text": rich("mail-ref-1")}}),
                 lambda source: source.pages[TARGET]["properties"].update({"Applied On": {"date": {"start": "2026-10-02", "end": "2026-10-09", "time_zone": None}}}),
                 lambda source: source.pages[TARGET]["properties"].update({"Applied On": {"date": {"start": "2026-10-02", "end": None, "time_zone": "America/Chicago"}}}))
        for edit in edits:
            self.changed_after(2, edit, "DECISION_CONCURRENT_CHANGE")                           # nothing moved the edit time: the exact fields themselves are compared

    def test_an_archived_row_is_refused(self):
        chain = Chain()
        chain.source.pages[TARGET]["archived"] = True
        self.refused(chain, request(), "DECISION_STALE_IDENTITY")


class FailedWriteTests(unittest.TestCase):
    """Whatever goes wrong, nothing reports success."""

    def broken(self, code, **switch):
        chain = Chain()
        for name, value in switch.items():
            setattr(chain.source, name, value)
        with self.assertRaises(NotionError) as error:
            chain.confirm()
        self.assertEqual(str(error.exception), code)
        return chain

    def test_a_refused_write_is_a_failure(self):
        chain = self.broken("DECISION_WRITE_FAILED", fail_write=True)
        self.assertEqual(chain.source.decision(), None)

    def test_a_write_the_server_acknowledged_but_did_not_keep_is_a_failed_read_back(self):
        chain = self.broken("DECISION_READBACK_MISMATCH", drop_write=True)
        self.assertEqual(chain.source.decision(), None)

    def test_a_write_that_also_changed_the_application_record_is_a_failed_read_back(self):
        self.broken("DECISION_READBACK_MISMATCH", tamper=True)

    def test_a_write_that_also_changed_the_stable_key_dismissal_ref_or_full_date_is_a_failed_read_back(self):
        edits = (lambda page: page["properties"].update({"Stable Job Key": {"rich_text": rich("other-key")}}),
                 lambda page: page["properties"].update({"Dismissed Outcome Ref": {"rich_text": rich("mail-ref-1")}}),
                 lambda page: page["properties"].update({"Applied On": {"date": {"start": "2026-10-02", "end": "2026-10-09", "time_zone": None}}}),
                 lambda page: page["properties"].update({"Applied On": {"date": {"start": "2026-10-02", "end": None, "time_zone": "America/Chicago"}}}))
        for edit in edits:
            self.broken("DECISION_READBACK_MISMATCH", tamper=edit)

    def test_an_unreadable_row_after_the_write_is_a_failed_read_back(self):
        self.broken("DECISION_READBACK_FAILED", fail_second_read=True)

    def test_a_row_changed_by_someone_else_between_the_read_and_the_write_is_not_overwritten(self):
        def edit(source):
            source.pages[TARGET]["last_edited_time"] = "2026-10-06T01:00:00.000Z"
        chain = Chain()
        chain.source.on_get = {2: edit}                    # the second read of the exact row, just before the write
        with self.assertRaises(NotionError) as error:
            chain.confirm()
        self.assertEqual(str(error.exception), "DECISION_CONCURRENT_CHANGE")
        self.assertEqual(chain.source.patches(), [])

    def test_a_write_that_landed_but_whose_response_was_lost_is_finished_by_the_same_operation(self):
        chain = Chain()
        chain.source.fail_second_read = False
        real = chain.source.update_page_properties

        def lost(page_id, properties):
            real(page_id, properties)
            raise NotionError("NOTION_NETWORK")
        chain.source.update_page_properties = lost
        with self.assertRaises(NotionError) as error:
            chain.confirm()
        self.assertEqual(str(error.exception), "DECISION_WRITE_FAILED")                              # an uncertain write is a failure, never a success
        chain.source.update_page_properties = real
        done = chain.confirm()                                                                       # the same operation id: the row already carries it, so it only verifies
        self.assertEqual((done["status"], len(chain.source.history().splitlines())), ("already_recorded", 1))


class CompilerTests(unittest.TestCase):
    def rows(self, applied, parents=()):
        return T.compile_(applied, parents)

    def applied(self, company, role, decision=None, ident=None):
        return {**T.applied(company, role), "id": ident, "decision": decision}

    def test_a_confirmed_closure_outcome_or_passed_closes_only_that_pursuit(self):
        for value in M.OUTCOMES + ("Passed",):
            got = self.rows([self.applied("Acme", "PM", value, "i1"), self.applied("Acme", "Director", None, "i2"), self.applied("Globex", "PM", None, "i3")])
            self.assertEqual(sorted((r["company"], r["role"]) for r in got["rows"]), [("Acme", "Director"), ("Globex", "PM")])
            self.assertEqual(got["closed"], 1)
            self.assertEqual({r["role"]: r["ledger_id"] for r in got["rows"]}, {"Director": "i2", "PM": "i3"})

    def test_the_closed_decisions_are_the_six_outcomes_and_passed_and_nothing_else_closes(self):
        self.assertEqual(M.CLOSED_DECISIONS, M.OUTCOMES + ("Passed",))                     # LIFE-47 S2: a closure the screen records and reads back leaves the hourly report
        self.assertEqual(M.OUTCOMES, ("Rejected", "Withdrew", "Position filled", "Declined offer", "Hired", "Other"))

    def test_an_active_notes_page_does_not_keep_a_confirmed_closure_open(self):
        got = self.rows([self.applied("Acme", "PM", "Rejected", "i1")], [{"id": "p1", "company": "Acme", "role": "PM", "rounds": 3}])
        self.assertEqual((got["rows"], got["closed"]), ([], 1))

    def test_an_unknown_or_empty_decision_value_never_closes_anything(self):
        for value in (None, "", "Reopened", "rejected"):
            got = self.rows([self.applied("Acme", "PM", value, "i1")])
            self.assertEqual((len(got["rows"]), got["closed"]), (1, 0), value)

    def test_a_decision_on_one_of_two_applications_for_one_company_and_role_closes_nothing_and_is_flagged(self):
        got = self.rows([self.applied("Acme", "PM", None, "i1"), self.applied("Acme", "PM", "Rejected", "i2")])
        self.assertEqual((len(got["rows"]), got["closed"], got["reasons"]), (1, 0, ["DECISION_AMBIGUOUS"]))           # still listed, with a degraded signal, until the application identity is proven
        self.assertIsNone(got["rows"][0]["ledger_id"])
        first = self.rows([self.applied("Acme", "PM", "Passed", "i1"), self.applied("Acme", "PM", None, "i2"), self.applied("Acme", "PM", None, "i3")])
        self.assertEqual((len(first["rows"]), first["closed"], first["reasons"]), (1, 0, ["DECISION_AMBIGUOUS"]))     # whichever duplicate carries it, and however many there are
        both = self.rows([self.applied("Acme", "PM", "Rejected", "i1"), self.applied("Acme", "PM", "Rejected", "i2")])
        self.assertEqual((len(both["rows"]), both["closed"], both["reasons"]), (1, 0, ["DECISION_AMBIGUOUS"]))        # even when both say so: two applications are not one decision
        for value in M.OUTCOMES:                                                                                        # and for every outcome: a duplicate is never read as a safely closed pursuit
            dup = self.rows([self.applied("Acme", "PM", value, "i1"), self.applied("Acme", "PM", None, "i2")])
            self.assertEqual((len(dup["rows"]), dup["closed"], dup["reasons"]), (1, 0, ["DECISION_AMBIGUOUS"]), value)
        rendered = render.render(got, None, NOW)[0]
        self.assertTrue(rendered.startswith("DEGRADED"))
        self.assertIn("one of several applications", rendered)

    def test_duplicate_applications_without_any_decision_stay_one_quiet_opportunity(self):
        got = self.rows([self.applied("Acme", "PM", None, "i1"), self.applied("Acme", "PM", None, "i2")])
        self.assertEqual((len(got["rows"]), got["closed"], got["reasons"]), (1, 0, []))
        self.assertIsNone(got["rows"][0]["ledger_id"])

    def test_the_legacy_retired_notes_closure_still_works_beside_the_new_one(self):
        retired = {"id": "r1", "company": "Acme", "role": "Old", "rounds": 1, "retired": True}
        got = self.rows([self.applied("Acme", "Old", None, "i1"), self.applied("Acme", "PM", "Rejected", "i2"), self.applied("Acme", "Live", None, "i3")], [retired])
        self.assertEqual([r["role"] for r in got["rows"]], ["Live"])
        self.assertEqual(got["closed"], 2)

    def test_the_decision_and_the_row_id_are_read_from_the_ledger_page(self):
        source = Source()
        source.pages[TARGET] = Source.make(TARGET, COMPANY, ROLE, decision="Rejected")
        rows = T.stage.read_applied(source)
        self.assertEqual({(r["id"], r["decision"]) for r in rows}, {(TARGET, "Rejected"), (SIBLING, None), (OTHER, None)})


class BoundaryTests(unittest.TestCase):
    ROOT = T.ROOT

    def test_the_only_writes_are_the_decision_properties_and_the_hand_set_property_and_no_page_moves(self):
        text = (self.ROOT / "lifeos/hiring/decision.py").read_text()
        self.assertEqual(len(re.findall(r"update_page_properties\(", text)), 2)
        self.assertEqual(re.findall(r"update_page_properties\(want\[\"id\"\], \{(\w+): .*?\*\*\(\{(\w+): ", text), [("DECISION", "HISTORY")])
        self.assertEqual(re.findall(r"update_page_properties\(want\[\"id\"\], \{(\w+): \{\"rich_text\": rich_text\(new\)\}\}\)", text), ["HAND_SET"])     # LIFE-111/112: the one hand-set property, alone
        for banned in ("call_once", "/move", "/children", "Retired Opportunities", "Active Opportunities", "tinyfish", "gmail", "outlook", ".create(", "lifeos.jobs", "lifeos.interview", "lifeos.jira",
                       '"DELETE"', '"archived": True', '"in_trash": True', "Applied\": {\"checkbox\": ", "\"Stage\": {\"select\": {\"name"):
            self.assertNotIn(banned.lower(), text.lower(), banned)                                 # LIFE-353: no page move, no block write, Stage and Applied never written
        self.assertNotIn("update_page_properties", (self.ROOT / "lifeos/sources/pipeline/hiring.py").read_text())

    def test_the_action_is_not_reachable_from_any_stage_workflow_or_schedule(self):
        for path in (self.ROOT / ".github/workflows").glob("*.yml"):
            self.assertNotIn("confirm_rejected", path.read_text(), path.name)
        from lifeos import run as entry
        self.assertFalse([name for name, stage in entry.STAGES.items() if "decision" in str(getattr(stage, "target", ""))])


if __name__ == "__main__":
    unittest.main()
