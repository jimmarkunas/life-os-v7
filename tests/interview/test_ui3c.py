import dataclasses
"""LIFE-88 / LIFE-46: the approved UI 3c and UI 4 trees, written in stages, reconciled from source changes, never touching
Jim's notes, child pages or another job's page. Everything here is synthetic."""
import copy
import unittest
from unittest.mock import patch
from lifeos.interview import create, notion, prep, template, ui_probe, view
from lifeos.interview.models import Child, ParentQuery, PrepEvidence, RoundQuery, Scan
from lifeos.platform.notion_client import NotionError
from lifeos.platform.runtime import RunContext
from tests.interview.test_create import WritableFake
from tests.interview.test_notion import ENV, block, page

TODAY = "2026-10-20"
# The approved pages' outlines (read Oct 20, 2026 from https://app.notion.com/p/3ff3c5a0592681aeba23e00675f097f1 and
# https://app.notion.com/p/3fa3c5a059268118ac0bd448ee12c199) with the deliberate differences named in template.py: the QA-only
# footer toggles and "(QA preview)" / "(sandbox)" suffixes are left out, "historical" is fixture wording, and Mark Complete is
# shown unavailable instead of as a checkbox.
UI3C = """0:toggle ⚙️ LIFE OS [color=gray]
1:callout [color=yellow_background icon=⚠️]
0:callout [color=blue_background icon=https://www.notion.so/icons/arrow-right_gray.svg]
1:heading_3 Action Center
1:column_list
2:column [width_ratio=0.625]
3:callout [color=default_background]
4:toggle Mark Complete · unavailable
2:column [width_ratio=0.375]
3:heading_4 Secondary Actions
3:toggle Empty / unknown states
0:divider
0:column_list
1:column [width_ratio=0.63]
2:callout [color=default_background icon=https://www.notion.so/icons/info-alternate_gray.svg]
3:heading_3 LinkedIn Contacts
3:table [table_width=3 has_column_header=True]
2:tab
3:paragraph Role Snapshot
4:heading_3 Role requirements
4:heading_3 Your fit
3:paragraph Win Strategy
4:heading_3 How to win this opportunity
2:toggle Job Description · requirement highlights
1:column [width_ratio=0.37]
2:callout [color=default_background icon=https://www.notion.so/icons/info-alternate_gray.svg]
3:heading_3 Quick Facts
3:table [table_width=2 has_row_header=True]
2:callout [color=gray_background icon=📅]
3:heading_3 Interviews
3:table [table_width=3 has_column_header=True]
2:callout [color=gray_background icon=👥]
3:heading_3 Recruiters & Coordinators
3:table [table_width=3 has_column_header=True]
2:callout [color=gray_background icon=📎]
3:heading_3 Materials Sent
0:divider
0:heading_2 Job Description — Original Posting
0:callout [color=gray_background icon=📄]
0:divider
0:heading_2 Jim’s Notes
0:callout [color=gray_background icon=✍️]
0:toggle Live Notes · protected space
0:toggle Raw Notes · protected space
0:toggle Interview Rounds · existing children remain canonical""".split("\n")
UI4 = """0:toggle ⚙️ LIFE OS · Source Health [color=gray]
1:callout [color=yellow_background icon=⚠️]
0:callout [color=blue_background icon=🎯]
0:column_list
1:column [width_ratio=0.7]
2:tab
3:paragraph Prepare
4:heading_3 Interview objective
4:heading_3 What they may evaluate
4:heading_3 Strongest stories & evidence
4:heading_3 Risks & objections
4:heading_3 Questions to ask
4:heading_3 Prior-round lessons
4:toggle Coaching methods & source requirements
3:paragraph Live Notes
4:heading_3 Questions Asked
4:heading_3 What Landed
4:heading_3 What Needs Work
4:heading_3 Signals / Concerns
4:heading_3 Commitments I Made
4:heading_3 Follow-Up Owed
4:heading_3 Next Step / Timing
3:paragraph After Interview
4:heading_3 Outcome & decisions
4:heading_3 Commitments & follow-up
4:heading_3 Transcript-backed facts
4:heading_3 Coaching & next-round improvements
4:toggle Processing requirements & degraded states
2:heading_3 Protected Raw Notes
2:toggle Raw notes · open to write
1:column [width_ratio=0.3]
2:callout [color=gray_background icon=📅]
3:heading_3 Logistics
2:callout [color=gray_background icon=👤]
3:heading_3 Interviewer
2:callout [color=gray_background icon=📼]
3:heading_3 Transcript & Recording
2:callout [color=gray_background icon=🔗]
3:heading_3 Navigation""".split("\n")
REQUEST = "https://mail.google.com/mail/u/0/#all/synthetic-request"


def opportunity(rounds=(), **facts):
    return view.Opportunity("ExampleCo", "Program Manager", "exampleco-pm-1", None, "row" if facts else None,
                            "linked" if facts else "not_linked", REQUEST, "root", rounds=rounds, **facts)


def texts(client, block_id):
    out = []
    for b in client.blocks.get(block_id, []):
        out.append(notion.text(b))
        if b["type"] == "table_row":
            out += ["".join(r.get("plain_text", (r.get("text") or {}).get("content", "")) for r in cell) for cell in b["table_row"]["cells"]]
        out += texts(client, b["id"])
    return out


def pane(client, round_id, title):
    columns = [b for b in client.blocks[round_id] if b["type"] == "column_list"][0]["id"]
    left = client.blocks[client.blocks[columns][0]["id"]]
    tab = [b for b in left if b["type"] == "tab"][0]["id"]
    return [b for b in client.blocks[tab] if notion.text(b) == title][0]["id"]


class Sim(WritableFake):
    """The production-shaped writable fake."""


class FillerSim(Sim):
    """A template page that carries one empty paragraph, applied before the write ("before") or after it ("after"), as the Full Width template did."""

    def __init__(self, lands):
        super().__init__()
        self.lands, self.template_page = lands, None

    def call(self, method, path, body=None):
        out = super().call(method, path, body)
        if method == "POST" and path == "/pages" and body.get("template"):
            self.template_page = out["id"]
            if self.lands == "before":
                self.blocks[out["id"]].insert(0, block(""))
        if method == "PATCH" and path.endswith("/children") and self.lands == "after" and self.template_page and path.split("/")[2] == self.template_page:
            self.lands = "done"
            self.blocks[self.template_page].insert(0, block(""))
        return out


def written(current=None):
    """A UI 3c page written by the staged writer under "active", with Jim's own note and an interview page after it."""
    client = Sim()
    current = current or opportunity()
    blocks = create.rendered(current, tail=None)[0]
    page_id = notion.write_tree(client, "active", blocks, title="ExampleCo — Program Manager")
    assert create.finalize(client, page_id, current, blocks) == "readback_ok"
    live = [b["id"] for b in client.blocks[page_id] if notion.text(b) == "Live Notes · protected space"][0]
    client.blocks[live].append(block("Jim's private note", id="jim-note"))
    client.blocks[page_id].append({"id": "round-1", "type": "child_page", "child_page": {"title": "Interview 1"}})
    return client, page_id


class Layout(unittest.TestCase):
    def test_both_pages_are_the_approved_trees(self):
        current = opportunity((view.Round("r1", 1, None, None),), compensation="$90/hr", work_mode="Remote", apply_url="https://jobs.example.com/1")
        self.assertEqual(notion.outline(notion.as_nodes(template.opportunity_page(current, TODAY))), UI3C)
        self.assertEqual(notion.outline(notion.as_nodes(template.round_page(current, RoundQuery(True, None, None, 1), "p", "id"))), UI4)

    def test_staged_writes_respect_notion_nesting_and_read_back_identical(self):
        client, page_id = written(opportunity(compensation="$90/hr"))
        self.assertEqual(notion.outline(notion.read_tree(client, page_id, [500]))[:-1], UI3C)   # + the Interview 1 child page
        self.assertNotIn("PLACEHOLDER", str(client.blocks))
        round_id = notion.write_tree(client, page_id, template.round_page(opportunity(), RoundQuery(True, None, None, 1), page_id, "id"), title="Interview 1")
        self.assertEqual(notion.outline(notion.read_tree(client, round_id, [500])), UI4)
        self.assertEqual([b["type"] for b in client.blocks[round_id]], ["toggle", "callout", "column_list"])   # no empty placeholder left
        self.assertIsNotNone(notion.protected_snapshot(client, round_id))

    def test_new_pages_are_created_from_the_full_width_template_then_filled(self):
        client = Sim()
        blocks = create.rendered(opportunity(), tail=None)[0]
        page_id = notion.write_tree(client, "active", blocks, title="ExampleCo — Program Manager", template=notion.FULL_WIDTH)
        self.assertEqual(client.posts[-1]["template"], {"type": "template_id", "template_id": notion.FULL_WIDTH})
        self.assertNotIn("children", client.posts[-1])                       # Notion takes no children with a template
        self.assertEqual(notion.outline(notion.read_tree(client, page_id, [500])), UI3C)
        self.assertEqual(create.finalize(client, page_id, opportunity(), blocks), "readback_ok")

    def test_a_template_that_leaves_an_empty_paragraph_on_top_does_not_break_the_page(self):
        """The 8 PM run: Notion's Full Width template left an empty first paragraph, so the marker was not the first block and the page read as not ours."""
        for lands in ("before", "after"):
            client = FillerSim(lands)
            blocks = create.rendered(opportunity(), tail=None)[0]
            page_id = notion.write_tree(client, "active", blocks, title="ExampleCo — Program Manager", template=notion.FULL_WIDTH)
            top = client.blocks[page_id]
            self.assertNotEqual((top[0]["type"], notion.text(top[0])), ("paragraph", ""), lands)
            self.assertEqual(notion.readback(client, page_id, "active", "opportunity"), "readback_ok", lands)
            self.assertEqual(notion.ownership(client, page_id, "opportunity"), notion.Ownership.MACHINE, lands)

    def test_clearing_filler_never_touches_text_or_a_page_that_would_be_left_empty(self):
        client = Sim()
        client.blocks["p"] = [block("", id="e0")]
        self.assertEqual(notion.clear_template_filler(client, "p", [50]), 0)                      # the only block stays
        client.blocks["p"] = [block("Jim wrote this", id="t"), block("", id="e")]
        self.assertEqual(notion.clear_template_filler(client, "p", [50]), 0)                      # text on top stays
        client.blocks["p"] = [block("", id=f"e{i}") for i in range(4)] + [block("fold", id="fold")]
        self.assertEqual(notion.clear_template_filler(client, "p", [50]), notion.FILLER_MAX)      # bounded
        self.assertEqual([notion.text(b) for b in client.blocks["p"]], ["", "fold"])

    def test_values_come_from_the_view_and_missing_sources_say_so(self):
        current = opportunity((view.Round("r2", 2, "2026-11-02", "Pat Example"), view.Round("r1", 1, "2026-10-01", None)),
                              compensation="$90/hr", work_mode="Remote", apply_url="https://jobs.example.com/1", fit_summary="ERP programs")
        client, page_id = written(current)
        shown = texts(client, page_id)
        for value in ("$90/hr (Job Ledger)", "Remote (Job Ledger)", "ERP programs (LIFE OS fit summary)", "Pat Example",
                      "2026-11-02 · Calendar event not verified", "Prepare for Interview 2 on 2026-11-02.", "Original posting ↗",
                      "No separately verified contacts", "No verified recruiters or coordinators", "Mark Complete · unavailable",
                      "Employment Type", "Not verified", "Calendar: not connected to LIFE OS yet"):
            self.assertIn(value, "\n".join(shown))
        for invented in ("ExampleCo Inc", "Rituraj", "Smartsheet", "$58", "QA preview", "sandbox"):
            self.assertNotIn(invented, "\n".join(shown))
        self.assertNotIn("to_do", str(client.blocks))                         # no fake completion control

    def test_a_recruiter_lead_page_says_to_reply_or_close_and_any_other_job_keeps_the_degraded_action(self):
        lead = dataclasses.replace(opportunity(), stable_job_key="recruiter-acme-program-manager", ledger="not_linked")
        label, action, due, link, basis = view.primary_action(lead, TODAY)
        self.assertEqual((label, due, basis), ("PRIMARY ACTION", "No verified deadline", "derived from the linked recruiter message"))
        self.assertIn("Reply to the recruiter", action)
        self.assertEqual(link, lead.request_url)
        self.assertEqual(view.primary_action(opportunity(), TODAY)[0], "DEGRADED · NO VERIFIED ACTION")

    def test_d15_order_and_no_invented_primary_action(self):
        rounds = (view.Round("past", 1, "2026-10-01", None), view.Round("pending", 3, None, None), view.Round("next", 2, "2026-10-25", None),
                  view.Round("later", 4, "2026-11-30", None))
        self.assertEqual([r.page_id for r in view.sort_rounds(rounds, TODAY)], ["next", "later", "pending", "past"])
        self.assertEqual(view.primary_action(opportunity(), TODAY)[0], "DEGRADED · NO VERIFIED ACTION")
        self.assertEqual(view.primary_action(opportunity((view.Round("p", 1, None, None),)), TODAY)[1:3],
                         ("Confirm a time for Interview 1 with the recruiter.", "No verified deadline"))

    def test_e10_a_round_s_recorded_calendar_state_reaches_the_opportunity_page(self):
        """LIFE-78 E10: an accepted invite is shown as source-backed; a cancelled one is never "scheduled" or "Prepare" on the opportunity."""
        accepted = opportunity((view.Round("r1", 1, "2026-11-02", None, "accepted"),))
        self.assertEqual(view.stage(accepted, TODAY), ("Interview 1 scheduled", "2026-11-02"))
        self.assertEqual(view.primary_action(accepted, TODAY)[1:], ("Prepare for Interview 1 on 2026-11-02.", "2026-11-02", None, "from the accepted Calendar invite"))
        shown = "\n".join(texts(*written(accepted)))
        for value in ("2026-11-02 · accepted Calendar invite", "Calendar: 1 interview(s) linked to a Calendar invite by reference; joining details not verified"):
            self.assertIn(value, shown)
        cancelled = opportunity((view.Round("r1", 1, "2026-11-02", None, "cancelled"),))
        self.assertEqual(view.stage(cancelled, TODAY), ("Interview 1 cancelled in Calendar", "Unavailable"))
        label, action, due, link, basis = view.primary_action(cancelled, TODAY)
        self.assertEqual((action, due, link, basis), ("Ask the recruiter whether Interview 1 moves to a new time.", "No verified deadline", REQUEST,
                                                      "derived from the cancelled Calendar invite"))
        shown = "\n".join(texts(*written(cancelled)))
        self.assertIn("2026-11-02 · cancelled in Calendar", shown)
        self.assertNotIn("Prepare for Interview 1", shown)
        later = (view.Round("r2", 2, "2026-11-09", None, "accepted"), view.Round("r1", 1, "2026-11-02", None, "cancelled"))
        self.assertEqual([r.page_id for r in view.sort_rounds(later, TODAY)], ["r2", "r1"])          # a cancelled round never sorts as upcoming
        self.assertEqual(view.stage(opportunity(view.sort_rounds(later, TODAY)), TODAY), ("Interview 2 scheduled", "2026-11-09"))

    def test_e10_only_a_dated_round_page_s_own_logistics_is_read_and_an_unreadable_one_stays_not_verified(self):
        calls = []
        def fake(client, page_id):
            calls.append(page_id)
            if page_id == "old-layout":
                raise NotionError("INTERVIEW_ROUND_LAYOUT")
            return {"dated": ("abc123def456", False), "gone": ("abc123def456", True), "no-ref": (None, False)}[page_id]
        rounds = (view.Round("pending", 1, None, None), view.Round("dated", 2, "2026-11-02", None), view.Round("gone", 3, "2026-11-09", None),
                  view.Round("no-ref", 4, "2026-11-16", None), view.Round("old-layout", 5, "2026-11-23", None))
        with patch.object(create, "round_calendar", fake):
            got = view.with_calendar(object(), rounds)
        self.assertEqual([r.calendar for r in got], [None, "accepted", "cancelled", None, None])
        self.assertNotIn("pending", calls)                                    # an undated round has no invite to read

    def test_ledger_facts_only_from_the_exact_row(self):
        client = WritableFake()
        rich = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}]}
        client.pages["row"] = {"properties": {"Stable Job Key": rich("exampleco-pm-1"), "Compensation": rich("$90/hr"),
                                              "Location / Work Mode": rich("Remote"), "Apply URL": {"type": "url", "url": "https://jobs.example.com/1"}}}
        self.assertEqual(view.ledger_facts(client, "row", "exampleco-pm-1")[1]["compensation"], "$90/hr")
        self.assertEqual(view.ledger_facts(client, "row", "other-job"), ("mismatch", {}))
        self.assertEqual(view.ledger_facts(client, "missing", "exampleco-pm-1"), ("unreadable", {}))
        self.assertEqual(view.ledger_facts(client, None, "exampleco-pm-1"), ("not_linked", {}))


class Reconcile(unittest.TestCase):
    def protected(self, client, page_id):
        return notion.human_tail(client, page_id), [b["id"] for b in client.blocks[page_id] if b["type"] == "child_page"]

    def test_replay_writes_nothing_and_a_source_change_updates_the_same_page(self):
        client, page_id = written()
        kept, out = self.protected(client, page_id), {"writes": 0}
        start = len(client.calls)
        self.assertEqual(create.refresh(client, page_id, opportunity(), True, out), "parent_unchanged")
        self.assertFalse([c for c in client.calls[start:] if c[0] != "GET"])
        changed = opportunity((view.Round("round-1", 1, None, None),), compensation="$95/hr")
        self.assertEqual(create.refresh(client, page_id, changed, False, out), "refresh_allowed")
        self.assertFalse([c for c in client.calls[start:] if c[0] != "GET"])
        self.assertEqual(create.refresh(client, page_id, changed, True, out), "parent_refreshed")
        shown = "\n".join(texts(client, page_id))
        self.assertIn("$95/hr (Job Ledger)", shown)
        self.assertIn("Scheduling pending · no Calendar event", shown)
        self.assertEqual(self.protected(client, page_id), kept)               # Jim's notes and the interview page untouched
        self.assertIn("Jim's private note", shown)
        self.assertEqual([notion.text(b) for b in client.blocks[page_id]].count(notion.FOLD), 1)
        self.assertEqual(notion.outline(notion.read_tree(client, page_id, [500]))[:-1], UI3C)
        self.assertEqual(create.refresh(client, page_id, changed, True, out), "parent_unchanged")

    def test_renaming_a_linked_page_is_not_an_edit(self):
        client, page_id = written(opportunity((view.Round("round-1", 1, None, None),)))
        runs = [r for blocks in client.blocks.values() for b in blocks if b["type"] == "table_row" for cell in b["table_row"]["cells"]
                for r in cell if r.get("type") == "mention"]
        self.assertEqual(len(runs), 1)
        runs[0]["plain_text"] = "Interview 1 (renamed by Jim)"
        self.assertEqual(create.refresh(client, page_id, opportunity((view.Round("round-1", 1, None, None),)), True, {"writes": 0}),
                         "parent_unchanged")

    def test_a_human_edit_another_job_or_an_unknown_layout_stops_without_writing(self):
        for mode, code in (("edit", "machine_region_edited"), ("job", "identity_mismatch"), ("legacy", "parent_legacy_layout"),
                           ("notes", "parent_unrecognized")):
            with self.subTest(mode=mode):
                client, page_id = written()
                current = opportunity(compensation="$95/hr")
                if mode == "edit":
                    columns = [b for b in client.blocks[page_id] if b["type"] == "column_list"][-1]
                    left = client.blocks[client.blocks[columns["id"]][0]["id"]]
                    left.append(block("Jim typed here", id="typed"))
                if mode == "job":
                    current = view.Opportunity("Other", "Role", "other-job", None, None, "not_linked", None, "root")
                if mode == "legacy":
                    fold = client.blocks[page_id][0]
                    client.blocks[fold["id"]] = [b for b in client.blocks[fold["id"]] if not create.RENDER.fullmatch(notion.text(b))]
                if mode == "notes":
                    client.blocks[page_id].append(block(notion.JIM_NOTES, "heading_2", "second-notes"))
                before, start = copy.deepcopy(client.blocks), len(client.calls)
                self.assertEqual(create.refresh(client, page_id, current, True, {"writes": 0}), code)
                self.assertFalse([c for c in client.calls[start:] if c[0] != "GET"])
                self.assertEqual(client.blocks, before)

    def test_a_ui_3b_page_migrates_only_when_authorized_and_keeps_jims_notes(self):
        client = Sim()
        client.blocks["old"] = [template.fold(notion.MARKERS["opportunity"]), block("Canonical opportunity parent", "callout", "c"),
                                block("old machine text", id="o"), block(notion.LEGACY_NOTES, "heading_2", "jh"),
                                block("Your space", "callout", "jc"), block("Live Notes", "heading_2", "ln"), block("mine", id="m"),
                                {"id": "r1", "type": "child_page", "child_page": {"title": "Interview 1"}}]
        client.blocks["old"][0] = dict(client.blocks["old"][0], id="f", has_children=True)
        client.blocks["f"] = [block(notion.MARKERS["opportunity"], id="fm")]
        tail = notion.human_tail(client, "old")
        self.assertEqual(create.refresh(client, "old", opportunity(), True, {"writes": 0}), "parent_legacy_layout")
        self.assertEqual(create.refresh(client, "old", opportunity(), True, {"writes": 0}, migrate=True), "parent_refreshed")
        self.assertEqual(notion.human_tail(client, "old"), tail)
        self.assertEqual([b["id"] for b in client.blocks["old"]][-4:], ["jc", "ln", "m", "r1"])
        self.assertEqual(notion.text([b for b in client.blocks["old"] if b["id"] == "jh"][0]), notion.JIM_NOTES)


class ReadbackValues(unittest.TestCase):
    """finalize() accepts a page only when every machine-written value reads back, not just its shape."""
    def page(self):
        client = Sim()
        current = opportunity((view.Round("round-1", 1, None, None),), compensation="$90/hr")
        blocks = create.rendered(current, tail=None)[0]
        return client, notion.write_tree(client, "active", blocks, title="ExampleCo — Program Manager"), current, blocks

    def find(self, client, page_id, test):
        stack = list(client.blocks[page_id])
        while stack:
            b = stack.pop()
            if test(b):
                return b
            stack += client.blocks.get(b["id"], [])

    def test_a_changed_paragraph_or_table_cell_fails_although_the_structure_matches(self):
        for target in ("paragraph", "cell"):
            with self.subTest(target=target):
                client, page_id, current, blocks = self.page()
                if target == "paragraph":
                    b = self.find(client, page_id, lambda b: notion.text(b).startswith("Due: "))
                    b["paragraph"]["rich_text"] = [{"type": "text", "text": {"content": "Due: Friday"}}]
                else:
                    b = self.find(client, page_id, lambda b: b["type"] == "table_row" and "Compensation" in str(b))
                    b["table_row"]["cells"][1] = [{"type": "text", "text": {"content": "$200/hr (Job Ledger)"}}]
                self.assertEqual(create.finalize(client, page_id, current, blocks), "readback_content_mismatch")

    def test_notion_mention_titles_and_icons_are_normalized_not_failures(self):
        client, page_id, current, blocks = self.page()
        for blocks_ in client.blocks.values():
            for b in blocks_:
                for r in (b.get(b["type"]) or {}).get("rich_text", []) + [r for c in (b.get("table_row") or {}).get("cells", []) for r in c]:
                    if r.get("type") == "mention":
                        r["plain_text"] = "Interview 1"                # Notion shows a mention by the page's title
                if b["type"] == "callout" and (b["callout"].get("icon") or {}).get("type") == "external":
                    b["callout"]["icon"] = {"type": "icon", "icon": {"name": "arrow-right"}}
        self.assertEqual(create.finalize(client, page_id, current, blocks), "readback_ok")

    def test_the_opportunity_outcome_is_never_asserted_and_the_action_says_what_it_rests_on(self):
        shown = lambda current: "\n".join(texts(*written(current)))
        self.assertIn("Current opportunity status: Not verified (outcome sources not connected)", shown(opportunity()))
        self.assertNotIn("Interviewing", shown(opportunity()))
        pending = (view.Round("r1", 1, None, None),)
        self.assertIn("PRIMARY ACTION · derived from the linked interview request", shown(opportunity(pending)))
        unlinked = view.Opportunity("ExampleCo", "Program Manager", "exampleco-pm-1", None, None, "not_linked", None, "root", rounds=pending)
        self.assertIn("DEGRADED · derived from the interview page; the request message is not connected", shown(unlinked))
        self.assertNotIn("source-backed", shown(unlinked))


class TranscriptPage(unittest.TestCase):
    def round(self, transcript=None):
        client = Sim()
        client.blocks["opp"] = []
        query = RoundQuery(True, None, None, 1)
        round_id = notion.write_tree(client, "opp", template.round_page(opportunity(), query, "opp", "id"), title="Interview 1")
        if transcript is not None:
            client.blocks[round_id].append({"id": "ft", "type": "child_page", "child_page": {"title": "Full Transcript"}})
            client.blocks["ft"] = [block(transcript, id="ft-1")]
        return client, round_id

    def lines(self, client, round_id):
        return [t for t in texts(client, round_id) if t.startswith(("Transcript:", "Full Transcript page:"))]

    def test_absent_placeholder_and_accepted_transcript_pages(self):
        client, round_id = self.round()
        self.assertEqual(create.transcript_state(client, round_id), ("absent", None))
        self.assertEqual(create.link_transcript(client, round_id, True, {}), "transcript_unchanged")
        client, round_id = self.round("Transcript placeholder — paste the Granola export here")
        self.assertEqual(create.transcript_state(client, round_id), ("unverified", "ft"))
        before = notion.protected_snapshot(client, round_id)
        out = {"writes": 0}
        self.assertEqual(create.link_transcript(client, round_id, True, out), "transcript_linked")
        self.assertEqual(self.lines(client, round_id), ["Transcript: Full Transcript page exists but is not an accepted transcript",
                                                        "Full Transcript page: Exists — not an accepted transcript · "])
        runs = [r for bs in client.blocks.values() for b in bs for r in (b.get(b["type"]) or {}).get("rich_text", [])]
        self.assertIn({"type": "mention", "mention": {"type": "page", "page": {"id": "ft"}}}, runs)      # the line links the page
        self.assertEqual(notion.protected_snapshot(client, round_id), before)
        self.assertEqual(create.link_transcript(client, round_id, True, {}), "transcript_unchanged")
        self.assertEqual(client.blocks["ft"], [block("Transcript placeholder — paste the Granola export here", id="ft-1")])   # never edited
        client, round_id = self.round(create.ACCEPTED + "synthetic-import-1")
        self.assertEqual(create.transcript_state(client, round_id), ("accepted", "ft"))
        create.link_transcript(client, round_id, True, {})
        self.assertEqual(self.lines(client, round_id)[1], "Full Transcript page: Accepted transcript · ")

    def test_two_full_transcript_pages_are_ambiguous_and_write_nothing(self):
        client, round_id = self.round("placeholder")
        client.blocks[round_id].append({"id": "ft2", "type": "child_page", "child_page": {"title": "Full Transcript"}})
        before = copy.deepcopy(client.blocks)
        self.assertEqual(create.link_transcript(client, round_id, True, {}), "transcript_ambiguous")
        self.assertEqual(client.blocks, before)


class PreparationTab(unittest.TestCase):
    def setUp(self):
        self.client = Sim()
        self.client.blocks["active"].append({"id": "opp", "type": "child_page", "child_page": {"title": "ExampleCo — Program Manager"}})
        self.client.pages["opp"] = page("ExampleCo — Program Manager", "active")
        current = view.Opportunity("ExampleCo", "Program Manager", None, None, None, "not_linked", None, "root")
        blocks = create.rendered(current, tail=None)[0]
        self.client.blocks["opp"] = []
        notion.write_tree(self.client, "opp", blocks)
        self.query = RoundQuery(True, "2026-10-28", ordinal=1)
        self.round = notion.write_tree(self.client, "opp", template.round_page(current, self.query, "opp", create.identity_text(self.query)), title="Interview 1")
        self.client.pages[self.round] = page("Interview 1", "opp")
        self.client.blocks[pane(self.client, self.round, "Live Notes")].append(block("my live note", id="live-note"))

    def apply(self, evidence, live=True):
        return prep.apply(self.client, ENV, ParentQuery("ExampleCo", "Program Manager"), self.query, evidence, live, RunContext.start(60))

    def test_prepare_tab_written_once_then_matched_and_notes_untouched(self):
        before = notion.protected_snapshot(self.client, self.round)
        evidence = PrepEvidence(focus=("Delivery recovery",), questions=("What does success look like?",))
        self.assertEqual(self.apply(evidence, live=False)["code"], "derived_allowed")
        self.assertEqual(self.apply(evidence)["code"], "derived_created")
        self.assertEqual(self.apply(evidence)["code"], "derived_match")
        self.assertEqual(self.apply(PrepEvidence(focus=("Different",)))["code"], "derived_conflict")
        shown = "\n".join(texts(self.client, self.round))
        for value in ("Delivery recovery", "What does success look like?", "Preparation: accepted source-grounded preparation",
                      "Objective (derived): See Prepare.", "my live note"):
            self.assertIn(value, shown)
        self.assertEqual(notion.protected_snapshot(self.client, self.round), before)
        self.assertEqual(notion.outline(notion.read_tree(self.client, self.round, [500])), UI4)

    def test_a_human_edit_in_the_prepare_tab_is_a_conflict(self):
        self.client.blocks[pane(self.client, self.round, "Prepare")].append(block("typed in Prepare", id="typed"))
        before = copy.deepcopy(self.client.blocks)
        self.assertEqual(self.apply(PrepEvidence(focus=("A",)))["code"], "derived_conflict")
        self.assertEqual(self.client.blocks, before)


class QaAcceptanceRun(unittest.TestCase):
    """The probe's QA proof against a simulated workspace: real-shaped evidence pages, writes only under the QA parent."""
    def workspace(self):
        client = Sim()
        rich = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}]}
        client.pages[ui_probe.LEDGER_ROW] = {"properties": {"Company": rich("ExampleCo"), "Job": {"type": "title", "title": [{"plain_text": "Program Manager"}]},
                                                            "Stable Job Key": rich("exampleco-pm-1"), "Compensation": rich("$90/hr")}}
        client.pages[ui_probe.ROUND_PAGE] = page("Interview 1", "production")
        client.blocks[ui_probe.ROUND_PAGE] = [block(notion.MARKERS["round"]), block(create.identity_text(RoundQuery(True, None, None, 1)))]
        client.blocks[ui_probe.QA_PARENT] = [{"id": "human-qa", "type": "child_page", "child_page": {"title": "Smartsheet notes"}}]
        for approved, blocks in ((ui_probe.UI3C, template.opportunity_page(opportunity(), TODAY)),
                                 (ui_probe.UI4, template.round_page(opportunity(), RoundQuery(True, None, None, 1), "p", "i"))):
            client.blocks[approved] = []
            notion.write_tree(client, approved, blocks)
        original = client.call
        def tracked(method, path, body=None):
            if method == "POST" and body["parent"]["page_id"] not in (ui_probe.QA_PARENT,) and not str(body["parent"]["page_id"]).startswith("created-"):
                raise AssertionError("write outside QA")
            return original(method, path, body)
        client.call = tracked
        return client

    def test_first_and_second_runs(self):
        client = self.workspace()
        production = copy.deepcopy([client.blocks[ui_probe.ROUND_PAGE], client.pages[ui_probe.LEDGER_ROW]])
        first = ui_probe._render(client)
        self.assertEqual((first["create"], first["replay"], first["source_b"], first["protected_after_b"]),
                         ("readback_ok", "parent_unchanged", "parent_refreshed", "unchanged"))
        self.assertEqual((first["transcript_state"], first["transcript_link"], first["transcript_replay"]),
                         ("unverified", "transcript_linked", "transcript_unchanged"))
        self.assertEqual((first["wrong_job"], first["wrong_job_page"], first["rounds_seen"], first["ledger"]),
                         ("identity_mismatch", "unchanged", 1, "linked"))
        self.assertEqual(first["compare_ui3c"][-1:], ["+ 0:child_page"])        # the approved preview has no interview child yet
        self.assertEqual(first["compare_ui4"], ["+ 0:child_page"])               # the QA round's Full Transcript page sits at the end
        second = ui_probe._render(client)
        self.assertEqual((second["source_a"], second["replay"], second["source_b"], second["protected_after_b"]),
                         ("parent_refreshed", "parent_unchanged", "parent_refreshed", "unchanged"))
        titles = [b["child_page"]["title"] for b in client.blocks[ui_probe.QA_PARENT] if b["type"] == "child_page"]
        self.assertEqual(sorted(titles), sorted(["Smartsheet notes", ui_probe.RENDER_TITLE, ui_probe.CONTROL_TITLE]))
        self.assertEqual(production, [client.blocks[ui_probe.ROUND_PAGE], client.pages[ui_probe.LEDGER_ROW]])
        self.assertNotIn("exampleco-pm-1", str(first) + str(second))


if __name__ == "__main__":
    unittest.main()
