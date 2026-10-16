"""D194: Confirm Rejected. A confirmed action writes one exact Job Ledger row with read-back, and only then does the Hiring Pipeline projection drop the pursuit.

All company names, roles and ids here are invented. The chain under test is the production one: stage.confirm_rejected -> hiring.decision -> the Ledger client interface -> stage.run -> compiler -> region writer;
only Notion (a stateful in-memory Ledger and the page tree), the snapshot store and the Calendar are stand-ins."""
import copy
import json
import re
import unittest
from datetime import timedelta
from unittest import mock

from lifeos.hiring import compile as C, decision as D, models as M, render
from lifeos.jobs import hiring_pipeline, ledger
from lifeos.platform.notion_client import NotionError
from tests.hiring import test_hiring as T
from tests.kit.tree import rich

TARGET, SIBLING, OTHER, LEDGER = "a1" * 16, "b2" * 16, "c3" * 16, "e5" * 16
COMPANY, ROLE = "Fabrikam Retail", "Senior Manager, Operations"
SIBLING_ROLE, OTHER_COMPANY = "Director, Delivery", "Contoso Freight"
OP = "op-0123456789"
NOW = T.NOW


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
    def make(page_id, company, role, decision=None, history=""):
        props = {"Company": {"rich_text": rich(company)}, "Job": {"title": rich(role)}, "Applied": {"checkbox": True}, "Applied On": {"date": {"start": "2026-10-02", "end": None, "time_zone": None}},
                 "Stable Job Key": {"rich_text": rich(f"key-{page_id[:6]}")},
                 "Saturn Decision": {"select": {"name": "Submitted"}}, "Hiring Decision": {"select": {"name": decision} if decision else None},
                 "Hiring Decision History": {"rich_text": rich(history) if history else []}, "Dismissed Outcome Ref": {"rich_text": []}}
        return {"id": page_id, "archived": False, "in_trash": False, "last_edited_time": "2026-10-05T00:00:00.000Z", "properties": props,
                "parent": {"type": "data_source_id", "data_source_id": LEDGER, "database_id": "d4" * 16}}

    def call(self, method, path, body=None):
        self.log.append((method, path.split("/")[1]))
        if self.fail_read:
            raise NotionError("NOTION_HTTP_500")
        props = {name: {"type": kind} for name, kind in ledger.REQUIRED.items()}
        if self.schema:
            props["Hiring Decision"] = {"type": "select", "select": {"options": [{"name": "Rejected"}, {"name": "Passed"}]}}
            props["Hiring Decision History"] = {"type": "rich_text"}
            props["Dismissed Outcome Ref"] = {"type": "rich_text"}
        return {"properties": props}

    def query_data_source(self, source_id=None, body=None):
        self.log.append(("QUERY", "data_sources"))
        flt = body["filter"]
        if self.fail_read:
            raise NotionError("NOTION_HTTP_500")
        if flt == {"property": "Applied", "checkbox": {"equals": True}}:
            return {"results": [copy.deepcopy(p) for p in self.pages.values() if p["properties"]["Applied"]["checkbox"]], "has_more": False}
        word = flt["and"][0]["rich_text"]["contains"]                                                       # the unapplied-row probe: the company's first word, not yet Applied
        assert flt == {"and": [{"property": "Company", "rich_text": {"contains": word}}, {"property": "Applied", "checkbox": {"equals": False}}]}
        return {"results": [copy.deepcopy(p) for p in self.pages.values() if not p["properties"]["Applied"]["checkbox"] and word in "".join(r["plain_text"] for r in p["properties"]["Company"]["rich_text"]).lower()], "has_more": False}

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


class Untouchable:
    def __getattr__(self, name):
        raise AssertionError(f"the Notes pages were touched: {name}")


PARENT = hiring_pipeline.Opportunity("aaaa1111-0000-4000-8000-0000000000aa", f"{COMPANY} - {ROLE}", hiring_pipeline.ACTIVE, 2, COMPANY, ROLE)       # an Active Notes page with two interview rounds
SIBLING_PARENT = hiring_pipeline.Opportunity("aaaa1111-0000-4000-8000-0000000000bb", f"{COMPANY} - {SIBLING_ROLE}", hiring_pipeline.ACTIVE, 0, COMPANY, SIBLING_ROLE)


def seen(tree):
    return [row[0][0] for row in T.table_state(tree)]                                 # the first cell is "Company — Role"


class Chain:
    """The whole production path against the stand-ins. `tree` is the Daily Report page, `store` the private projection row."""

    def __init__(self, source=None, parents=(PARENT, SIBLING_PARENT)):
        self.source, self.parents, self.tree, self.store = source or Source(), list(parents), T.page(extra_rows=0), T.FakeStore()

    def _patched(self):
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(T.snapshot, "STORE", self.store))
        stack.enter_context(mock.patch.object(T.stage.hiring_pipeline, "snapshot", lambda client, root: (list(self.parents), "ok")))
        return stack

    def project(self):
        with self._patched():
            return T.stage.run(0, True, T.RUN_ENV, ledger_client=self.source, pipeline_client=Untouchable(), calendar=T.FakeCalendar([]), connection=object(), client=self.tree, now=NOW)

    def confirm(self, req=None, live=True, now=NOW, calendar=None):
        with self._patched():
            return T.stage.confirm_rejected(req or request(), live, T.RUN_ENV, ledger_client=self.source, now=now, pipeline_client=Untouchable(), calendar=calendar or T.FakeCalendar([]), connection=object(), client=self.tree)


class ConfirmRejectedTests(unittest.TestCase):
    def test_a_confirmed_rejection_writes_the_exact_row_and_the_pursuit_leaves_the_active_queue_after_read_back(self):
        chain = Chain()
        chain.project()
        self.assertEqual(sorted(seen(chain.tree)), sorted([f"{COMPANY} — {ROLE}", f"{COMPANY} — {SIBLING_ROLE}", f"{OTHER_COMPANY} — Program Manager"]))
        before = copy.deepcopy(chain.source.pages)
        done = chain.confirm()
        self.assertEqual((done["status"], done["verified"], done["entries"]), ("recorded", True, 1))
        self.assertEqual(done["projection"], {"status": "fresh", "rows": 2, "closed": 1, "verified": True, "target_listed": False})
        self.assertIs(done["cleared"], True)
        self.assertNotIn(f"{COMPANY} — {ROLE}", seen(chain.tree))                                # gone from the Daily Report table only after the verified write
        self.assertEqual(sorted(seen(chain.tree)), sorted([f"{COMPANY} — {SIBLING_ROLE}", f"{OTHER_COMPANY} — Program Manager"]))
        self.assertEqual(chain.source.patches(), [("PATCH", TARGET, ("Hiring Decision", "Hiring Decision History"))])      # one bounded write, two properties, one row
        self.assertEqual(chain.source.decision(), {"name": "Rejected"})
        entry = json.loads(chain.source.history())
        self.assertEqual(entry, {"action": "CONFIRM_REJECTED", "at": NOW.isoformat(timespec="seconds"), "op": OP, "reason": "Employer rejection email confirmed", "outcome": "Rejected"})
        for page_id in (SIBLING, OTHER):                                                           # another opportunity at the same company, and another company: not touched
            self.assertEqual(chain.source.pages[page_id], before[page_id])
        for name in ("Applied", "Applied On", "Saturn Decision", "Company", "Job"):               # the historical application stays exactly as it was
            self.assertEqual(chain.source.pages[TARGET]["properties"][name], before[TARGET]["properties"][name], name)

    def test_the_active_notes_page_and_its_interview_rounds_stay_where_they_are_while_the_pursuit_is_closed(self):
        chain = Chain()
        chain.confirm()
        self.assertEqual([p.rounds for p in chain.parents if p.company == COMPANY and p.role == ROLE], [2])           # the Notes page is still an Active page with its rounds
        self.assertNotIn(f"{COMPANY} — {ROLE}", seen(chain.tree))
        self.assertEqual({e[1].split("/")[2] for e in T.writes(chain.tree)}, {"0000000000000000000000000000a001", "0000000000000000000000000000a003"})     # only the pinned region

    def test_retrying_the_same_confirmed_operation_writes_and_appends_nothing_twice(self):
        chain = Chain()
        chain.confirm()
        again = chain.confirm()
        self.assertEqual((again["status"], again["verified"], again["entries"]), ("already_recorded", True, 1))
        self.assertEqual(len(chain.source.patches()), 1)
        self.assertEqual(len(chain.source.history().splitlines()), 1)
        self.assertNotIn(f"{COMPANY} — {ROLE}", seen(chain.tree))

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
        self.assertEqual((done["status"], done["verified"], done["cleared"]), ("dry_run", False, False))
        self.assertEqual((chain.source.patches(), T.writes(chain.tree)), ([], []))


class IdentityAndAuthorizationTests(unittest.TestCase):
    def refused(self, chain, req, code, **kw):
        with self.assertRaises(NotionError) as error:
            chain.confirm(req, **kw)
        self.assertEqual(str(error.exception), code)
        self.assertEqual(chain.source.patches(), [])
        self.assertEqual(T.writes(chain.tree), [])
        for private in (COMPANY, ROLE, TARGET, OP, "Employer rejection"):                          # fixed codes only
            self.assertNotIn(private, str(error.exception))

    def test_company_alone_never_selects_a_row(self):
        self.refused(Chain(), request(role="Vice President, Something Else"), "DECISION_NO_MATCH")
        self.refused(Chain(), request(role="Director, Delivery"), "DECISION_STALE_IDENTITY")          # the sibling role exists, but not under this page id

    def test_two_ledger_rows_for_one_company_and_role_are_ambiguous_and_nothing_is_written(self):
        chain = Chain(Source([(TARGET, COMPANY, ROLE), (SIBLING, COMPANY, ROLE), (OTHER, OTHER_COMPANY, "Program Manager")]))
        self.refused(chain, request(), "DECISION_AMBIGUOUS")

    def test_a_missing_row_or_a_row_that_is_no_longer_applied_has_no_match(self):
        chain = Chain(Source([(SIBLING, COMPANY, SIBLING_ROLE)]), parents=())               # no Notes page either: nothing to close (with one, the pursuit routes through its Notes page, D195)
        self.refused(chain, request(), "DECISION_NO_MATCH")
        gone = Chain(parents=())
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
        self.assertEqual((chain.source.patches(), T.writes(chain.tree)), ([], []))

    def changed_after(self, call, edit, code="DECISION_STALE_IDENTITY"):
        chain = Chain()
        chain.source.on_get = {call: edit}
        with self.assertRaises(NotionError) as error:
            chain.confirm()
        self.assertEqual(str(error.exception), code)
        self.assertEqual((chain.source.patches(), T.writes(chain.tree)), ([], []))

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
        self.assertEqual((chain.source.patches(), T.writes(chain.tree)), ([], []))

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
    """Whatever goes wrong, nothing reports success and the Hiring Pipeline projection keeps showing what it showed."""

    def broken(self, code, **switch):
        chain = Chain()
        chain.project()
        shown, saves = T.table_state(chain.tree), chain.store.saves
        for name, value in switch.items():
            setattr(chain.source, name, value)
        with self.assertRaises(NotionError) as error:
            chain.confirm()
        self.assertEqual(str(error.exception), code)
        self.assertEqual((T.table_state(chain.tree), chain.store.saves), (shown, saves))           # the UI is not cleared; the region is not even read for a rewrite
        self.assertIn(f"{COMPANY} — {ROLE}", seen(chain.tree))
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
        chain.project()
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
        self.assertNotIn(f"{COMPANY} — {ROLE}", seen(chain.tree))

    def test_a_projection_that_fails_after_a_verified_write_is_reported_and_the_decision_stands(self):
        chain = Chain()
        with mock.patch.object(T.stage, "run", side_effect=RuntimeError("down")):
            done = chain.confirm()
        self.assertEqual((done["status"], done["verified"], done["projection"], done["cleared"]), ("recorded", True, "FAILED", False))
        self.assertEqual(chain.source.decision(), {"name": "Rejected"})

    def test_a_fresh_verified_projection_that_still_lists_the_pursuit_is_recorded_but_never_cleared(self):
        chain = Chain()
        real = C.compile_rows

        def forgetful(applied, *args, **kw):                         # a compiler that does not honour the decision: the table stays fresh and verified, and still lists the pursuit
            return real([dict(a, decision=None) for a in applied], *args, **kw)
        with mock.patch.object(T.stage.C, "compile_rows", forgetful):
            done = chain.confirm()
        self.assertEqual((done["status"], done["verified"]), ("recorded", True))                      # the canonical decision stands
        self.assertEqual((done["projection"]["status"], done["projection"]["verified"], done["projection"]["target_listed"]), ("fresh", True, True))
        self.assertIs(done["cleared"], False)
        self.assertIn(f"{COMPANY} — {ROLE}", seen(chain.tree))

    def test_a_degraded_projection_after_a_verified_write_is_never_reported_as_cleared(self):
        chain = Chain()
        done = chain.confirm(calendar=T.FakeCalendar([], broken=True))                              # Calendar unreadable: the projection is DEGRADED
        self.assertEqual((done["status"], done["verified"], done["projection"]["status"], done["cleared"]), ("recorded", True, "degraded", False))
        self.assertEqual(chain.source.decision(), {"name": "Rejected"})


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

    def test_the_only_writes_are_the_decision_properties_one_record_block_and_one_page_move(self):
        text = (self.ROOT / "lifeos/hiring/decision.py").read_text()
        self.assertEqual(len(re.findall(r"update_page_properties\(", text)), 1)
        self.assertEqual(re.findall(r"update_page_properties\(row\[\"id\"\], \{(\w+): .*?, (\w+): ", text), [("DECISION", "HISTORY")])
        self.assertEqual(re.findall(r"client\.call_once\(\"(\w+)\", f\"(/[a-z]+/\{want\['id'\]\}/[a-z]+)\"", text), [("PATCH", "/blocks/{want['id']}/children"), ("POST", "/pages/{want['id']}/move")])
        for banned in ("tinyfish", "gmail", "outlook", ".create(", "lifeos.jobs", "lifeos.interview", "lifeos.jira", '"DELETE"', '"archived": True', '"in_trash": True', "Applied\": {\"checkbox\": "):
            self.assertNotIn(banned.lower(), text.lower(), banned)
        self.assertNotIn("update_page_properties", (self.ROOT / "lifeos/sources/hiring.py").read_text())

    def test_the_action_is_not_reachable_from_any_stage_workflow_or_schedule(self):
        for path in (self.ROOT / ".github/workflows").glob("*.yml"):
            self.assertNotIn("confirm_rejected", path.read_text(), path.name)
        from lifeos import run as entry
        self.assertFalse([name for name, stage in entry.STAGES.items() if "decision" in str(getattr(stage, "target", ""))])


if __name__ == "__main__":
    unittest.main()
