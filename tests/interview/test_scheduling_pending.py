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
                            block("Raw Notes", "heading_2", "raw"), block("Jim's raw notes", id="rn"), block("Interview Rounds", "heading_2", "ir")]
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


def plain(blocks):
    return "\n".join(notion.text(b) for b in blocks)


class SchedulingPending(unittest.TestCase):
    def test_request_creates_one_interview_1_in_scheduling_pending_with_the_full_template(self):
        result = progress(REQUEST, [ROW, TWIN])
        self.assertEqual((result.code, result.ledger, result.ledger_page_id), ("ready", "linked", "L1"))
        self.assertEqual(result.round, RoundQuery(True, None, None, 1, source=SOURCE, requisition="EX-1234"))
        client = opportunity()
        before = notion.protected_snapshot(client, "opp")
        counts = run(client, result)
        self.assertEqual((counts["writes"], counts["rounds_created"], counts["readback_ok"], counts["why"]), (1, 1, 1, {"round_created": 1}))
        (post,) = client.posts
        self.assertEqual((post["parent"]["page_id"], post["properties"]["title"]["title"][0]["text"]["content"]), ("opp", "Interview 1"))
        blocks = post["children"]
        self.assertEqual([notion.text(b) for b in blocks[:2]], [notion.MARKERS["round"], create.identity_text(result.round)])
        self.assertEqual(json.loads(notion.text(blocks[1]).split(";", 1)[1]), {"interview_date": None, "interviewer": None, "ordinal": 1})
        self.assertEqual([notion.text(b) for b in blocks[2:3] + blocks[4:]],
                         ["Interview 1 · Scheduling Pending", "Prepare", "Live Notes", "After Interview", "Raw Notes", "Derived"])
        brief, side = (col["column"]["children"] for col in blocks[3]["column_list"]["children"])
        text = plain(brief + side)
        for shown in ("10-second brief", "ExampleCo", "Senior Technical Program Manager", "Round: 1", "Interviewer: Not known yet",
                      "🟡 Scheduling Pending", "Not scheduled · no Calendar event yet", "requisition EX-1234",
                      "Logistics", "Calendar event: none yet", "Transcript & Recording", "Navigation", "Evidence guardrails"):
            self.assertIn(shown, text)
        links = [r["text"]["link"]["url"] for b in brief + side for r in b[b["type"]]["rich_text"] if r["text"].get("link")]
        self.assertIn(SOURCE, links)
        self.assertIn("https://www.notion.so/opp", links)
        self.assertNotIn("2026", text)                                               # no time is invented
        self.assertEqual(notion.protected_snapshot(client, "opp"), before)              # Jim's parent notes untouched

    def test_the_page_carries_every_ui_4_section_and_label(self):
        """UI 4 (LIFE-47, accepted again for LIFE-42 in comment 10829): every section, field and state toggle is present."""
        client = opportunity()
        run(client, progress(REQUEST, [ROW]))
        text = "\n".join(_all_text(client.posts[0]["children"]))
        for label in ("10-second brief", "Company:", "Role:", "Round:", "Interviewer:", "Stage:", "Interview outcome:", "When/where:",
                      "Next action:", "Objective (derived):", "Evidence freshness:",
                      "Pre-interview analysis · Game Theory + Jordan Belfort / Straight Line", "Input contract:", "Game Theory lens (derived):",
                      "Jordan Belfort / Straight Line lens (derived):", "Output:", "Refresh:", "Pre-analysis states · normal / empty / degraded",
                      "Editable live notes", "Questions Asked", "What Landed", "What Needs Work", "Signals / Concerns", "Commitments I Made",
                      "Follow-Up Owed", "Next Step / Timing",
                      "Post-interview analysis · Game Theory + Jordan Belfort / Straight Line", "Required evidence gate:", "First extract facts:",
                      "Game Theory review (derived):", "Jordan Belfort / Straight Line review (derived):", "Outputs:", "Failure state:",
                      "Transcript-backed facts", "Game Theory · post-interview", "Jordan Belfort / Straight Line · post-interview",
                      "Post-analysis states · ready / empty / degraded", "Human content safety:",
                      "Raw notes · open to write", "Logistics", "Calendar event:", "Meeting link:", "Transcript & Recording",
                      "Granola acquisition:", "Full verbatim transcript:", "Post-analysis gate:", "Audio file:", "Video/recording:", "Processing:",
                      "Navigation", "Hiring Pipeline ↗", "Prior round: none (this is Interview 1)", "Evidence guardrails"):
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


if __name__ == "__main__":
    unittest.main()
