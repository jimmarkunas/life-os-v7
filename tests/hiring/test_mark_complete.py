"""E05 Mark Complete (Jim, Oct 24): the opportunity page's one primary action, recorded as done on the private Close screen after two steps (the link, then
Confirm), written as one line in that page's own fold and read back; the Interview writer carries the line forward and shows the action as done. Every name
here is invented; nothing is live."""
import re
import unittest
from dataclasses import replace

from lifeos.interview import create, notion, template, view
from lifeos.sources import close_app
from tests.hiring.test_close_app import Harness, LOGIN, WHO
from tests.hiring.test_notes_decision import COMPANY, ROLE

ACTION = "Reply to the recruiter's message, or close this lead."
KEY = template.action_key(ACTION)


def page_with_fold(h, action=ACTION, done=()):
    """The target Notes page gets the fold the Interview writer writes: marker, render line, action line and any completion lines."""
    ws = h.world.ws
    fold = ws.add("toggle", h.world.ws.target, notion.FOLD)
    ws.kids[ws.target].remove(fold)
    ws.kids[ws.target].insert(0, fold)
    for line in (notion.MARKERS["opportunity"], "v7-render:1;job=none;source=0123456789abcdef;page=pending", template.action_line(action),
                 *(template.done_line(k, d) for k, d in done)):
        ws.add("paragraph", fold, line)
    return fold


def lines(h, fold):
    return ["".join(r["plain_text"] for r in h.world.ws.blocks[k]["paragraph"]["rich_text"]) for k in h.world.ws.kids[fold]]


class EdgeScreen(unittest.TestCase):
    def open(self, h, key=KEY):
        return h.call("GET", "/complete", {"o": h.world.ws.target, "a": key})

    def test_the_link_shows_the_action_and_confirm_records_one_line_in_the_fold_read_back(self):
        h = Harness()
        fold = page_with_fold(h)
        status, body, _ = self.open(h)
        self.assertEqual(status, 200)
        self.assertIn("Mark “" + ACTION.replace("'", "&#x27;") + "” complete?", body)
        self.assertEqual(h.world.ws.writes(), [])                                          # the link (step 1) never writes
        status, body, _ = h.call("POST", "/complete", form={**h.fields(body), "step": "confirm"})
        self.assertEqual(status, 200, body)
        self.assertIn("Marked complete", body)
        self.assertEqual(lines(h, fold)[-1], template.done_line(KEY, h.clock.date().isoformat()))
        self.assertEqual(len([w for w in h.world.ws.writes() if w[0] == "PATCH"]), 1)
        status, body, _ = self.open(h)                                                    # the same link again: already done, nothing written
        self.assertIn("Already marked complete", body)
        self.assertEqual(len(h.world.ws.writes()), 1)

    def test_a_replayed_confirm_is_already_recorded_and_writes_nothing(self):
        h = Harness()
        page_with_fold(h)
        form = {**h.fields(self.open(h)[1]), "step": "confirm"}
        h.call("POST", "/complete", form=form)
        status, body, _ = h.call("POST", "/complete", form=form)
        self.assertIn("Already recorded", body)
        self.assertEqual(len(h.world.ws.writes()), 1)

    def test_cancel_a_forged_token_an_expired_form_or_a_changed_action_write_nothing(self):
        h = Harness()
        page_with_fold(h)
        form = h.fields(self.open(h)[1])
        self.assertIn("No changes made", h.call("POST", "/complete", form={**form, "step": "cancel"})[1])
        self.assertEqual(h.call("POST", "/complete", form={**form, "step": "confirm", "t": "0" * 64})[0], 403)
        self.assertEqual(h.call("POST", "/complete", form={**form, "step": "confirm", "a": "f" * 16})[0], 403)          # the token signs the action key
        self.assertEqual(h.call("POST", "/complete", form={**form, "step": "confirm"}, who={**WHO, "login": "someone@example.com"})[0], 403)
        self.assertEqual(h.call("POST", "/complete", form={**form, "step": "confirm"}, now=h.clock.replace(year=h.clock.year + 1))[0], 403)
        self.assertEqual(h.world.ws.writes(), [])

    def test_a_link_for_another_action_another_page_or_a_page_not_written_by_life_os_is_refused(self):
        h = Harness()
        page_with_fold(h, action="Prepare for Interview 1 on 2026-10-28.")                # the action moved on since the link was written
        status, body, _ = self.open(h)
        self.assertEqual(status, 409)
        self.assertIn("ACTION_CHANGED", body)
        self.assertEqual(self.open(h, key="nothex")[0], 400)
        bare = Harness()                                                                   # a human page: no LIFE OS fold
        self.assertIn("NOT_A_LIFE_OS_PAGE", self.open(bare)[1])
        other = Harness()
        page_with_fold(other)
        self.assertIn("NOT_FOUND", other.call("GET", "/complete", {"o": "a" * 32, "a": KEY})[1])
        self.assertEqual(h.world.ws.writes() + bare.world.ws.writes() + other.world.ws.writes(), [])

    def test_a_write_that_does_not_read_back_is_degraded_never_done(self):
        h = Harness()
        page_with_fold(h)
        form = {**h.fields(self.open(h)[1]), "step": "confirm"}
        h.world.ws.fail.add("append")
        status, body, _ = h.call("POST", "/complete", form=form)
        self.assertEqual(status, 500)
        self.assertIn("DEGRADED", body)
        self.assertNotIn("Marked complete", body)

    def test_the_edge_copy_of_the_line_formats_is_the_interview_writers(self):
        self.assertEqual((close_app.ACTION_LINE.pattern, close_app.DONE_LINE.pattern), (template.ACTION_LINE.pattern, template.DONE_LINE.pattern))
        self.assertEqual(close_app.FOLD, notion.FOLD)
        self.assertEqual(close_app.OPP_MARKER, notion.MARKERS["opportunity"])


def opp(**kw):
    base = view.Opportunity("ExampleCo", "Senior Technical Program Manager", "recruiter-exampleco-stpm", None, None, "not_linked",
                            "https://mail.google.com/mail/u/0/#all/synthetic-1", "root")
    return replace(base, **kw)


def texts(blocks):
    out = []
    for b in blocks:
        body = b[b["type"]]
        out.append("".join(r["text"]["content"] for r in body.get("rich_text", [])))
        out += texts(body.get("children", []))
    return out


def live(client, block_id):
    """The raw blocks now under `block_id` on the writable fake, links included (its block map also keeps deleted blocks)."""
    return [x for b in client.blocks.get(block_id, []) for x in [b] + live(client, b["id"])]


def links(blocks):
    return re.findall(r"'url': '([^']+)'", str(blocks))


class PageRender(unittest.TestCase):
    def test_the_primary_action_links_to_the_close_screen_by_page_and_action_key(self):
        blocks = template.opportunity_page(opp(close_base="https://edge.example.test:47443", page_id="0123456789abcdef0123456789abcdef"), "2026-10-24")
        self.assertIn(template.action_line(ACTION), texts(blocks))
        self.assertIn(f"https://edge.example.test:47443/complete?o=0123456789abcdef0123456789abcdef&a={KEY}", links(blocks))
        self.assertNotIn("Mark Complete · unavailable", texts(blocks))

    def test_without_the_close_origin_or_the_page_id_it_stays_unavailable(self):
        for view_ in (opp(), opp(close_base="https://edge.example.test"), opp(page_id="0123456789abcdef0123456789abcdef")):
            self.assertIn("Mark Complete · unavailable", texts(template.opportunity_page(view_, "2026-10-24")))

    def test_a_recorded_completion_shows_done_and_its_line_is_carried(self):
        blocks = template.opportunity_page(opp(close_base="https://edge.example.test", page_id="0" * 32, done=((KEY, "2026-10-24"),)), "2026-10-24")
        self.assertIn(template.done_line(KEY, "2026-10-24"), texts(blocks))
        self.assertTrue(any(t.startswith("✅ Done Oct 24") for t in texts(blocks)), texts(blocks))
        self.assertFalse([u for u in links(blocks) if "/complete?" in u])

    def test_the_same_wording_for_a_later_round_is_a_new_action_never_already_done(self):
        one = (view.Round("round-1", None, None, None),)
        two = one + (view.Round("round-2", None, None, None),)
        first = opp(rounds=one, close_base="https://edge.example.test", page_id="0" * 32)
        action = view.primary_action(first, "2026-10-24")[1]
        key = template.action_key(action, "round-1")
        self.assertNotEqual(key, template.action_key(action, "round-1,round-2"))
        self.assertTrue(any(t.startswith("✅ Done") for t in texts(template.opportunity_page(replace(first, done=((key, "2026-10-20"),)), "2026-10-24"))))
        later = replace(first, rounds=two, done=((key, "2026-10-20"),))
        self.assertEqual(view.primary_action(later, "2026-10-24")[1], action)                          # the very same words for the next round
        blocks = template.opportunity_page(later, "2026-10-24")
        self.assertFalse(any(t.startswith("✅ Done") for t in texts(blocks)))
        self.assertTrue([u for u in links(blocks) if "/complete?" in u])

    def test_a_degraded_action_is_never_completable(self):
        blocks = template.opportunity_page(opp(request_url=None, stable_job_key="exampleco-stpm", close_base="https://edge.example.test", page_id="0" * 32), "2026-10-24")
        self.assertFalse([t for t in texts(blocks) if t.startswith("v7-action:")])
        self.assertIn("Mark Complete · unavailable", texts(blocks))


class Writer(unittest.TestCase):
    def test_completion_lines_are_read_newest_kept_and_never_count_as_an_edit(self):
        para = lambda t: {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": t}]}}            # noqa: E731
        got = create.done_lines([para(notion.MARKERS["opportunity"]), para(template.done_line("a" * 16, "2026-10-01")), para(template.done_line("a" * 16, "2026-10-02")),
                                 para(template.done_line("b" * 16, "2026-10-03")), para("v7-done:1;key=short;on=2026-10-03")])
        self.assertEqual(got, (("a" * 16, "2026-10-01"), ("b" * 16, "2026-10-03")))
        nodes = [{"text": notion.FOLD, "children": [{"text": notion.MARKERS["opportunity"]}, {"text": template.done_line("a" * 16, "2026-10-01")}]}, {"text": "leaf"}]
        self.assertEqual(create._undone(nodes), [{"text": notion.FOLD, "children": [{"text": notion.MARKERS["opportunity"]}]}, {"text": "leaf"}])

    def test_a_page_written_before_mark_complete_keeps_its_digest_and_still_refreshes(self):
        """With no completion lines, the region digest is the plain tree digest main stores in every render line, so no existing page reads as edited."""
        from tests.interview import test_ui3c as U
        client, page_id = U.written()                                                               # written and finalized as main writes it
        machine, lines, _ = create.machine_region(client, page_id)
        skip = {b["id"] for b in lines if create.RENDER.fullmatch(notion.text(b))}
        nodes = notion.region_nodes(client, machine, [notion.TREE_MAX_CALLS], skip)
        self.assertEqual(notion.tree_digest(create._undone(nodes)), notion.tree_digest(nodes))
        old = replace(U.opportunity(), stable_job_key="exampleco-pm-1")
        self.assertEqual(create.refresh(client, page_id, old, True, {"writes": 0}), "parent_unchanged")
        current = replace(U.opportunity(), stable_job_key="exampleco-pm-1", compensation="$95/hr", ledger_page_id="row", ledger="linked")
        self.assertEqual(create.refresh(client, page_id, current, True, {"writes": 0}), "parent_refreshed")

    def test_a_line_the_close_screen_appended_is_carried_and_the_page_shows_the_action_done(self):
        from tests.interview import test_notion as TN, test_ui3c as U
        current = replace(U.opportunity(), stable_job_key="recruiter-exampleco-pm", close_base="https://edge.example.test")
        client, page_id = U.written(current)
        out = {"writes": 0}
        self.assertEqual(create.refresh(client, page_id, current, True, out), "parent_refreshed")          # its own page id joins the view: the link appears
        shown = U.texts(client, page_id)
        key = template.action_key(ACTION)
        self.assertIn(template.action_line(ACTION), shown)
        self.assertIn(f"https://edge.example.test/complete?o={page_id.replace('-', '')}&a={key}", str(live(client, page_id)))
        self.assertEqual(create.refresh(client, page_id, current, True, out), "parent_unchanged")
        fold = client.blocks[page_id][0]["id"]
        client.blocks[fold].append(TN.block(template.done_line(key, "2026-10-24"), id="done-1"))           # the Close screen's one appended line
        kept = (notion.human_tail(client, page_id), [b["id"] for b in client.blocks[page_id] if b["type"] == "child_page"])
        self.assertEqual(create.refresh(client, page_id, current, True, out), "parent_refreshed")          # not an edit: the line is carried into the rewrite
        shown = U.texts(client, page_id)
        self.assertIn(template.done_line(key, "2026-10-24"), shown)
        self.assertTrue(any(t.startswith("✅ Done Oct 24") for t in shown))
        self.assertNotIn("/complete?", str(live(client, page_id)))                                         # the rewrite carries no Mark Complete link
        self.assertEqual((notion.human_tail(client, page_id), [b["id"] for b in client.blocks[page_id] if b["type"] == "child_page"]), kept)
        self.assertEqual(create.refresh(client, page_id, current, True, out), "parent_unchanged")
        self.assertIn(template.done_line(key, "2026-10-24"), U.texts(client, page_id))


class LeadRefresh(unittest.TestCase):
    """A recruiter lead's page has no rounds, so no round evidence ever refreshed it: the refresh-only item brings it up to date (its Mark Complete link)
    and never creates a page or a round."""

    def run_(self, client, query, live=True):
        from lifeos.interview import stage
        from lifeos.interview.models import ParentQuery
        from tests.interview.test_notion import ENV
        return stage.run(10, live, environ={**ENV, "V7_CLOSE_BASE_URL": "https://edge.example.test"}, client=client, evidence=[(ParentQuery("ExampleCo", "Program Manager"), query)])

    def test_a_lead_page_gains_its_link_and_a_replay_writes_nothing(self):
        from lifeos.interview.models import RoundQuery
        from tests.interview import test_ui3c as U
        current = replace(U.opportunity(), stable_job_key="recruiter-exampleco-pm")
        client, page_id = U.written(current)
        client.blocks[page_id] = [b for b in client.blocks[page_id] if b["type"] != "child_page"]        # a lead: no interview page yet
        query = RoundQuery(True, source=U.REQUEST, stable_job_key="recruiter-exampleco-pm", refresh_only=True)
        posts = len(client.posts)
        first = self.run_(client, query)
        self.assertEqual((first["writes"], first["readback_ok"], first["why"].get("parent_refreshed"), first["blocked"]), (1, 1, 1, 0))
        self.assertIn("/complete?o=", str(live(client, page_id)))
        again = self.run_(client, query)
        self.assertEqual((again["writes"], again["why"].get("parent_exists"), again["blocked"]), (0, 1, 0))
        self.assertEqual(len(client.posts), posts)                                                   # no page and no round created

    def test_refresh_only_never_creates_adopts_or_touches_a_page_with_rounds(self):
        from lifeos.interview.models import RoundQuery
        from tests.interview import test_ui3c as U
        query = RoundQuery(True, source=U.REQUEST, stable_job_key="recruiter-exampleco-pm", refresh_only=True)
        empty = U.Sim()
        out = self.run_(empty, query)
        self.assertEqual((out["why"], out["blocked"], empty.posts), ({"refresh_not_machine": 1}, 0, []))   # no page: nothing is created, and it is no failure
        client, page_id = U.written(replace(U.opportunity(), stable_job_key="recruiter-exampleco-pm"))    # it has an interview page
        posts = len(client.posts)
        out = self.run_(client, query)
        self.assertEqual((out["writes"], len(client.posts), "parent_refreshed" in out["why"]), (0, posts, False))

    def test_a_lead_page_without_the_notes_heading_is_never_upgraded(self):
        from lifeos.interview import create as C
        from lifeos.interview.models import RoundQuery
        from tests.interview import test_ui3c as U
        from unittest import mock
        client, page_id = U.written(replace(U.opportunity(), stable_job_key="recruiter-exampleco-pm"))
        client.blocks[page_id] = [b for b in client.blocks[page_id] if b["type"] != "child_page" and notion.text(b) not in notion.NOTES]
        with mock.patch.object(C, "upgrade", side_effect=AssertionError("refresh-only must never upgrade")):
            out = self.run_(client, RoundQuery(True, source=U.REQUEST, stable_job_key="recruiter-exampleco-pm", refresh_only=True))
        self.assertEqual((out["writes"], out["blocked"], out["why"].get("parent_exists")), (0, 0, 1))

    def test_refresh_only_items_past_the_limit_wait_quietly_and_never_crowd_out_a_real_item(self):
        from lifeos.interview import stage
        from lifeos.interview.models import ParentQuery, RoundQuery
        from tests.interview.test_notion import ENV
        lead = (ParentQuery("ExampleCo", "Program Manager"), RoundQuery(True, source="https://example.test/m", stable_job_key="recruiter-x", refresh_only=True))
        real = (ParentQuery("ExampleCo", "Program Manager"), RoundQuery(True, "2026-10-28", None, 1))
        from tests.interview import test_ui3c as U
        quiet = stage.run(1, False, environ=ENV, client=U.Sim(), evidence=[real, lead, lead])
        self.assertNotIn("pipeline_incomplete", quiet["why"])
        self.assertEqual((quiet["why"].get("refresh_deferred"), quiet["blocked"]), (2, 0))             # counted, never an alarm
        loud = stage.run(1, False, environ=ENV, client=U.Sim(), evidence=[real, real])
        self.assertEqual(loud["why"].get("pipeline_incomplete"), 1)


if __name__ == "__main__":
    unittest.main()
