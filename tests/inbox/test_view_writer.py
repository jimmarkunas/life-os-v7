"""LIFE-711: the Notion view writer, its QA proof stage and the ownership rules, against an in-memory fake of the Notion API (no network, no token)."""
import itertools
import unittest
from datetime import datetime, timezone

from lifeos.inbox import view_plan, view_rows, view_stage, view_synthetic, view_writer
from lifeos.inbox.model import InboxError

NOW = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)
PARENT = "a" * 32
OTHER_SOURCE = "c" * 32


class FakeNotion:
    """Just enough of the Notion API: search, create database, query a data source, create and patch pages. Records every request so a test can prove what was (not) sent."""

    def __init__(self, parent_titles=("LIFE OS QA Home",)):
        self.pages, self.requests, self._n = {}, [], itertools.count(1)
        self.parent_pages = [{"object": "page", "id": f"{i:032x}", "properties": {"title": {"type": "title", "title": [{"plain_text": t}]}}} for i, t in enumerate(parent_titles, start=1)]
        self.view = None

    def _new_id(self):
        return f"{next(self._n):032x}"

    def call(self, method, path, body=None):
        self.requests.append((method, path))
        if path == "/search":
            if body["filter"]["value"] == "page":
                return {"results": self.parent_pages}
            return {"results": [self.view] if self.view else []}
        raise AssertionError(path)

    def call_once(self, method, path, body=None, version=None, rate_retries=None):
        self.requests.append((method, path))
        if (method, path) == ("POST", "/databases"):
            db, source = self._new_id(), self._new_id()
            parent = body["parent"]["page_id"]
            self.view = {"object": "data_source", "id": source, "title": body["title"], "parent": {"type": "database_id", "database_id": db}, "database_parent": {"type": "page_id", "page_id": parent}}
            return {"id": db, "data_sources": [{"id": source}]}
        if (method, path) == ("POST", "/pages"):
            page = self._new_id()
            self.pages[page] = {"id": page, "in_trash": False, "parent": {"type": "data_source_id", "data_source_id": body["parent"]["data_source_id"]}, "properties": self._typed(body["properties"]), "n": next(self._n)}
            return {"id": page}
        if method == "PATCH" and path.startswith("/pages/"):
            page = self.pages[path.split("/")[-1]]
            if "in_trash" in body:
                page["in_trash"] = body["in_trash"]
            return {}
        raise AssertionError((method, path))

    def update_page_properties(self, page_id, properties):
        self.requests.append(("PATCH", f"/pages/{page_id}"))
        self.pages[page_id]["properties"].update(self._typed(properties))

    @staticmethod
    def _typed(props):
        return {name: {"type": next(iter(value)), **value} for name, value in props.items()}

    def query_data_source(self, source_id, body):
        self.requests.append(("POST", f"/data_sources/{source_id}/query"))
        want = body["filter"]["select"]["equals"]
        hits = [p for p in sorted(self.pages.values(), key=lambda p: p["n"]) if (p["properties"].get("Managed by", {}).get("select") or {}).get("name") == want]
        return {"results": hits, "has_more": False}

    def add_foreign(self, source_id, record_id, managed=""):
        """A page a person or another tool made: in another data source, or in ours without the Managed by mark."""
        page = self._new_id()
        props = {"Record ID": {"rich_text": [{"plain_text": record_id}]}, "Managed by": {"select": {"name": managed} if managed else None}}
        self.pages[page] = {"id": page, "in_trash": False, "parent": {"type": "data_source_id", "data_source_id": source_id}, "properties": self._typed(props), "n": next(self._n)}
        return page


def writer_for(fake):
    _, source, _ = view_writer.ensure_view(fake)
    return view_writer.ViewWriter(fake, source), source


def live_pages(fake):
    return [p for p in fake.pages.values() if not p["in_trash"]]


class Reconcile(unittest.TestCase):
    def test_first_run_creates_every_row_and_reads_back_equal_then_a_second_run_changes_nothing(self):
        fake = FakeNotion()
        writer, _ = writer_for(fake)
        wanted = view_synthetic.rows(NOW, "a")
        first = view_writer.reconcile(writer, wanted)
        self.assertEqual((first["created"], first["updated"], first["archived"]), (len(wanted), 0, 0))
        self.assertTrue(first["readback_equal"])
        self.assertEqual(first["rows"], len(wanted))
        second = view_writer.reconcile(writer, wanted)
        self.assertEqual((second["created"], second["updated"], second["archived"]), (0, 0, 0))
        self.assertTrue(second["readback_equal"])

    def test_the_next_hour_updates_one_row_archives_one_from_a_clean_source_and_keeps_a_degraded_sources_rows(self):
        fake = FakeNotion()
        writer, _ = writer_for(fake)
        view_writer.reconcile(writer, view_synthetic.rows(NOW, "a"))
        receipt = view_writer.reconcile(writer, view_synthetic.rows(NOW, "b"))
        self.assertEqual(receipt["archived"], 1)                                  # the Asana record gone from a clean source
        self.assertGreaterEqual(receipt["updated"], 1)                            # the changed Jira title and the degraded Todoist health row
        self.assertTrue(receipt["readback_equal"])
        titles = {view_writer.from_page(p)["Record ID"] for p in live_pages(fake)}
        self.assertIn("todoist:personal:jim-personal:t1", titles)                  # its source is degraded: the last accepted row stays
        self.assertNotIn("asana:bytalos:jim-bytalos:a1", titles)

    def test_every_row_has_a_workspace_a_health_text_and_the_tgp_row_is_tracked_not_connected(self):
        rows = view_synthetic.rows(NOW, "a")
        items = [r for r in rows if r["Kind"] == "item"]
        self.assertTrue(items)
        for row in items:
            self.assertIn(row["Workspace"], ("Personal", "Bytalos"))
            self.assertTrue(row["Link"].startswith("https://"))
        tgp = [r for r in rows if r["Source"] == "TGP"]
        self.assertEqual(len(tgp), 1)
        self.assertEqual(tgp[0]["Source health"], "Tracked, not connected")
        self.assertFalse(any("tgp" in r["Record ID"].lower() and r["Kind"] == "item" for r in rows))
        degraded = [r for r in rows if r.get("Health state") == "degraded"]
        self.assertTrue(degraded and all(r["Source health"].startswith("DEGRADED, last accepted") for r in degraded))      # never an ungrounded zero
        unavailable = [r for r in rows if r["Kind"] == "health" and r["Source health"].startswith("Unavailable")]
        self.assertTrue(unavailable)

    def test_the_view_schema_covers_every_property_the_projection_emits(self):
        emitted = set().union(*(set(r) for r in view_synthetic.rows(NOW, "a"))) - {"Archived"}
        self.assertLessEqual(emitted, set(view_writer.PROPERTIES))                 # a property added to view_rows without a schema entry fails here, not in production
        self.assertEqual(set(view_writer.schema()), set(view_writer.PROPERTIES))

    def test_a_comma_in_a_select_value_and_a_long_text_are_cleaned_the_way_notion_will_hand_them_back(self):
        row = {**view_synthetic.rows(NOW, "a")[0], "Bucket": "Needs me, today", "Title": "x" * 5000}
        clean = view_writer.canon(row)
        self.assertEqual(clean["Bucket"], "Needs me; today")
        self.assertLessEqual(len(clean["Title"]), 2000)


class Ownership(unittest.TestCase):
    def test_pages_that_are_not_ours_are_never_read_into_the_row_map_or_touched(self):
        fake = FakeNotion()
        writer, source = writer_for(fake)
        view_writer.reconcile(writer, view_synthetic.rows(NOW, "a"))
        elsewhere = fake.add_foreign(OTHER_SOURCE, "jira:personal:jim-personal:LIFE-1", managed=view_writer.MANAGED)   # marked, but in another data source
        by_hand = fake.add_foreign(source, "jira:personal:jim-personal:LIFE-1")                                          # in our data source, but not marked by the writer
        before = {k: dict(v["properties"]) for k, v in fake.pages.items()}
        receipt = view_writer.reconcile(writer, view_synthetic.rows(NOW, "b"))
        self.assertTrue(receipt["readback_equal"])
        self.assertNotIn(elsewhere, writer.pages.values())
        self.assertNotIn(by_hand, writer.pages.values())
        for page in (elsewhere, by_hand):
            self.assertEqual(fake.pages[page]["properties"], before[page])
            self.assertFalse(fake.pages[page]["in_trash"])

    def test_an_update_or_archive_for_a_key_outside_the_row_map_is_refused_before_any_request(self):
        fake = FakeNotion()
        writer, _ = writer_for(fake)
        sent = len(fake.requests)
        for op in ((view_plan.UPDATE, "jira:personal:jim-personal:LIFE-9", {"Title": "x"}), (view_plan.ARCHIVE, "jira:personal:jim-personal:LIFE-9", None)):
            with self.assertRaises(view_writer.ScopeError) as caught:
                writer.write(op)
            self.assertEqual(str(caught.exception), "INBOX_VIEW_WRITE_OUT_OF_SCOPE")
        self.assertEqual(len(fake.requests), sent)

    def test_a_create_goes_only_to_our_data_source_and_carries_the_managed_by_mark(self):
        fake = FakeNotion()
        writer, source = writer_for(fake)
        view_writer.reconcile(writer, view_synthetic.rows(NOW, "a"))
        for page in fake.pages.values():
            self.assertEqual(page["parent"]["data_source_id"], source)
            self.assertEqual(view_writer.from_page(page)["Managed by"], view_writer.MANAGED)

    def test_duplicate_rows_for_one_key_leave_the_oldest_and_archive_the_rest(self):
        fake = FakeNotion()
        writer, source = writer_for(fake)
        view_writer.reconcile(writer, view_synthetic.rows(NOW, "a"))
        first = next(p for p in fake.pages.values() if view_writer.from_page(p)["Record ID"].startswith("jira:personal"))
        copy = {**first, "id": "f" * 32, "n": 10**6, "properties": dict(first["properties"])}
        fake.pages[copy["id"]] = copy
        receipt = view_writer.reconcile(writer, view_synthetic.rows(NOW, "a"))
        self.assertEqual(receipt["duplicates_archived"], 1)
        self.assertTrue(fake.pages[copy["id"]]["in_trash"])
        self.assertFalse(first["in_trash"])

    def test_rollback_archives_every_row_we_own_and_nothing_else(self):
        fake = FakeNotion()
        writer, source = writer_for(fake)
        view_writer.reconcile(writer, view_synthetic.rows(NOW, "a"))
        by_hand = fake.add_foreign(source, "note:by:hand")
        receipt = view_writer.rollback(writer)
        self.assertTrue(receipt["rolled_back"])
        self.assertEqual(receipt["remaining"], 0)
        self.assertGreater(receipt["archived"], 0)
        self.assertFalse(fake.pages[by_hand]["in_trash"])
        self.assertEqual([p for p in live_pages(fake)], [fake.pages[by_hand]])

    def test_the_writer_module_holds_no_token_and_reads_no_provider(self):
        import pathlib
        text = pathlib.Path(view_writer.__file__).read_text()
        for word in ("NOTION_API_TOKEN", "os.environ", "urllib", "import requests", "followup_readers", "followup_fetchers"):
            self.assertNotIn(word, text, word)                                      # the writer takes a client; the token and every provider read live elsewhere


class Parent(unittest.TestCase):
    def test_a_missing_or_ambiguous_qa_parent_is_a_fixed_refusal_and_nothing_is_created(self):
        for titles, reason in (((), "view_parent_not_found"), (("LIFE OS QA Home", "LIFE OS QA Home"), "view_parent_ambiguous"), (("Some other page",), "view_parent_not_found")):
            fake = FakeNotion(titles)
            with self.assertRaises(InboxError) as caught:
                view_writer.ensure_view(fake)
            self.assertEqual(caught.exception.reason, reason)
            self.assertFalse([r for r in fake.requests if r[1] == "/databases"])

    def test_the_view_is_created_once_then_found(self):
        fake = FakeNotion()
        _, source, created = view_writer.ensure_view(fake)
        self.assertTrue(created)
        _, again, created2 = view_writer.ensure_view(fake)
        self.assertEqual((again, created2), (source, False))
        self.assertEqual(len([r for r in fake.requests if r == ("POST", "/databases")]), 1)


class Stage(unittest.TestCase):
    CONFIRM = {"INBOX_VIEW_MODE": "qa-proof", "INBOX_VIEW_CONFIRM": "QA"}

    def test_the_hourly_path_is_off_by_default_and_unwired_when_on_and_never_writes(self):
        fake = FakeNotion()
        self.assertEqual(view_stage.run(0, True, environ={"ACTION_INBOX_VIEW": "off"}, client=fake), {"off": 1})
        self.assertEqual(view_stage.run(0, True, environ={"ACTION_INBOX_VIEW": "on"}, client=fake), {"unwired": 1})
        self.assertEqual(fake.requests, [])

    def test_without_live_or_without_the_qa_confirmation_it_is_a_dry_run_that_sends_nothing(self):
        for env, live in ((self.CONFIRM, False), ({"INBOX_VIEW_MODE": "qa-proof"}, True), ({**self.CONFIRM, "INBOX_VIEW_CONFIRM": "yes"}, True)):
            fake = FakeNotion()
            out = view_stage.run(0, live, environ=env, client=fake, clock=lambda: NOW)
            self.assertEqual(out["dry_run"], 1)
            self.assertGreater(out["would_create"], 0)
            self.assertEqual(fake.requests, [])

    def test_the_qa_proof_creates_the_view_writes_both_scenarios_reads_back_and_reports_counts_only(self):
        fake = FakeNotion()
        out = view_stage.run(0, True, environ=self.CONFIRM, client=fake, clock=lambda: NOW)
        self.assertEqual(out["view_created"], 1)
        self.assertEqual((out["a_readback_equal"], out["b_readback_equal"]), (1, 1))
        self.assertEqual(out["b_archived"], 1)
        self.assertTrue(all(isinstance(v, int) for v in out.values()))             # counts only: no title, link or id can be in the output

    def test_the_qa_rollback_archives_the_rows_and_refuses_when_there_is_no_view(self):
        fake = FakeNotion()
        with self.assertRaises(InboxError):
            view_stage.run(0, True, environ={"INBOX_VIEW_MODE": "qa-rollback", "INBOX_VIEW_CONFIRM": "QA"}, client=fake, clock=lambda: NOW)
        view_stage.run(0, True, environ=self.CONFIRM, client=fake, clock=lambda: NOW)
        out = view_stage.run(0, True, environ={"INBOX_VIEW_MODE": "qa-rollback", "INBOX_VIEW_CONFIRM": "QA"}, client=fake, clock=lambda: NOW)
        self.assertEqual(out["rollback_rolled_back"], 1)
        self.assertEqual(live_pages(fake), [])

    def test_the_switch_file_has_the_page_switch_on(self):
        import json
        import pathlib
        data = json.loads((pathlib.Path(view_rows.__file__).parents[1] / "platform/switches.json").read_text())
        self.assertEqual(data["ACTION_INBOX_VIEW"]["state"], "on")             # LIFE-711: the page writer runs after a live followup run (the old database view writer is withdrawn)


if __name__ == "__main__":
    unittest.main()
