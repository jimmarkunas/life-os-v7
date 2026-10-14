"""D195: a Notes-only pursuit (no Applied Ledger row) is closed, and reopened, through its one Notion opportunity page: moved into the existing Retired Opportunities container with one appended record block.

Everything is invented. The chain is the production one against an in-memory Notion page hierarchy: stage.confirm_rejected -> decision.route -> the real page reader (jobs.hiring_pipeline) -> decision.move_page ->
stage.run -> compiler -> region writer. Only Notion (the page tree and the Ledger), the snapshot store and the Calendar are stand-ins; nothing here is live proof of the Notion move itself."""
import copy
import unittest
import urllib.request
import uuid
from unittest import mock

from lifeos.hiring import decision as D
from lifeos.jobs import hiring_pipeline
from lifeos.platform import notion_client
from lifeos.platform.notion_client import NotionError
from tests.hiring import test_decision as TD, test_hiring as T

COMPANY, ROLE, SIBLING_ROLE = "Fabrikam Retail", "Senior Manager, Operations", "Director, Delivery"
OP, OP2 = "op-0123456789", "op-9876543210"
NOW = T.NOW


def nid(n):
    return str(uuid.UUID(int=n))


class Workspace:
    """An in-memory Notion page hierarchy with just the calls V7 makes: list children, fetch a page, append blocks, move a page. Every switch breaks one step."""

    def __init__(self, retired=1, active=1):
        self.blocks, self.kids, self.parent_of, self.times, self.log, self.versions = {}, {}, {}, {}, [], []
        self.n, self.edits, self.gets = 100, 0, 0
        self.fail, self.drop_move, self.lose_move, self.tamper, self.on_get, self.paged_forever = set(), False, False, None, {}, False
        self.root = nid(1)
        self.put(self.root, "child_page", None, "Hiring Pipeline")
        self.add("callout", self.root, "Context and interview notes only.")
        cols = self.add("column_list", self.root)
        self.col_active, self.col_retired = self.add("column", cols), self.add("column", cols)
        self.add("heading_2", self.col_active, "Active Opportunities")
        self.add("heading_2", self.col_retired, "Retired Opportunities")
        self.active = [self.add("child_page", self.root, "Active Opportunities") for _ in range(active)]           # the existing machine container
        self.retired = [self.add("child_page", self.root, "Retired Opportunities") for _ in range(retired)]        # the container Jim would create
        self.target = self.opportunity(f"{COMPANY} — {ROLE}", "Pat Example")
        self.sibling = self.opportunity(f"{COMPANY} — {SIBLING_ROLE}", "Sam Example")
        self.other = self.opportunity("Contoso Freight — Program Manager", "Lee Example")

    def canon(self, bid):
        """V7 sends ids without dashes (decision._page_id); Notion accepts either form."""
        return next(k for k in self.blocks if k.replace("-", "") == bid.replace("-", ""))

    def put(self, bid, kind, parent, text=""):
        inner = {"title": text} if kind == "child_page" else {"rich_text": [{"plain_text": text}]} if text else {}
        self.blocks[bid] = {"id": bid, "type": kind, "has_children": False, kind: inner}
        self.kids[bid], self.parent_of[bid], self.times[bid] = [], parent, "2026-10-05T00:00:00.000Z"
        if parent:
            self.kids[parent].append(bid)
            self.blocks[parent]["has_children"] = True
        return bid

    def add(self, kind, parent, text=""):
        self.n += 1
        return self.put(nid(self.n), kind, parent, text)

    def opportunity(self, title, interviewer):
        page = self.add("child_page", self.col_active, title)
        self.add("callout", page, f"Human note about {interviewer}: protected, never rewritten.")
        self.add("paragraph", page, "Live notes written by a person.")
        round_ = self.add("child_page", page, f"Interview 1: {interviewer}")
        self.add("paragraph", round_, "Transcript text.")
        return page

    def digest(self, bid):
        return [(k, self.blocks[k]["type"], (self.blocks[k].get(self.blocks[k]["type"]) or {}).get("title") if self.blocks[k]["type"] == "child_page" else "".join(r["plain_text"] for r in (self.blocks[k].get(self.blocks[k]["type"]) or {}).get("rich_text", []))) for k in self.kids[bid]]

    def parent_page(self, bid):
        parent = self.parent_of[bid]
        return {"type": "page_id", "page_id": parent} if self.blocks[parent]["type"] == "child_page" else {"type": "block_id", "block_id": parent}

    def call(self, method, path, body=None):
        assert method == "GET", (method, path)
        if path.startswith("/pages/"):
            bid = self.canon(path.split("/")[2])
            self.gets += 1
            self.log.append(("GET", "page"))
            if self.on_get.get(self.gets):
                self.on_get[self.gets](self)
            return {"id": bid, "archived": False, "in_trash": False, "last_edited_time": self.times[bid], "parent": self.parent_page(bid),
                    "properties": {"title": {"type": "title", "title": [{"plain_text": self.blocks[bid]["child_page"]["title"]}]}}}
        bid = self.canon(path.split("/")[2].split("?")[0])
        self.log.append(("GET", "children"))
        if self.paged_forever:
            return {"results": [], "has_more": True, "next_cursor": None}
        return {"results": [copy.deepcopy(self.blocks[k]) for k in self.kids[bid]], "has_more": False}

    def call_once(self, method, path, body=None, version=None):
        bid = self.canon(path.split("/")[2])
        self.log.append((method, path.split("/")[1], bid))
        if method == "PATCH":
            if "append" in self.fail:
                raise NotionError("NOTION_HTTP_500")
            for child in body["children"]:
                new = self.add(child["type"], bid, "".join(r["text"]["content"] for r in child[child["type"]]["rich_text"]))
                self.edits += 1
                self.times[bid] = f"2026-10-06T00:00:{self.edits:02d}.000Z"
            return {}
        assert method == "POST" and path.endswith("/move") and body["parent"]["type"] == "page_id"
        self.versions.append(version)
        if "move" in self.fail:
            raise NotionError("NOTION_HTTP_500")
        if not self.drop_move:
            dest = self.canon(body["parent"]["page_id"])
            assert self.blocks[dest]["type"] == "child_page"                              # the Move page API takes a page as the new parent, never a column or other block
            self.kids[self.parent_of[bid]].remove(bid)
            self.kids[dest].append(bid)
            self.parent_of[bid] = dest
            self.edits += 1
            self.times[bid] = f"2026-10-06T00:00:{self.edits:02d}.000Z"
            if callable(self.tamper):
                self.tamper(self)
        if self.lose_move:
            raise NotionError("NOTION_NETWORK")
        return {}

    def relocate(self, bid, new_parent):
        """A person drags the page somewhere else (a column, a container, or out of the pipeline) between two V7 reads. The edit time is left alone, as Notion would for a parent a reader sees only by walking."""
        self.kids[self.parent_of[bid]].remove(bid)
        self.kids[new_parent].append(bid)
        self.parent_of[bid] = new_parent

    def writes(self):
        return [e for e in self.log if e[0] in ("PATCH", "POST")]

    def where(self, bid):
        return self.parent_of[bid]


class World:
    """The whole path for one fictional workspace: Ledger stand-in, page tree, Daily Report page, projection row."""

    def __init__(self, ledger_rows=(), **kw):
        self.ws, self.source, self.tree, self.store = Workspace(**kw), TD.Source(rows=list(ledger_rows)), T.page(extra_rows=0), T.FakeStore()
        self.env = {**T.RUN_ENV, "HIRING_PIPELINE_PAGE_ID": self.ws.root}

    def project(self):
        with mock.patch.object(T.snapshot, "STORE", self.store):
            return T.stage.run(0, True, self.env, ledger_client=self.source, pipeline_client=self.ws, calendar=T.FakeCalendar([]), connection=object(), client=self.tree, now=NOW)

    def decide(self, req, live=True):
        with mock.patch.object(T.snapshot, "STORE", self.store):
            return T.stage.confirm_rejected(req, live, self.env, ledger_client=self.source, now=NOW, pipeline_client=self.ws, calendar=T.FakeCalendar([]), connection=object(), client=self.tree)

    def request(self, **changes):
        return {"action": "CONFIRM_REJECTED", "confirmed": True, "id": self.ws.target, "company": COMPANY, "role": ROLE, "reason": "Employer rejection email confirmed", "op": OP, **changes}

    def reopen(self, **changes):
        return self.request(action="REOPEN", reason="Rejection was a mistake", op=OP2, **changes)

    def listed(self):
        return sorted(TD.seen(self.tree))

    def sections(self):
        return {o.page_id: (o.section, o.rounds) for o in hiring_pipeline.read(self.ws, self.ws.root)}


class RoutingTests(unittest.TestCase):
    WANT = {"company": COMPANY, "role": ROLE, "action": "CONFIRM_REJECTED"}

    def parent(self, pid, retired=False, role=ROLE):
        return {"id": pid, "company": COMPANY, "role": role, "rounds": 1, "retired": retired}

    def row(self, rid, decision=None, role=ROLE):
        return {"id": rid, "company": COMPANY, "role": role, "decision": decision}

    def test_one_ledger_application_or_one_active_notes_page_is_routed_and_company_alone_never_is(self):
        self.assertEqual(D.route([self.row("l1")], [], self.WANT)[0], "ledger")
        self.assertEqual(D.route([], [self.parent("p1")], self.WANT)[0], "notes")
        for applied, parents, code in (([], [], "DECISION_NO_MATCH"), ([self.row("l1", role="Another Role")], [self.parent("p1", role="Another Role")], "DECISION_NO_MATCH")):
            with self.assertRaises(D.DecisionError) as error:
                D.route(applied, parents, self.WANT)
            self.assertEqual(str(error.exception), code)

    def test_a_ledger_application_and_a_notes_page_are_one_pursuit_decided_on_the_ledger_only(self):
        self.assertEqual(D.route([self.row("l1")], [self.parent("p1")], self.WANT), ("ledger", self.row("l1")))

    def test_duplicates_or_a_page_that_is_both_active_and_retired_are_never_guessed(self):
        for applied, parents in (([self.row("l1"), self.row("l2")], []), ([], [self.parent("p1"), self.parent("p2")]), ([], [self.parent("p1"), self.parent("p2", retired=True)]),
                                 ([self.row("l1")], [self.parent("p1"), self.parent("p2")]), ([], [self.parent("p1", retired=True), self.parent("p2", retired=True)])):
            with self.assertRaises(D.DecisionError) as error:
                D.route(applied, parents, self.WANT)
            self.assertEqual(str(error.exception), "DECISION_AMBIGUOUS")

    def test_reopen_acts_on_a_retired_notes_page_and_never_on_a_pursuit_the_ledger_closed(self):
        want = {**self.WANT, "action": "REOPEN"}
        self.assertEqual(D.route([], [self.parent("p1", retired=True)], want)[0], "notes")
        self.assertEqual(D.route([], [self.parent("p1")], want)[0], "notes")                                 # an open page is routed too: move_page refuses it as NOT_CLOSED, or recognises a finished retry
        for applied, parents, code in (([self.row("l1", "Rejected")], [self.parent("p1", retired=True)], "DECISION_LEDGER_CLOSED"), ([self.row("l1", "Passed")], [], "DECISION_LEDGER_CLOSED"),
                                       ([self.row("l1")], [], "DECISION_NOT_CLOSED")):
            with self.assertRaises(D.DecisionError) as error:
                D.route(applied, parents, want)
            self.assertEqual(str(error.exception), code)


class NotesOnlyCloseTests(unittest.TestCase):
    def test_a_notes_only_pursuit_is_closed_through_its_page_and_leaves_the_queue_after_read_back(self):
        world = World()
        world.project()
        ws, before = world.ws, None
        self.assertEqual(len(world.listed()), 3)                                                     # Notes-only: no Applied Ledger row anywhere
        before = {k: ws.digest(k) for k in (ws.target, ws.sibling, ws.other)}
        interview = ws.kids[ws.target][-1]
        round_before = ws.digest(interview)
        done = world.decide(world.request())
        self.assertEqual((done["status"], done["verified"], done["route"], done["cleared"]), ("recorded", True, "notes", True))
        self.assertEqual(done["projection"], {"status": "fresh", "rows": 2, "closed": 0, "verified": True, "target_listed": False})
        self.assertNotIn(f"{COMPANY} — {ROLE}", world.listed())
        self.assertEqual(sorted(world.listed()), sorted([f"{COMPANY} — {SIBLING_ROLE}", "Contoso Freight — Program Manager"]))     # the same company's other role is untouched
        self.assertEqual(ws.where(ws.target), ws.retired[0])
        kids = ws.digest(ws.target)
        self.assertEqual(kids[:-2], before[ws.target])                                                # the page's top-level blocks, as before, in order (ids, types, plain text; nested content is not compared here)
        tail = f"{NOW.date().isoformat()} · Employer rejection email confirmed · op {OP}"
        self.assertEqual([k[1:] for k in kids[-2:]], [("callout", f"Hiring decision (V7) · Pending Rejected · {tail}"), ("callout", f"Hiring decision (V7) · Rejected · {tail}")])
        self.assertEqual(ws.digest(interview), round_before)                                          # the fake keeps the interview page's own blocks as they were (a property of the stand-in, not a V7 check)
        for page in (ws.sibling, ws.other):
            self.assertEqual((ws.digest(page), ws.where(page)), (before[page], ws.col_active))
        self.assertEqual([e[:2] + (e[2],) for e in ws.writes()], [("PATCH", "blocks", ws.target), ("POST", "pages", ws.target), ("PATCH", "blocks", ws.target)])     # intent record, the move, then the completion record
        self.assertEqual(ws.versions, [D.MOVE_VERSION])
        sections = world.sections()
        self.assertEqual(sections[ws.target], (hiring_pipeline.RETIRED, 1))                            # the existing reader sees it as Retired, interview round counted
        self.assertEqual(sections[ws.sibling], (hiring_pipeline.ACTIVE, 1))
        self.assertEqual(world.source.patches(), [])                                                  # the Ledger is not touched, and Applied is never created

    def test_retrying_the_same_operation_changes_nothing_more(self):
        world = World()
        world.decide(world.request())
        writes = len(world.ws.writes())
        again = world.decide(world.request())
        self.assertEqual((again["status"], again["verified"], again["cleared"]), ("already_recorded", True, True))
        self.assertEqual(len(world.ws.writes()), writes)
        self.assertEqual(sum(1 for _, _, text in world.ws.digest(world.ws.target) if text.startswith("Hiring decision (V7)")), 2)           # Pending + complete, once

    def test_a_dry_run_validates_and_writes_nothing(self):
        world = World()
        done = world.decide(world.request(), live=False)
        self.assertEqual((done["status"], done["verified"], done["cleared"], world.ws.writes(), T.writes(world.tree)), ("dry_run", False, False, [], []))

    def test_the_exact_page_is_selected_and_company_alone_never_selects_one(self):
        world = World()
        for req, code in ((world.request(id=world.ws.sibling), "DECISION_STALE_IDENTITY"), (world.request(role="Vice President, Something Else"), "DECISION_NO_MATCH"),
                          (world.request(id=nid(999)), "DECISION_STALE_IDENTITY")):
            with self.assertRaises(NotionError) as error:
                world.decide(req)
            self.assertEqual(str(error.exception), code)
        self.assertEqual((world.ws.writes(), T.writes(world.tree)), ([], []))

    def test_a_page_renamed_or_changed_after_it_was_routed_is_refused_on_the_exact_fetch(self):
        for call in (1, 2):                                                                           # the first fetch, and the one just before the write
            world = World()
            world.ws.on_get = {call: lambda ws: ws.blocks[ws.target]["child_page"].update(title=f"{COMPANY} — Vice President, Something Else")}
            with self.assertRaises(NotionError) as error:
                world.decide(world.request())
            self.assertEqual((str(error.exception), world.ws.writes()), ("DECISION_STALE_IDENTITY", []))

    def test_a_duplicate_opportunity_refuses_mutation(self):
        world = World()
        twin = world.ws.add("child_page", world.ws.active[0], f"{COMPANY} — {ROLE}")                   # a second Active page with the same company and role, in the Active container
        world.ws.add("child_page", twin, "Interview 1: Someone")
        with self.assertRaises(NotionError) as error:
            world.decide(world.request())
        self.assertEqual(str(error.exception), "DECISION_AMBIGUOUS")
        self.assertEqual(world.ws.writes(), [])

    def test_a_pursuit_with_both_a_ledger_application_and_a_notes_page_is_decided_once_on_the_ledger(self):
        world = World(ledger_rows=[(TD.TARGET, COMPANY, ROLE)])
        before = {k: copy.deepcopy(world.ws.digest(k)) for k in (world.ws.target, world.ws.sibling)}
        done = world.decide(world.request(id=TD.TARGET))
        self.assertEqual((done["status"], done["cleared"], "route" in done), ("recorded", True, False))      # the D194 write
        self.assertEqual(world.source.decision(), {"name": "Rejected"})
        self.assertEqual(world.ws.writes(), [])                                                       # the Notes page is never moved, and the Ledger decision closes the pursuit
        self.assertEqual({k: world.ws.digest(k) for k in before}, before)
        with self.assertRaises(NotionError) as error:                                                 # one pursuit, one decision: the Notes page id is not a second target
            world.decide(world.request(op=OP2, id=world.ws.target))
        self.assertEqual(str(error.exception), "DECISION_STALE_IDENTITY")


class NotesOnlyFailureTests(unittest.TestCase):
    """Nothing reports cleared unless the exact page moved, kept every block and the projection dropped it."""

    def broken(self, code, setup):
        world = World()
        world.project()
        shown, saves = T.table_state(world.tree), world.store.saves
        setup(world.ws)
        with self.assertRaises(NotionError) as error:
            world.decide(world.request())
        self.assertEqual(str(error.exception), code)
        self.assertEqual((T.table_state(world.tree), world.store.saves), (shown, saves))               # the UI is not cleared and the region was not rewritten
        self.assertIn(f"{COMPANY} — {ROLE}", world.listed())
        return world

    def test_a_refused_record_write_is_a_failure_and_nothing_moved(self):
        world = self.broken("DECISION_WRITE_FAILED", lambda ws: ws.fail.add("append"))
        self.assertEqual((world.ws.where(world.ws.target), world.ws.writes()), (world.ws.col_active, [("PATCH", "blocks", world.ws.target)]))

    def records(self, ws):
        return [text for _, _, text in ws.digest(ws.target) if text.startswith("Hiring decision (V7)")]

    def test_a_refused_move_leaves_only_a_pending_record_and_the_same_operation_finishes_it_without_a_second_one(self):
        world = self.broken("DECISION_WRITE_FAILED", lambda ws: ws.fail.add("move"))
        ws = world.ws
        self.assertEqual([r.split(" · ")[1] for r in self.records(ws)], ["Pending Rejected"])          # partially applied, and nowhere reading as a completed rejection
        ws.fail.clear()
        done = world.decide(world.request())
        self.assertEqual((done["status"], done["cleared"], ws.where(ws.target)), ("recorded", True, ws.retired[0]))
        self.assertEqual([r.split(" · ")[1] for r in self.records(ws)], ["Pending Rejected", "Rejected"])
        self.assertEqual([e[0] for e in ws.writes()].count("PATCH"), 2)                                # one intent, one completion; never a second intent

    def test_a_different_operation_cannot_act_while_an_earlier_one_is_half_applied(self):
        world = self.broken("DECISION_WRITE_FAILED", lambda ws: ws.fail.add("move"))
        ws = world.ws
        ws.fail.clear()
        writes, records = list(ws.writes()), self.records(ws)
        for other in (world.request(op=OP2), world.request(op=OP2, reason="A different reason")):
            with self.assertRaises(NotionError) as error:
                world.decide(other)
            self.assertEqual(str(error.exception), "DECISION_PENDING")
        self.assertEqual((ws.writes(), self.records(ws), ws.where(ws.target)), (writes, records, ws.col_active))      # zero additional records, zero moves

    def test_a_move_that_landed_but_whose_response_was_lost_is_an_error_then_finished_by_the_same_operation(self):
        world = self.broken("DECISION_WRITE_FAILED", lambda ws: setattr(ws, "lose_move", True))
        ws = world.ws
        self.assertEqual(ws.where(ws.target), ws.retired[0])                                          # it did land, and is still only Pending
        self.assertEqual([r.split(" · ")[1] for r in self.records(ws)], ["Pending Rejected"])
        with self.assertRaises(NotionError) as error:                                                 # even landed, another operation waits for the first to be reconciled
            world.decide(world.request(op=OP2))
        self.assertEqual(str(error.exception), "DECISION_PENDING")
        ws.lose_move = False
        done = world.decide(world.request())
        self.assertEqual((done["status"], done["verified"], done["cleared"]), ("recorded", True, True))
        self.assertEqual([r.split(" · ")[1] for r in self.records(ws)], ["Pending Rejected", "Rejected"])
        self.assertEqual(world.decide(world.request())["status"], "already_recorded")
        self.assertNotIn(f"{COMPANY} — {ROLE}", world.listed())

    def test_an_acknowledged_move_that_did_not_happen_is_a_failed_read_back_with_no_completion_record(self):
        world = self.broken("DECISION_READBACK_MISMATCH", lambda ws: setattr(ws, "drop_move", True))
        self.assertEqual([r.split(" · ")[1] for r in self.records(world.ws)], ["Pending Rejected"])    # never written as complete

    def test_a_move_that_lost_a_block_is_a_failed_read_back(self):
        def lose(ws):
            ws.kids[ws.target].remove(ws.kids[ws.target][2])                                          # an interview child page vanished
        self.broken("DECISION_READBACK_MISMATCH", lambda ws: setattr(ws, "tamper", lose))

    def test_an_incomplete_page_listing_fails_closed(self):
        world = World()
        world.ws.paged_forever = True
        with self.assertRaises(NotionError):
            world.decide(world.request())
        self.assertEqual((world.ws.writes(), T.writes(world.tree)), ([], []))

    def test_a_missing_or_duplicated_retired_container_is_the_blocker_and_nothing_is_written(self):
        for kw, code in (({"retired": 0}, "DECISION_CONTAINER_MISSING"), ({"retired": 2}, "DECISION_CONTAINER_AMBIGUOUS")):
            world = World(**kw)
            with self.assertRaises(NotionError) as error:
                world.decide(world.request())
            self.assertEqual((str(error.exception), world.ws.writes()), (code, []))

    def test_a_page_changed_by_someone_else_between_the_read_and_the_write_is_not_written(self):
        def edit(ws):
            ws.times[ws.target] = "2026-10-06T01:00:00.000Z"
        world = World()
        world.ws.on_get = {2: edit}                                                                    # the second fetch of the page, just before the write
        with self.assertRaises(NotionError) as error:
            world.decide(world.request())
        self.assertEqual((str(error.exception), world.ws.writes()), ("DECISION_CONCURRENT_CHANGE", []))

    def test_a_degraded_projection_after_a_verified_move_is_never_reported_as_cleared(self):
        world = World()
        with mock.patch.object(T.snapshot, "STORE", world.store):
            done = T.stage.confirm_rejected(world.request(), True, world.env, ledger_client=world.source, now=NOW, pipeline_client=world.ws,
                                            calendar=T.FakeCalendar([], broken=True), connection=object(), client=world.tree)
        self.assertEqual((done["status"], done["verified"], done["projection"]["status"], done["cleared"]), ("recorded", True, "degraded", False))


class SourceLocationTests(unittest.TestCase):
    """The page must still be in the section it was routed from, whether a column (block_id parent) or a container (page_id parent), on the first fetch and again just before the write."""

    def refused(self, world, code="DECISION_STALE_SOURCE", req=None):
        with self.assertRaises(NotionError) as error:
            world.decide(req or world.request())
        self.assertEqual((str(error.exception), world.ws.writes(), T.writes(world.tree)), (code, [], []))

    def test_a_page_moved_to_the_retired_column_or_out_of_the_pipeline_after_routing_is_refused_with_zero_writes(self):
        for where, call, code in (("retired_column", 1, "DECISION_STALE_SOURCE"), ("retired_column", 2, "DECISION_STALE_SOURCE"),            # a block_id parent that stays a block_id parent: only the section check can see it
                                  ("unrelated_page", 1, "DECISION_STALE_SOURCE"), ("unrelated_page", 2, "DECISION_CONCURRENT_CHANGE")):   # a new page_id parent just before the write: the parent comparison sees it first
            world = World()
            dest = world.ws.col_retired if where == "retired_column" else world.ws.add("child_page", None, "Some other page")
            world.ws.on_get = {call: lambda ws, d=dest: ws.relocate(ws.target, d)}
            self.refused(world, code)

    def test_a_page_that_is_out_of_its_section_only_at_the_first_fetch_is_refused_by_that_check_alone(self):
        world = World()
        world.ws.on_get = {1: lambda ws: ws.relocate(ws.target, ws.col_retired), 2: lambda ws: ws.relocate(ws.target, ws.col_active)}         # a transient move: the re-read before the write looks right again
        self.refused(world)

    def test_a_reopen_of_a_page_that_is_no_longer_in_the_retired_container_after_routing_is_refused_with_zero_writes(self):
        world = World()
        world.decide(world.request())
        world.ws.log.clear()
        world.tree.log.clear()
        world.ws.on_get = {world.ws.gets + 1: lambda ws: ws.relocate(ws.target, ws.col_active)}
        self.refused(world, "DECISION_NOT_CLOSED", req=world.reopen())

    def test_a_page_in_the_active_container_is_closed_and_reopened_there(self):
        world = World()
        ws = world.ws
        ws.relocate(ws.target, ws.active[0])                                                             # a page_id parent, as the machine-created opportunities have
        self.assertEqual(world.sections()[ws.target], (hiring_pipeline.ACTIVE, 1))
        self.assertEqual(world.decide(world.request())["cleared"], True)
        self.assertEqual(ws.where(ws.target), ws.retired[0])
        self.assertEqual(world.decide(world.reopen())["status"], "recorded")
        self.assertEqual(ws.where(ws.target), ws.active[0])

    def test_a_column_page_is_closed_from_its_block_parent(self):
        world = World()
        self.assertEqual(world.ws.parent_page(world.ws.target)["type"], "block_id")
        self.assertEqual(world.decide(world.request())["cleared"], True)


class OwnershipAndIdentityTests(unittest.TestCase):
    def test_placement_is_the_only_status_and_a_record_on_a_page_never_closes_or_reopens_it(self):
        world = World()
        ws = world.ws
        for text in (f"Hiring decision (V7) · Rejected · 2026-10-01 · old · op {OP}", f"Hiring decision (V7) · Pending Rejected · 2026-10-01 · old · op {OP2}"):
            ws.add("callout", ws.target, text)                                                           # records on an Active page: annotations only
        world.project()
        self.assertIn(f"{COMPANY} — {ROLE}", world.listed())
        self.assertEqual(world.sections()[ws.target][0], hiring_pipeline.ACTIVE)
        ws.relocate(ws.sibling, ws.retired[0])                                                           # a page retired by hand, with no record at all, is closed (D189)
        world.project()
        self.assertNotIn(f"{COMPANY} — {SIBLING_ROLE}", world.listed())

    def test_an_unapplied_ledger_row_for_the_same_company_and_role_is_not_an_application_and_is_never_written(self):
        world = World(ledger_rows=[(TD.TARGET, COMPANY, ROLE)])
        world.source.pages[TD.TARGET]["properties"]["Applied"] = {"checkbox": False}                    # a posting the Jobs engine surfaced, never applied for through the Ledger
        before = copy.deepcopy(world.source.pages)
        done = world.decide(world.request())
        self.assertEqual((done["status"], done["route"], done["cleared"]), ("recorded", "notes", True))     # the Applied read shows no eligible application, so the Notes page is the pursuit
        self.assertEqual((world.source.pages, world.source.patches()), (before, []))                      # the unapplied row: untouched, still unapplied, no decision
        with self.assertRaises(NotionError) as error:                                                    # and it is never a target
            world.decide(world.request(op=OP2, id=TD.TARGET))
        self.assertEqual(str(error.exception), "DECISION_STALE_IDENTITY")


class ReopenTests(unittest.TestCase):
    def test_reopen_returns_the_pursuit_to_the_active_container_and_keeps_every_record(self):
        world = World()
        world.decide(world.request())
        ws = world.ws
        closed_kids = ws.digest(ws.target)
        done = world.decide(world.reopen())
        self.assertEqual((done["status"], done["verified"], done["cleared"]), ("recorded", True, False))      # reopened: the pursuit is listed again, so nothing is "cleared"
        self.assertEqual(ws.where(ws.target), ws.active[0])
        kids = ws.digest(ws.target)
        self.assertEqual(kids[:-2], closed_kids)                                                      # the earlier records and the page's other top-level blocks are as they were
        tail = f"{NOW.date().isoformat()} · Rejection was a mistake · op {OP2}"
        self.assertEqual([k[2] for k in kids[-2:]], [f"Hiring decision (V7) · Pending Reopened · {tail}", f"Hiring decision (V7) · Reopened · {tail}"])
        self.assertIn(f"{COMPANY} — {ROLE}", world.listed())
        self.assertEqual(world.sections()[ws.target], (hiring_pipeline.ACTIVE, 1))
        self.assertEqual(done["projection"]["target_listed"], True)

    def test_a_reopen_retry_adds_nothing_and_a_reopen_of_an_open_pursuit_is_refused(self):
        world = World()
        with self.assertRaises(NotionError) as error:
            world.decide(world.reopen())
        self.assertEqual((str(error.exception), world.ws.writes()), ("DECISION_NOT_CLOSED", []))
        world.decide(world.request())
        world.decide(world.reopen())
        writes = len(world.ws.writes())
        self.assertEqual(world.decide(world.reopen())["status"], "already_recorded")
        self.assertEqual(len(world.ws.writes()), writes)

    def test_reopening_cannot_resurrect_an_application_the_ledger_closed(self):
        world = World(ledger_rows=[(TD.TARGET, COMPANY, ROLE)])
        world.source.pages[TD.TARGET] = TD.Source.make(TD.TARGET, COMPANY, ROLE, decision="Rejected")
        ws = world.ws
        ws.kids[ws.col_active].remove(ws.target)                                                        # a legacy retirement of the Notes page by hand
        ws.kids[ws.retired[0]].append(ws.target)
        ws.parent_of[ws.target] = ws.retired[0]
        before = ws.digest(ws.target)
        with self.assertRaises(NotionError) as error:
            world.decide(world.reopen())
        self.assertEqual((str(error.exception), ws.writes(), ws.digest(ws.target)), ("DECISION_LEDGER_CLOSED", [], before))
        self.assertEqual(world.source.decision(), {"name": "Rejected"})


class BoundaryTests(unittest.TestCase):
    def test_the_move_uses_its_own_api_version_and_every_other_call_keeps_the_pinned_one(self):
        seen = []

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"
        client = notion_client.Client({"NOTION_API_TOKEN": "t", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "00000000000000000000000000000abc"}, clock=lambda: 0.0, sleep=lambda s: None)
        with mock.patch.object(urllib.request, "urlopen", lambda request, timeout=0: seen.append(request.get_header("Notion-version")) or Reply()):
            client.call("GET", "/pages/x")
            client.call_once("POST", "/pages/x/move", {"parent": {}}, version=D.MOVE_VERSION)
            client.call_once("PATCH", "/blocks/x/children", {})
        self.assertEqual(seen, [notion_client.VERSION, D.MOVE_VERSION, notion_client.VERSION])

    def test_the_move_is_not_reachable_from_any_workflow_stage_or_schedule(self):
        for path in (T.ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text()
            for name in ("move_page", "confirm_rejected", "decision.decide"):
                self.assertNotIn(name, text, path.name)


if __name__ == "__main__":
    unittest.main()
