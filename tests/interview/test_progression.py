"""LIFE-42: progression is fail-closed, and a replayed real-shaped invite writes nothing through the existing writer."""
import json
import os
import unittest
from unittest.mock import patch
from lifeos.interview import create, notion, stage
from lifeos.interview.models import RoundQuery
from lifeos.interview.progression import Invite, ledger_link, progress
from lifeos.run import STAGES
from tests.interview.test_create import WritableFake
from tests.interview.test_notion import ENV, block, page

# Shape of the production agency-invite case: agency invite, no requisition, no Ledger row, one existing machine round.
REAL_SHAPE = Invite("Example Rides", "Technical Project Manager", "2026-09-28", ordinal=1)


def real_shape_client():
    client = WritableFake()
    client.blocks["left"].append({"id": "opp", "type": "child_page", "child_page": {"title": "Example Rides — Technical Project Manager"}})
    client.pages["opp"] = page("Example Rides — Technical Project Manager")
    client.pages["opp"]["parent"] = {"type": "block_id", "block_id": "left"}
    client.blocks["opp"] = [block(notion.MARKERS["opportunity"], id="m"), block("Live Notes", "heading_2", "live"), block("human live notes", id="ln"),
                             block("Raw Notes", "heading_2", "raw"), block("human raw notes", id="rn"), block("Interview Rounds", "heading_2", "ir"),
                             {"id": "r1", "type": "child_page", "child_page": {"title": "Interview 1"}}]
    client.pages["r1"] = page("Interview 1", "opp")
    client.blocks["r1"] = [block(notion.MARKERS["round"], id="rm"), block(create.identity_text(RoundQuery(True, "2026-09-28", None, 1)), id="ri"),
                           block("Live Notes", "heading_2", "rl"), block("round notes", id="rln"), block("Raw Notes", "heading_2", "rr")]
    return client


class Progress(unittest.TestCase):
    def test_ready_queries_match_the_existing_writer_contract(self):
        result = progress(REAL_SHAPE)
        self.assertEqual((result.code, result.ledger), ("ready", "none"))
        self.assertEqual((result.parent.company, result.parent.role), ("Example Rides", "Technical Project Manager"))
        self.assertEqual(result.round, RoundQuery(True, "2026-09-28", None, 1))

    def test_round_identity_needs_date_and_interviewer_or_ordinal(self):
        self.assertEqual(progress(Invite("Example Rides", "Program Lead", None, ordinal=1)).code, "round_date_missing")
        self.assertEqual(progress(Invite("Example Rides", "Program Lead", "2026-9-29", ordinal=1)).code, "round_date_missing")
        self.assertEqual(progress(Invite("Example Rides", "Program Lead", "2026-09-28")).code, "round_identity_incomplete")
        self.assertEqual(progress(Invite("Example Rides", "Program Lead", "2026-09-28", ordinal=0)).code, "round_identity_incomplete")
        self.assertEqual(progress(Invite("Example Rides", "Program Lead", "2026-09-28", " ")).code, "round_identity_incomplete")
        self.assertEqual(progress(Invite("Example Rides", "Program Lead", "2026-09-28", "Synthetic Person")).code, "ready")
        self.assertEqual(progress(Invite(" ", "TPM", "2026-09-28", ordinal=1)).code, "identity_invalid")

    def test_ledger_link_needs_authoritative_identity(self):
        row = {"page_id": "L1", "company": "Example Rides", "role": "Technical Project Manager", "stable_job_key": "k1", "requisition": "R-1"}
        twin = dict(row, page_id="L2", stable_job_key="k2", requisition="R-2")
        self.assertEqual(ledger_link(REAL_SHAPE, [row]), ("unverified", None))
        self.assertEqual(ledger_link(REAL_SHAPE, []), ("none", None))
        self.assertEqual(ledger_link(REAL_SHAPE, [dict(row, company="Other Co")]), ("none", None))
        self.assertEqual(ledger_link(Invite("Example Rides", "Technical Project Manager", "2026-09-28", ordinal=1, requisition="R-2"), [row, twin]), ("linked", "L2"))
        self.assertEqual(ledger_link(Invite("Example Rides", "Technical Project Manager", "2026-09-28", ordinal=1, stable_job_key="k1"), [row, twin]), ("linked", "L1"))
        self.assertEqual(ledger_link(Invite("Example Rides", "Technical Project Manager", "2026-09-28", ordinal=1, requisition="R-9"), [row]), ("unverified", None))

    def test_same_title_requisitions_never_conflate(self):
        row = {"page_id": "L1", "company": "Example Rides", "role": "Technical Project Manager"}
        result = progress(REAL_SHAPE, [row, dict(row, page_id="L2")])
        self.assertEqual((result.code, result.parent, result.round), ("requisition_ambiguous", None, None))
        dup = {"page_id": "L1", "company": "Example Rides", "role": "Technical Project Manager", "requisition": "R-1"}
        self.assertEqual(ledger_link(Invite("Example Rides", "Technical Project Manager", "2026-09-28", ordinal=1, requisition="R-1"), [dup, dict(dup, page_id="L2")]),
                         ("requisition_ambiguous", None))


    def test_contradictory_authoritative_ids_fail_closed(self):
        a = {"page_id": "L1", "company": "Example Rides", "role": "Technical Project Manager", "stable_job_key": "k1", "requisition": "R-1"}
        b = dict(a, page_id="L2", stable_job_key="k2", requisition="R-2")
        split = Invite("Example Rides", "Technical Project Manager", "2026-09-28", ordinal=1, requisition="R-2", stable_job_key="k1")
        self.assertEqual(ledger_link(split, [a, b]), ("identity_conflict", None))
        result = progress(split, [a, b])
        self.assertEqual((result.code, result.parent, result.round, result.ledger_page_id), ("identity_conflict", None, None, None))
        # One row found by key, but it records a different requisition: still a conflict, never a link.
        other = Invite("Example Rides", "Technical Project Manager", "2026-09-28", ordinal=1, requisition="R-9", stable_job_key="k1")
        self.assertEqual(ledger_link(other, [a]), ("identity_conflict", None))
        agree = Invite("Example Rides", "Technical Project Manager", "2026-09-28", ordinal=1, requisition="r-1", stable_job_key="k1")
        self.assertEqual(ledger_link(agree, [a, b]), ("linked", "L1"))

    def test_unread_ledger_is_unverified_never_none_or_linked(self):
        self.assertEqual(ledger_link(REAL_SHAPE, None), ("unverified", None))
        self.assertEqual(progress(REAL_SHAPE, None).ledger, "unverified")

class Replay(unittest.TestCase):
    def run_invite(self, client, invite=REAL_SHAPE, live=True, copies=1):
        result = progress(invite)
        self.assertEqual(result.code, "ready")
        return stage.run(10, live, environ=ENV, client=client, evidence=[(result.parent, result.round)] * copies)

    def test_replayed_real_shape_invite_writes_nothing_and_keeps_notes(self):
        client = real_shape_client()
        parent_before, round_before = notion.protected_snapshot(client, "opp"), notion.protected_snapshot(client, "r1")
        for copies in (1, 2):
            result = self.run_invite(client, copies=copies)
            self.assertEqual((result["writes"], result["parents_created"], result["rounds_created"]), (0, 0, 0))
            self.assertEqual(result["why"]["child_match"], copies)
        self.assertEqual(client.posts, [])
        self.assertEqual(notion.protected_snapshot(client, "opp"), parent_before)
        self.assertEqual(notion.protected_snapshot(client, "r1"), round_before)

    def test_new_round_for_same_opportunity_adds_one_round_once(self):
        client = real_shape_client()
        second = Invite("Example Rides", "Technical Project Manager", "2026-10-20", ordinal=2)
        result = self.run_invite(client, second)
        self.assertEqual((result["writes"], result["parents_created"], result["rounds_created"]), (1, 0, 1))
        self.assertEqual(client.posts[0]["parent"]["page_id"], "opp")
        result = self.run_invite(client, second)
        self.assertEqual((result["writes"], result["why"]["child_match"]), (0, 1))
        self.assertEqual(len(client.posts), 1)


class AcceptanceEntrypoint(unittest.TestCase):
    """The production route: lifeos.run STAGES['interview-acceptance'] -> acceptance.run -> progress -> stage.run."""
    CASE = {"company": "Example Rides", "role": "Technical Project Manager", "confirmed_round": True,
            "interview_date": "2026-09-28", "interviewer": None, "ordinal": 1}

    def run_stage(self, client, live, case=None):
        environ = {**ENV, "GITHUB_EVENT_NAME": "workflow_dispatch", "INTERVIEW_ACCEPTANCE_JSON": json.dumps(case or self.CASE)}
        with patch.dict(os.environ, environ), patch.object(notion, "make_client", return_value=client):
            return STAGES["interview-acceptance"](1, live)

    def test_replay_through_the_stage_writes_nothing_and_reports_an_unverified_link(self):
        client = real_shape_client()
        parent_before, round_before = notion.protected_snapshot(client, "opp"), notion.protected_snapshot(client, "r1")
        for live in (False, True):
            result = self.run_stage(client, live)
            self.assertEqual((result["writes"], result["writes_planned"], result["why"].get("child_match")), (0, 0, 1))
            self.assertEqual(result["ledger"], "unverified")
            for private in ("Example Rides", "Technical Project Manager", "2026-09-28"):
                self.assertNotIn(private, json.dumps(result))
        self.assertEqual(client.posts, [])
        self.assertEqual(notion.protected_snapshot(client, "opp"), parent_before)
        self.assertEqual(notion.protected_snapshot(client, "r1"), round_before)

    def test_refused_progression_never_reaches_the_writer(self):
        client = real_shape_client()
        with patch.object(stage, "run", side_effect=AssertionError("must not delegate")):
            result = self.run_stage(client, True, {**self.CASE, "ordinal": None})
        self.assertEqual((result["writes"], result["why"]), (0, {"acceptance_secret_invalid": 1}))
        self.assertEqual(client.posts, [])

if __name__ == "__main__":
    unittest.main()
