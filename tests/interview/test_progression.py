"""LIFE-42: progression is fail-closed, and a replayed real-shaped invite writes nothing through the existing writer."""
import unittest
from lifeos.interview import create, notion, stage
from lifeos.interview.models import RoundQuery
from lifeos.interview.progression import Invite, ledger_link, progress
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


if __name__ == "__main__":
    unittest.main()
