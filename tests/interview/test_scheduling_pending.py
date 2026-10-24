"""LIFE-42: a verified, source-linked first-interview request with no date yet is exactly one Interview 1 in Scheduling Pending.

Everything is invented; the fake is the production-shaped page-parent Notion fake from test_create."""
import json
import unittest
from lifeos.interview import create, notion, stage
from lifeos.interview.models import RoundQuery
from lifeos.interview.progression import Invite, progress
from tests.interview.test_create import WritableFake
from tests.interview.test_notion import ENV, block, page

COMPANY, ROLE = "ExampleCo", "Senior Technical Program Manager"
SOURCE = "https://mail.google.com/mail/u/0/#all/synthetic-request-1"
ROW = {"page_id": "L1", "company": COMPANY, "role": ROLE, "requisition": "EX-1234", "stable_job_key": "exampleco-stpm-ex1234"}
TWIN = dict(ROW, page_id="L2", requisition="EX-5678", stable_job_key="exampleco-stpm-ex5678")
REQUEST = Invite(COMPANY, ROLE, None, requisition="EX-1234", source=SOURCE)


def opportunity(rounds=()):
    """An existing machine-owned ExampleCo parent with human notes, plus any existing round children."""
    client = WritableFake()
    client.blocks["left"].append({"id": "opp", "type": "child_page", "child_page": {"title": f"{COMPANY} — {ROLE}"}})
    client.pages["opp"] = page(f"{COMPANY} — {ROLE}")
    client.pages["opp"]["parent"] = {"type": "block_id", "block_id": "left"}
    client.blocks["opp"] = [block(notion.MARKERS["opportunity"], id="m"), block("Live Notes", "heading_2", "live"), block("Jim's live notes", id="ln"),
                            block("Raw Notes", "heading_2", "raw"), block("Jim's raw notes", id="rn"), block("Interview Rounds", "heading_2", "ir"), block(notion.JIM_NOTES, "heading_2", "jn")]
    for n, (title, identity) in enumerate(rounds):
        rid = f"r{n}"
        client.blocks["opp"].append({"id": rid, "type": "child_page", "child_page": {"title": title}})
        client.pages[rid] = page(title, "opp")
        client.blocks[rid] = [block(notion.MARKERS["round"], id=f"{rid}m"), block(identity, id=f"{rid}i"), block("Live Notes", "heading_2", f"{rid}l"),
                              block("Raw Notes", "heading_2", f"{rid}r")]
    return client


def run(client, result, copies=1, live=True):
    return stage.run(10, live, environ=ENV, client=client, evidence=[(result.parent, result.round)] * copies)


def _all_text(blocks):
    for b in blocks:
        body = b[b["type"]]
        yield "".join(r["text"]["content"] for r in body.get("rich_text", []))
        yield from _all_text(body.get("children", []))


def all_blocks(client, block_id):
    for b in client.blocks.get(block_id, []):
        yield b
        yield from all_blocks(client, b["id"])


def tree_text(client, block_id):
    return [notion.text(b) for b in all_blocks(client, block_id)]


def plain(blocks):
    return "\n".join(notion.text(b) for b in blocks)


class SchedulingPending(unittest.TestCase):
    def test_request_creates_one_interview_1_in_scheduling_pending_with_the_full_template(self):
        result = progress(REQUEST, [ROW, TWIN])
        self.assertEqual((result.code, result.ledger, result.ledger_page_id), ("ready", "linked", "L1"))
        self.assertEqual(result.round, RoundQuery(True, None, None, 1, source=SOURCE, requisition="EX-1234", ledger_page_id="L1"))
        client = opportunity()
        before = notion.protected_snapshot(client, "opp")
        counts = run(client, result)
        self.assertEqual((counts["writes"], counts["rounds_created"], counts["readback_ok"], counts["why"]), (1, 1, 1, {"round_created": 1}))
        (post,) = client.posts
        self.assertEqual((post["parent"]["page_id"], post["properties"]["title"]["title"][0]["text"]["content"]), ("opp", "Interview 1"))
        blocks = client.blocks["created-1"]                                    # UI 4 (LIFE-46 10875), written in stages
        self.assertEqual([b["type"] for b in blocks], ["toggle", "callout", "column_list"])
        self.assertEqual(notion.text(blocks[0]), notion.ROUND_FOLD)
        lines = client.blocks[blocks[0]["id"]]
        self.assertEqual([notion.text(b) for b in lines[:2]], [notion.MARKERS["round"], create.identity_text(result.round)])
        self.assertEqual(json.loads(notion.text(lines[1]).split(";", 1)[1]), {"interview_date": None, "interviewer": None, "ordinal": 1})
        shape = notion.outline(notion.read_tree(client, "created-1", [500]))
        for part in ("0:column_list", "1:column [width_ratio=0.7]", "1:column [width_ratio=0.3]", "2:tab", "3:paragraph Prepare",
                     "3:paragraph Live Notes", "3:paragraph After Interview", "2:heading_3 Protected Raw Notes", "2:toggle Raw notes · open to write"):
            self.assertIn(part, shape)
        text = "\n".join(tree_text(client, "created-1"))
        for shown in ("Interview snapshot", "Interviewer: Not verified", "Status: Scheduling Pending", "No verified Calendar event or meeting time",
                      "Request message: linked", "Logistics", "Calendar event: Unavailable", "Transcript & Recording", "Navigation",
                      "Previous round: None — this is Interview 1", "Source Health — ExampleCo · Interview 1"):
            self.assertIn(shown, text)
        mentions = [r["mention"]["page"]["id"] for b in all_blocks(client, "created-1") for r in b[b["type"]].get("rich_text", []) if r.get("type") == "mention"]
        self.assertEqual(mentions, ["opp", "root"])                            # Navigation: the opportunity and the Hiring Pipeline
        self.assertNotIn("2026", text)                                               # no time is invented
        self.assertEqual(notion.protected_snapshot(client, "opp"), before)              # Jim's parent notes untouched

    def test_the_page_carries_every_ui_4_section_and_label(self):
        """UI 4 (LIFE-46 10874/10875): every section, tab, field and state toggle of the approved page is present."""
        client = opportunity()
        run(client, progress(REQUEST, [ROW]))
        text = "\n".join(tree_text(client, "created-1"))
        for label in ("⚙️ LIFE OS · Source Health", "Guardrails:", "Interview snapshot", "Interviewer:", "Status:", "When:", "Next action:",
                      "Objective (derived):", "Prepare", "Interview objective", "What they may evaluate", "Strongest stories & evidence",
                      "Risks & objections", "Questions to ask", "Prior-round lessons", "Coaching methods & source requirements",
                      "Live Notes", "Questions Asked", "What Landed", "What Needs Work", "Signals / Concerns", "Commitments I Made",
                      "Follow-Up Owed", "Next Step / Timing", "After Interview", "Outcome & decisions", "Commitments & follow-up",
                      "Transcript-backed facts", "Coaching & next-round improvements", "Processing requirements & degraded states",
                      "Protected Raw Notes", "Raw notes · open to write", "Logistics", "Date / time:", "Calendar event:",
                      "Meeting link / location:", "Name:", "Role:", "LinkedIn:", "Transcript & Recording", "Granola:", "Full Transcript page:",
                      "Audio / video recording:", "Navigation", "Previous round:"):
            self.assertIn(label, text)

    def test_replay_reuses_the_same_child_and_writes_nothing(self):
        client = opportunity()
        result = progress(REQUEST, [ROW])
        run(client, result)
        child = "created-1"
        notes = notion.protected_snapshot(client, child)
        for copies in (1, 2):
            counts = run(client, result, copies)
            self.assertEqual((counts["writes"], counts["rounds_created"], counts["why"]), (0, 0, {"child_match": copies}))
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(notion.protected_snapshot(client, child), notes)
        self.assertEqual(notion.explicit_child(client, child).interview_date, None)

    def test_same_run_duplicate_request_creates_exactly_one_child(self):
        client = opportunity()
        counts = run(client, progress(REQUEST, [ROW]), copies=2)
        self.assertEqual((counts["rounds_created"], len(client.posts)), (1, 1))

    def test_a_scheduled_interview_1_already_there_is_matched_never_duplicated(self):
        dated = create.identity_text(RoundQuery(True, "2026-10-21", None, 1))
        client = opportunity([("Interview 1", dated)])
        counts = run(client, progress(REQUEST, [ROW]))
        self.assertEqual((counts["writes"], counts["why"]), (0, {"child_match": 1}))
        unnumbered = create.identity_text(RoundQuery(True, "2026-10-21", "Synthetic Person"))
        client = opportunity([("Interview — 2026-10-21", unnumbered)])
        counts = run(client, progress(REQUEST, [ROW]))
        self.assertEqual((counts["writes"], counts["why"]), (0, {"round_identity_incomplete": 1}))
        self.assertEqual(client.posts, [])

    def test_a_later_dated_invite_never_adds_a_second_interview_1(self):
        client = opportunity()
        run(client, progress(REQUEST, [ROW]))
        later = progress(Invite(COMPANY, ROLE, "2026-10-22", ordinal=1), [ROW])
        counts = run(client, later)
        self.assertEqual((counts["writes"], counts["why"]), (0, {"round_identity_incomplete": 1}))   # updating the same child is the next package
        self.assertEqual(len(client.posts), 1)

    def test_missing_date_needs_the_request_and_is_only_interview_1(self):
        for invite, code in ((Invite(COMPANY, ROLE, None, requisition="EX-1234"), "request_source_missing"),
                             (Invite(COMPANY, ROLE, None, requisition="EX-1234", source="  "), "request_source_missing"),
                             (Invite(COMPANY, ROLE, None, requisition="EX-1234", source="http://mail.example/1"), "request_source_missing"),
                             (Invite(COMPANY, ROLE, None, requisition="EX-1234", source="see email"), "request_source_missing"),
                             (Invite(COMPANY, ROLE, None, ordinal=2, requisition="EX-1234", source=SOURCE), "round_date_missing"),
                             (Invite(COMPANY, ROLE, None, ordinal=True, requisition="EX-1234", source=SOURCE), "round_date_missing")):
            with self.subTest(invite=invite):
                result = progress(invite, [ROW])
                self.assertEqual((result.code, result.parent, result.round), (code, None, None))

    def test_unverified_ambiguous_or_mismatched_identity_creates_nothing(self):
        cases = (
            ("no exact id", Invite(COMPANY, ROLE, None, source=SOURCE), [ROW], ("ledger_unverified", "none")),
            ("ledger not read", REQUEST, None, ("ledger_unverified", "unverified")),
            ("company/title only", REQUEST, [dict(ROW, requisition=None, stable_job_key=None)], ("ledger_unverified", "unverified")),
            ("unknown requisition", Invite(COMPANY, ROLE, None, requisition="EX-9999", source=SOURCE), [ROW], ("ledger_unverified", "unverified")),
            ("unknown requisition, two rows", Invite(COMPANY, ROLE, None, requisition="EX-9999", source=SOURCE), [ROW, TWIN],
             ("requisition_ambiguous", "requisition_ambiguous")),
            ("other company", REQUEST, [dict(ROW, company="Other Co")], ("ledger_unverified", "none")),
            ("wrong role", REQUEST, [dict(ROW, role="Data Engineer")], ("ledger_unverified", "none")),
            ("two rows, one requisition", REQUEST, [ROW, dict(ROW, page_id="L3")], ("requisition_ambiguous", "requisition_ambiguous")),
            ("key and requisition disagree", Invite(COMPANY, ROLE, None, requisition="EX-1234", stable_job_key="exampleco-stpm-ex5678", source=SOURCE),
             [ROW, TWIN], ("identity_conflict", "identity_conflict")))
        for name, invite, rows, expected in cases:
            with self.subTest(name):
                result = progress(invite, rows)
                self.assertEqual((result.code, result.ledger), expected)
                self.assertEqual((result.parent, result.round, result.ledger_page_id), (None, None, None))

    def test_a_human_owned_parent_is_never_written(self):
        client = opportunity()
        client.blocks["opp"][0] = block("Jim's own opportunity page", id="m")
        counts = run(client, progress(REQUEST, [ROW]))
        self.assertEqual((counts["writes"], counts["why"]), (0, {"human_page": 1}))
        self.assertEqual(client.posts, [])

    def test_the_writer_refuses_an_undated_round_other_than_1_even_with_a_source(self):
        client = opportunity()
        for query in (RoundQuery(True, None, None, 2, source=SOURCE, requisition="EX-1234"), RoundQuery(True, None, None, 1)):
            with self.subTest(query=query):
                counts = stage.run(10, True, environ=ENV, client=client, evidence=[(progress(REQUEST, [ROW]).parent, query)])
                self.assertEqual(counts["writes"], 0)
                self.assertTrue(set(counts["why"]) <= {"round_date_missing", "round_identity_incomplete"}, counts["why"])
        self.assertEqual(client.posts, [])

    def test_undated_identity_is_valid_only_for_interview_1(self):
        self.assertEqual(create.parse_identity(create.identity_text(RoundQuery(True, None, None, 1))),
                         {"interview_date": None, "interviewer": None, "ordinal": 1})
        self.assertIsNone(create.parse_identity(create.identity_text(RoundQuery(True, None, None, 2))))
        self.assertIsNone(create.parse_identity(create.identity_text(RoundQuery(True, None, "Synthetic Person", None))))
        self.assertIsNone(create.parse_identity(create.IDENTITY_PREFIX + '{"interview_date":null,"interviewer":null,"ordinal":true}'))

    def test_counts_never_carry_private_request_details(self):
        client = opportunity()
        counts = run(client, progress(REQUEST, [ROW]))
        for private in (COMPANY, ROLE, "EX-1234", SOURCE, "synthetic-request"):
            self.assertNotIn(private, json.dumps(counts))


class ListingLag(unittest.TestCase):
    def test_a_new_round_missing_from_its_parents_listing_is_confirmed_by_its_own_id_and_the_replay_writes_nothing(self):
        """LIFE-108: real Notion can leave a just-created page out of its parent's child listing for a few seconds."""
        result = progress(REQUEST, [ROW, TWIN])
        client = opportunity()
        client.hide_created = True
        counts = run(client, result)
        self.assertEqual((counts["writes"], counts["rounds_created"], counts["readback_ok"], counts["why"]), (1, 1, 1, {"round_created": 1}))
        client.blocks["opp"].append({"id": "created-1", "type": "child_page", "child_page": {"title": "Interview 1"}})   # the listing catches up
        again = run(client, result)
        self.assertEqual((again["writes"], len(client.posts)), (0, 1))


if __name__ == "__main__":
    unittest.main()
