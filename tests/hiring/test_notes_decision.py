"""LIFE-353 (REQ-5): hiring decisions live on the Job Ledger row only. Confirm Rejected and Reopen are one Ledger write each, read back; no opportunity page is moved and no container is read.
D195 (closing a Notes-only pursuit by moving its page) is superseded: a pursuit with no Ledger row is refused with nothing written.

Everything is invented. The chain is the production one: stage.confirm_rejected -> decision.route -> decision.confirm_rejected -> stage.run -> compiler -> region writer. Only Notion (the page tree and
the Ledger), the snapshot store and the Calendar are stand-ins. The page tree stand-in (`Workspace`) stays: the Close, Manage and Mark Complete screens still locate opportunity pages in it."""
import copy
import json
import unittest
import uuid
from unittest import mock

from lifeos.hiring import decision as D, models as M
from lifeos.jobs import hiring_pipeline
from lifeos.platform.notion_client import NotionError
from tests.hiring import test_decision as TD, test_hiring as T

COMPANY, ROLE, SIBLING_ROLE = "Fabrikam Retail", "Senior Manager, Operations", "Director, Delivery"
OP, OP2 = "op-0123456789", "op-9876543210"
NOW = T.NOW


_OUTCOME_SCAN = mock.patch.object(T.stage.outcome_mail, "load", lambda conn: ({}, (NOW - T.timedelta(hours=1)).isoformat()))     # the outcome-mail scan saved an hour ago: these tests are about the decision, not that stage


def setUpModule():
    _OUTCOME_SCAN.start()


def tearDownModule():
    _OUTCOME_SCAN.stop()


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
        if method == "PATCH" and not path.endswith("/children"):                     # one record line set to its plain label, in place
            if "label" in self.fail:
                raise NotionError("NOTION_HTTP_500")
            kind = self.blocks[bid]["type"]
            self.blocks[bid][kind] = {"rich_text": [{"plain_text": "".join(r["text"]["content"] for r in body[kind]["rich_text"])}]}
            self.edits += 1
            return {}
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


def staged(world, pid, stage="Interviewing", **kw):
    """An interview pursuit's row (LIFE-361): Stage set, Applied not ticked."""
    world.source.pages[pid] = TD.Source.make(pid, COMPANY, ROLE, applied=False, stage=stage, **kw)


def ledger_world(**kw):
    return World(ledger_rows=[(TD.TARGET, COMPANY, ROLE), (TD.SIBLING, COMPANY, SIBLING_ROLE)], **kw)


def req(**changes):
    return {"action": "CONFIRM_REJECTED", "confirmed": True, "id": TD.TARGET, "company": COMPANY, "role": ROLE, "reason": "Employer rejection email confirmed", "op": OP, **changes}


def reopen(**changes):
    return req(action="REOPEN", reason="Rejection was a mistake", op=OP2, **changes)


class RoutingTests(unittest.TestCase):
    WANT = {"company": COMPANY, "role": ROLE, "action": "CONFIRM_REJECTED"}

    def row(self, rid, decision=None, role=ROLE):
        return {"id": rid, "company": COMPANY, "role": role, "decision": decision}

    def refused(self, rows, want, code):
        with self.assertRaises(D.DecisionError) as error:
            D.route(rows, want)
        self.assertEqual(str(error.exception), code)

    def test_the_row_named_by_page_id_is_the_one_and_its_names_must_still_agree(self):
        a, b = self.row("a1" * 16), self.row("b2" * 16)
        self.assertEqual(D.route([a, b], {**self.WANT, "id": "b2" * 16}), b)                       # two rows for one company and role: the page id decides
        self.assertEqual(D.route([a], {"action": "CONFIRM_REJECTED", "id": "a1" * 16}), a)           # an id alone (the Close screen's link) is enough
        self.refused([a], {**self.WANT, "id": "a1" * 16, "role": SIBLING_ROLE}, "DECISION_STALE_IDENTITY")

    def test_an_id_that_is_no_row_resolves_by_company_and_role_and_company_alone_never_does(self):
        tree_page = "c3" * 16                                                                          # an older opportunity page's own id (its Manage link)
        self.assertEqual(D.route([self.row("a1" * 16), self.row("b2" * 16, role=SIBLING_ROLE)], {**self.WANT, "id": tree_page})["id"], "a1" * 16)
        self.refused([self.row("a1" * 16, role=SIBLING_ROLE)], {**self.WANT, "id": tree_page}, "DECISION_NO_MATCH")
        self.refused([self.row("a1" * 16), self.row("b2" * 16)], self.WANT, "DECISION_AMBIGUOUS")
        self.refused([], {"action": "CONFIRM_REJECTED", "id": tree_page}, "DECISION_NO_MATCH")

    def test_open_or_closed_is_the_writers_check_not_the_routers(self):
        for decision in (None, "Rejected", "Hired"):
            self.assertEqual(D.route([self.row("a1" * 16, decision)], {**self.WANT, "action": "REOPEN"})["decision"], decision)


class LedgerDecisionTests(unittest.TestCase):
    def test_gwt_dw_1_confirm_rejected_writes_the_row_once_reads_it_back_and_moves_no_page(self):
        world = ledger_world()
        world.project()
        done = world.decide(req())
        self.assertEqual((done["status"], done["verified"], world.source.decision(), len(world.source.history().splitlines())), ("recorded", True, {"name": "Rejected"}, 1))
        self.assertEqual(world.ws.writes(), [])                                                      # no page moved, no record block appended
        self.assertEqual(world.ws.where(world.ws.target), world.ws.col_active)
        self.assertNotIn(f"{COMPANY} — {ROLE}", world.listed())
        patches = len(world.source.patches())
        self.assertEqual(world.decide(req())["status"], "already_recorded")                          # the same operation id: nothing written twice
        self.assertEqual((len(world.source.patches()), len(world.source.history().splitlines())), (patches, 1))

    def test_an_interview_pursuit_row_with_stage_set_closes_on_its_row_and_keeps_its_stage(self):
        world = ledger_world()
        staged(world, TD.TARGET)
        done = world.decide(req())
        self.assertEqual((done["status"], world.source.decision()), ("recorded", {"name": "Rejected"}))
        self.assertEqual(world.source.pages[TD.TARGET]["properties"]["Stage"], {"select": {"name": "Interviewing"}})
        self.assertEqual(world.source.pages[TD.TARGET]["properties"]["Applied"], {"checkbox": False})

    def test_gwt_dw_2_reopen_clears_the_decision_and_adds_one_reopened_line(self):
        world = ledger_world()
        world.decide(req())
        done = world.decide(reopen())
        self.assertEqual((done["status"], done["verified"], world.source.decision()), ("recorded", True, None))
        self.assertEqual([(e["action"], e["outcome"]) for e in D.entries(world.source.history())], [("CONFIRM_REJECTED", "Rejected"), ("REOPEN", "Rejected")])
        self.assertEqual(world.decide(reopen())["status"], "already_recorded")
        self.assertEqual(len(world.source.history().splitlines()), 2)
        self.assertEqual(world.ws.writes(), [])
        self.assertIn(f"{COMPANY} — {ROLE}", world.listed())                                          # back among the open opportunities

    def test_gwt_dw_3_a_hired_row_is_not_reopened_without_its_own_confirmation(self):
        world = ledger_world()
        options(world.source, NEW)
        world.decide(req(outcome="Hired"))
        patches = len(world.source.patches())
        with self.assertRaises(D.DecisionError) as error:
            world.decide(reopen())
        self.assertEqual((str(error.exception), len(world.source.patches()), world.source.decision()), ("DECISION_HIRED_REOPEN_UNCONFIRMED", patches, {"name": "Hired"}))
        self.assertEqual(world.decide(reopen(reopen_hired=True))["status"], "recorded")
        self.assertIsNone(world.source.decision())

    def test_gwt_dw_4_a_half_applied_operation_runs_only_its_missing_step(self):
        for action in ("CONFIRM_REJECTED", "REOPEN"):
            world = ledger_world()
            if action == "REOPEN":
                world.decide(req())
            want = req() if action == "CONFIRM_REJECTED" else reopen()
            line = json.dumps({"op": want["op"], "action": action, "at": NOW.isoformat(timespec="seconds"), "reason": want["reason"], "outcome": "Rejected"}, separators=(",", ":"), sort_keys=True)
            history = world.source.history()
            world.source.pages[TD.TARGET]["properties"]["Hiring Decision History"] = {"rich_text": [{"plain_text": f"{history}\n{line}" if history else line}]}     # the line landed, the decision did not
            before = world.source.history()
            done = world.decide(want)
            self.assertEqual((done["status"], world.source.history()), ("recorded", before), action)            # no second line
            self.assertEqual(world.source.patches()[-1][2], ("Hiring Decision",), action)                        # only the missing decision was written
            self.assertEqual(world.source.decision(), {"name": "Rejected"} if action == "CONFIRM_REJECTED" else None, action)
            self.assertEqual(world.decide(want)["status"], "already_recorded", action)

    def test_a_line_for_this_operation_followed_by_another_decision_is_never_overwritten(self):
        world = ledger_world()
        world.decide(req())
        world.decide(reopen())
        with self.assertRaises(D.DecisionError) as error:
            world.decide(req())                                                                         # an old confirm replayed after the reopen
        self.assertEqual((str(error.exception), world.source.decision()), ("DECISION_CONFLICT", None))

    def test_a_decision_set_by_hand_is_reopened_with_one_line(self):
        world = ledger_world()
        world.source.pages[TD.TARGET] = TD.Source.make(TD.TARGET, COMPANY, ROLE, decision="Passed")
        self.assertEqual(world.decide(reopen())["status"], "recorded")
        self.assertEqual((world.source.decision(), [(e["action"], e["outcome"]) for e in D.entries(world.source.history())]), (None, [("REOPEN", "Passed")]))

    def test_an_open_row_is_not_reopened_and_a_closed_row_is_not_closed_again(self):
        world = ledger_world()
        with self.assertRaises(D.DecisionError) as error:
            world.decide(reopen())
        self.assertEqual(str(error.exception), "DECISION_NOT_CLOSED")
        world.decide(req())
        with self.assertRaises(D.DecisionError) as error:
            world.decide(req(op="op-5555555555"))
        self.assertEqual(str(error.exception), "DECISION_CONFLICT")

    def test_a_pursuit_with_no_ledger_row_is_refused_and_its_page_never_moves(self):
        world = World()                                                                                 # D195 superseded: the Notes page alone carries no decision
        world.project()
        for want in (req(id=world.ws.target), reopen(id=world.ws.target)):
            with self.assertRaises(D.DecisionError) as error:
                world.decide(want)
            self.assertEqual(str(error.exception), "DECISION_NO_MATCH")
        self.assertEqual((world.ws.writes(), world.source.patches()), ([], []))
        self.assertIn(f"{COMPANY} — {ROLE}", world.listed())

    def test_the_decision_needs_no_pipeline_page_or_page_tree(self):
        world = ledger_world()
        world.env = {k: v for k, v in world.env.items() if k != "HIRING_PIPELINE_PAGE_ID"}
        with mock.patch.object(hiring_pipeline, "read", side_effect=AssertionError("the page tree was read for a decision")):
            with mock.patch.object(T.snapshot, "STORE", world.store), mock.patch.object(T.stage, "run", return_value={}):
                done = T.stage.confirm_rejected(req(), True, world.env, ledger_client=world.source, now=NOW, pipeline_client=world.ws)
        self.assertEqual((done["status"], world.source.decision()), ("recorded", {"name": "Rejected"}))


class BoundaryTests(unittest.TestCase):
    def test_the_decision_is_not_reachable_from_any_workflow_stage_or_schedule(self):
        for path in (T.ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text()
            for name in ("move_page", "confirm_rejected", "decision.decide"):
                self.assertNotIn(name, text, path.name)

    def test_the_page_move_writer_is_gone(self):
        for name in ("move_page", "listing", "unapplied_matches", "Unapplied", "MOVE_VERSION", "ACTIVE_TITLE", "RETIRED_TITLE", "applied_pages"):
            self.assertFalse(hasattr(D, name), name)


# LIFE-47 S1: six distinct closure outcomes on BOTH paths, guarded Reopen. Invented records only.
NEW = [o for o in M.OUTCOMES if o != "Rejected"]


def options(source, names):
    orig = source.call

    def call(method, path, body=None):
        out = orig(method, path, body)
        if "Hiring Decision" in (out.get("properties") or {}):
            out["properties"]["Hiring Decision"]["select"]["options"] += [{"name": n} for n in names]
        return out
    source.call = call


class LedgerOutcomes(unittest.TestCase):
    def test_each_outcome_is_its_own_value_idempotent_and_reopens_with_history_kept(self):
        for outcome in M.OUTCOMES:
            chain = TD.Chain()
            options(chain.source, NEW)
            done = chain.confirm(TD.request(outcome=outcome))
            self.assertEqual((done["status"], done["verified"], chain.source.decision()), ("recorded", True, {"name": outcome}), outcome)
            self.assertEqual(chain.confirm(TD.request(outcome=outcome))["status"], "already_recorded")
            with self.assertRaises(D.DecisionError):                                       # a different operation never replaces the decision
                chain.confirm(TD.request(outcome="Other", op="op-9876543210"))
            reopen = TD.request(action="REOPEN", op="op-5555555555", reason="Closed in error")
            if outcome == "Hired":
                with self.assertRaises(D.DecisionError) as error:
                    chain.confirm(reopen)
                self.assertEqual((str(error.exception), len(chain.source.patches())), ("DECISION_HIRED_REOPEN_UNCONFIRMED", 1))
                reopen["reopen_hired"] = True
            self.assertEqual(chain.confirm(reopen)["status"], "recorded")
            self.assertEqual(chain.source.decision(), None)
            self.assertEqual([e["action"] for e in D.entries(chain.source.history())], ["CONFIRM_REJECTED", "REOPEN"])
            self.assertEqual(chain.confirm(reopen)["status"], "already_recorded")
            self.assertEqual(chain.confirm(TD.request(outcome="Hired", op="op-7777777777"))["status"], "recorded")   # and it can be closed again

    def test_an_outcome_without_its_select_option_fails_closed(self):
        chain = TD.Chain()
        with self.assertRaises(D.DecisionError) as error:
            chain.confirm(TD.request(outcome="Hired"))
        self.assertEqual((str(error.exception), chain.source.patches()), ("DECISION_SCHEMA_MISSING", []))

    def test_a_stored_outcome_that_differs_from_the_one_written_is_a_failed_read_back(self):
        chain = TD.Chain()
        options(chain.source, NEW)
        chain.source.tamper = lambda page: page["properties"].update({"Hiring Decision": {"select": {"name": "Other"}}})
        with self.assertRaises(D.DecisionError) as error:
            chain.confirm(TD.request(outcome="Hired"))
        self.assertEqual(str(error.exception), "DECISION_READBACK_MISMATCH")


if __name__ == "__main__":
    unittest.main()
