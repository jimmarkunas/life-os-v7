"""D118: the Amazon card renders canonical order rows into the one 'Amazon Orders' callout."""
import copy
import inspect
import pathlib
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.amazon import card, orders, stage
from lifeos.platform import report_region, router
from tests.kit.amazon import AmazonNotion, notion_page
from tests.kit.notion import AmazonRegions

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 10, 4, 13, 30, tzinfo=TZ)
ENV = {"AMAZON_CARD_BLOCK_ID": "amazon-callout", "JIRA_CARD_BLOCK_ID": "jira-callout", "CALENDAR_CARD_BLOCK_ID": "calendar-callout",
       "BILLS_CARD_BLOCK_ID": "bills-callout", "NOTION_AMAZON_DATA_SOURCE_ID": "source-example"}


def order(n, status, latest, *, total="12.97", summary=None, review=False):
    oid = f"114-{n:07d}-0000001"
    return {"Order ID": oid, "Status": status, "Ordered At": latest, "Latest Event At": latest, "Grand Total": total, "Item Summary": summary or f"Item {n}",
            "Item Count": None, "Amazon Order URL": f"https://www.amazon.com/your-orders/order-details?orderID={oid}", "Source Message IDs": f"m{n}",
            "Last Source Subject": "x", "Last Reconciled At": latest, "Needs Review": review}


class Reader(AmazonNotion):
    def __init__(self, rows, fail=False):
        super().__init__()
        self.pages, self.bodies, self.fail = [notion_page(r["Order ID"], r, f"page-{i}") for i, r in enumerate(rows)], [], fail

    def query_data_source(self, source_id, body):
        self.bodies.append(body)
        if self.fail:
            from lifeos.platform.notion_client import NotionError
            raise NotionError("NOTION_HTTP_500")
        return {"results": copy.deepcopy(self.pages), "has_more": False}


ROWS = [order(1, "SHIPPED", "2026-10-03T15:00:00+00:00", summary="Garden hose"), order(2, "ORDERED", "2026-10-04T10:00:00+00:00", summary="USB cable"),
        order(3, "DELIVERED", "2026-10-03T17:58:00+00:00", summary="2 Device Accessories items"),
        order(4, "DELIVERED", "2026-09-26T18:18:00+00:00", summary="Old office item"), order(5, "REVIEW", "2026-10-01T10:00:00+00:00", review=True, summary=None),
        order(6, "SHIPPED", "2026-06-08T10:00:00+00:00", summary="June order")]


def texts(blocks):
    return [report_region.plain(b) for b in blocks]


class ReturnRenderTests(unittest.TestCase):
    """LIFE-124: returning orders and refunds are their own sections; refunds leave after the same 7 days as Delivered."""
    def test_returning_and_refunded_sections_and_their_fall_off(self):
        rows = [order(1, "RETURNING", "2026-10-03T15:00:00+00:00", summary="Phone case"), order(2, "REFUNDED", "2026-10-03T17:00:00+00:00", summary="Screen protector"),
                order(3, "REFUNDED", "2026-09-26T18:00:00+00:00", summary="Old refund"), order(4, "RETURNING", "2026-06-08T10:00:00+00:00", summary="Old return")]
        blocks, counts = card.render(rows, NOW)
        text = texts(blocks)
        self.assertEqual(text[0], "Updated 1:30 PM CT · 0 in transit · 0 delivered (last 7 days) · 1 returning · 1 refunded (last 7 days) · 0 need review")
        self.assertIn("Returning", text)
        self.assertIn("Refunded, last 7 days", text)
        self.assertTrue(any(t.startswith("Phone case · Returning") for t in text))
        self.assertTrue(any(t.startswith("Screen protector · Refunded") for t in text))
        self.assertFalse(any("Old refund" in t or "Old return" in t for t in text))
        self.assertEqual((counts["returning"], counts["refunded"]), (1, 1))

    def test_the_query_asks_for_returning_and_recent_refunded_rows(self):
        text = str(card._query_body("2026-09-27"))
        self.assertIn("RETURNING", text)
        self.assertIn("REFUNDED", text)

    def test_the_filter_never_nests_deeper_than_notion_allows(self):
        """Notion accepts a compound filter inside a compound filter, and no deeper: three levels is a 400 and the card never redraws (LIFE-124 regression)."""
        def depth(node):
            kids = [v for key in ("or", "and") for v in node.get(key, [])]
            return 1 + max((depth(k) for k in kids), default=0) if kids else 0
        self.assertLessEqual(depth(card._query_body("2026-09-27")["filter"]), 2)


class RenderTests(unittest.TestCase):
    def test_summary_sections_and_the_seven_day_delivered_window(self):
        blocks, counts = card.render(ROWS, NOW)
        text = texts(blocks)
        self.assertEqual(text[0], "Updated 1:30 PM CT · 2 in transit · 1 delivered (last 7 days) · 1 need review · 1 older than 30 days not shown")
        self.assertIn("In transit", text)
        self.assertTrue(any(t.startswith("USB cable · Ordered") for t in text))
        self.assertTrue(any(t.startswith("Garden hose · Shipped") for t in text))
        self.assertTrue(any(t.startswith("2 Device Accessories items · Delivered") for t in text))
        self.assertFalse(any("Old office item" in t for t in text))                           # delivered 8 days ago is outside the 7-day window
        self.assertFalse(any("June order" in t for t in text))                                # an open order untouched for four months is counted, never listed
        self.assertEqual((counts["in_transit"], counts["delivered"], counts["review"], counts["older_open"], counts["status"]), (2, 1, 1, 1, "fresh"))

    def test_the_item_links_to_its_amazon_order_and_a_missing_total_is_said_not_zero(self):
        blocks, _ = card.render([order(9, "SHIPPED", "2026-10-03T15:00:00+00:00", total=None)], NOW)
        bullet = next(b for b in blocks if b["type"] == "bulleted_list_item")
        parts = bullet["bulleted_list_item"]["rich_text"]
        self.assertTrue(parts[0]["text"]["link"]["url"].startswith("https://www.amazon.com/your-orders/"))
        self.assertIn("amount unknown", texts([bullet])[0])
        self.assertNotIn("$0", texts([bullet])[0])

    def test_degraded_line_keeps_the_last_orders_visible_and_never_says_updated(self):
        blocks, counts = card.render(ROWS, NOW, degraded=True)
        self.assertTrue(texts(blocks)[0].startswith("DEGRADED · Amazon sync failed at 1:30 PM CT"))
        self.assertNotIn("Updated", texts(blocks)[0])
        self.assertTrue(any(t.startswith("Garden hose") for t in texts(blocks)))
        self.assertEqual(counts["status"], "degraded")

    def test_nothing_open_is_a_real_empty_state_with_its_own_sentence(self):
        blocks, _ = card.render([], NOW)
        self.assertIn("No open orders and nothing delivered in the last 7 days", texts(blocks))

    def test_long_lists_are_capped_and_counted(self):
        rows = [order(i, "SHIPPED", f"2026-10-03T{10 + i % 10}:00:00+00:00") for i in range(14)]
        blocks, counts = card.render(rows, NOW)
        self.assertEqual(sum(1 for b in blocks if b["type"] == "bulleted_list_item"), card.MAX_LISTED)
        self.assertIn("…and 4 more", texts(blocks))
        self.assertEqual(counts["in_transit"], 14)


    def test_the_summary_links_to_the_orders_database_only_when_there_is_a_link(self):
        blocks, _ = card.render(ROWS, NOW, url="https://app.notion.com/p/" + "a" * 32)
        parts = blocks[0]["paragraph"]["rich_text"]
        self.assertEqual([p["text"]["content"] for p in parts[-2:]], [" · ", "All orders"])
        self.assertEqual(parts[-1]["text"]["link"]["url"], "https://app.notion.com/p/" + "a" * 32)
        self.assertNotIn("link", str(card.render(ROWS, NOW)[0][0]))

    def test_delivered_stays_seven_days_and_an_old_open_order_is_counted(self):
        edge = [order(1, "DELIVERED", "2026-09-27T19:00:00+00:00", summary="Inside"), order(2, "DELIVERED", "2026-09-27T17:00:00+00:00", summary="Outside"),
                order(3, "ORDERED", "2026-09-05T14:00:00+00:00", summary="Thirty days"), order(4, "ORDERED", "2026-09-03T14:00:00+00:00", summary="Thirty one days")]
        blocks, counts = card.render(edge, NOW)
        text = " | ".join(texts(blocks))
        self.assertIn("Inside", text)
        self.assertNotIn("Outside", text)
        self.assertIn("Thirty days", text)
        self.assertNotIn("Thirty one days", text)
        self.assertEqual((counts["delivered"], counts["in_transit"], counts["older_open"]), (1, 1, 1))


class RunTests(unittest.TestCase):
    def run_card(self, notion=None, rows=ROWS, **kwargs):
        notion = notion or AmazonRegions()
        env = {**ENV, **kwargs.pop("env", {})}
        counts = card.run(0, kwargs.pop("live", True), environ=env, reader=Reader(rows), client=notion, now=NOW)
        return notion, counts

    def test_live_write_replaces_only_the_old_text_keeps_other_blocks_and_every_other_region(self):
        notion = AmazonRegions()
        before = {k: notion.full_tree(k) for k in ("calendar-callout", "jira-callout", "dcc-callout", "bills-callout")}
        notion, counts = self.run_card(notion)
        kids = notion.children["amazon-callout"]
        self.assertEqual(report_region.plain(kids[0]), "Amazon Orders")
        self.assertEqual(kids[0]["id"], "heading-amazon-callout")
        self.assertTrue(report_region.plain(kids[1]).startswith("Updated 1:30 PM CT"))
        self.assertEqual(kids[-1]["id"], "amazon-table-old")                                    # a non-text block is never removed
        self.assertNotIn("last accepted order state retained", "\n".join(texts(kids)))
        self.assertEqual({k: notion.full_tree(k) for k in before}, before)
        self.assertEqual(counts["blocks_written"], len(kids) - 2)
        self.assertEqual(kids[1]["id"], "amazon-status-old")                                   # the old line is rewritten in place, never shown beside the new one
        self.assertEqual([m for m, _ in notion.log if m == "DELETE"], [])
        self.assertLess([m for m, _ in notion.log].index("PATCH"), [m for m, _ in notion.log].index("APPEND"))

    def test_the_read_asks_only_for_open_review_and_recent_delivered_orders(self):
        reader = Reader(ROWS)
        card.run(0, False, environ=ENV, reader=reader, client=AmazonRegions(), now=NOW)
        flat = str(reader.bodies[0])
        for needle in ("ORDERED", "SHIPPED", "Needs Review", "DELIVERED", "on_or_after", "2026-09-26"):
            self.assertIn(needle, flat)

    def test_the_link_comes_from_the_data_sources_parent_database_and_a_missing_parent_only_drops_the_link(self):
        class WithParent(Reader):
            def call(self, method, path, body=None):
                result = super().call(method, path, body)
                return {**result, "parent": {"type": "database_id", "database_id": "12345678-1234-1234-1234-123456789abc"}} if path.startswith("/data_sources/") else result
        self.assertEqual(card._orders_url(WithParent(ROWS), "source-example"), "https://app.notion.com/p/12345678123412341234123456789abc")
        self.assertIsNone(card._orders_url(Reader(ROWS), "source-example"))
        notion = AmazonRegions()
        card.run(0, True, environ=ENV, reader=WithParent(ROWS), client=notion, now=NOW)
        self.assertIn("All orders", report_region.plain(notion.children["amazon-callout"][1]))

    def test_dry_run_and_missing_config_write_nothing(self):
        notion, counts = self.run_card(live=False)
        self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE", "PATCH")], [])
        self.assertEqual(counts["status"], "fresh")
        self.assertEqual(card.run(0, True, environ={}, reader=Reader(ROWS), client=AmazonRegions())["status"], "not_configured")

    def test_failed_sync_flag_writes_the_degraded_line(self):
        notion, counts = self.run_card(env={"AMAZON_SYNC_OUTCOME": "failure"})
        self.assertTrue(report_region.plain(notion.children["amazon-callout"][1]).startswith("DEGRADED · Amazon sync failed"))
        self.assertEqual(counts["status"], "degraded")

    def test_an_unreadable_or_malformed_order_list_fails_closed_and_leaves_the_callout_alone(self):
        broken = Reader(ROWS)
        del broken.pages[0]["properties"]["Needs Review"]                                      # a row missing a column this card needs
        for reader in (Reader(ROWS, fail=True), broken):
            notion = AmazonRegions()
            before = copy.deepcopy(notion.children["amazon-callout"])
            with self.assertRaises(card.CardError):
                card.run(0, True, environ=ENV, reader=reader, client=notion, now=NOW)
            self.assertEqual(notion.children["amazon-callout"], before)
            self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE")], [])

    def test_wrong_target_or_missing_protected_ids_refuse_before_any_write(self):
        for env, code in (({"AMAZON_CARD_BLOCK_ID": "bills-callout"}, "AMAZON_CARD_NOT_OWNED"), ({"AMAZON_CARD_BLOCK_ID": "calendar-callout"}, "AMAZON_CARD_NOT_OWNED"),
                          ({"BILLS_CARD_BLOCK_ID": ""}, "AMAZON_PROTECTED_REGION_UNAVAILABLE"), ({"JIRA_CARD_BLOCK_ID": ""}, "AMAZON_PROTECTED_REGION_UNAVAILABLE")):
            notion = AmazonRegions()
            with self.assertRaises(card.CardError) as caught:
                card.run(0, True, environ={**ENV, **env}, reader=Reader(ROWS), client=notion, now=NOW)
            self.assertEqual(str(caught.exception), code)
            self.assertEqual([m for m, _ in notion.log if m in ("APPEND", "DELETE")], [])

    def test_a_protected_region_changed_mid_write_fails_the_run(self):
        for op in ("PATCH", "APPEND"):                                                          # the in-place rewrite, then the extra lines
            for region in ("calendar-callout", "jira-callout", "bills-callout"):
                notion = AmazonRegions(change_on=op, change_region=region)
                with self.assertRaises(card.CardError) as caught:
                    card.run(0, True, environ=ENV, reader=Reader(ROWS), client=notion, now=NOW)
                self.assertEqual(str(caught.exception), "AMAZON_PROTECTED_REGION_CHANGED")


class OwnershipTests(unittest.TestCase):
    def test_router_gives_the_region_one_owner(self):
        self.assertEqual(router.OWNERS[router.AMAZON_REGION], "v7-amazon")
        self.assertEqual(len(set(router.OWNERS.values())), len(router.OWNERS))
        router.check_write("v7-amazon", [router.AMAZON_REGION])
        with self.assertRaises(router.RouterError):
            router.check_write("v7-bills", [router.AMAZON_REGION])
        with self.assertRaises(router.RouterError):
            router.check_write("v7-amazon", [router.BILLS_REGION])

    def test_the_card_never_writes_an_order(self):
        source = inspect.getsource(card)
        for banned in ("call_once", "create_page", "update_page", "apply_amazon", "PATCH"):
            self.assertNotIn(banned, source)

    def test_the_cli_stage_exists_and_the_amazon_job_runs_ingest_then_card_after_the_other_page_writers(self):
        from lifeos import run as runner
        self.assertEqual(runner.STAGES["amazon-card"].target, ("lifeos.amazon.card", "run"))
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        doc = yaml.safe_load((pathlib.Path(card.__file__).resolve().parents[2] / ".github/workflows/domains.yml").read_text())
        job = doc["jobs"]["amazon"]
        runs = [s.get("run", "") for s in job["steps"]]
        order_ = [next(i for i, r in enumerate(runs) if f"lifeos.run {name}" in r) for name in ("amazon-orders", "amazon-card")]
        self.assertEqual(order_, sorted(order_))
        self.assertEqual(set(job["needs"]), {"jira", "agenda", "bills"})
        card_step = next(s for s in job["steps"] if s.get("id") == "acard")
        self.assertTrue(card_step["continue-on-error"])
        self.assertIn("steps.amazon.outcome", card_step["env"]["AMAZON_SYNC_OUTCOME"])
        self.assertNotIn("schedule", doc[True])


if __name__ == "__main__":
    unittest.main()
