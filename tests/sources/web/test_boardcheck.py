import unittest
from datetime import date
from unittest import mock

from lifeos.jobs import identity
from lifeos.sources.web import boardcheck
from lifeos.sources.web import lister

TODAY = date(2026, 10, 4)


def job(title, location="London", url=None, posted=None):
    return {"id": title, "title": title, "location": location, "url": url or f"https://x.example/{title.replace(' ', '-')}", "posted": posted, "content": None}


class Conn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, args=()):
        sql % tuple("x" for _ in args)                      # the statement survives driver formatting
        self.sql.append((sql, args))

    def fetchall(self):
        return self.rows


class BoardCheckTests(unittest.TestCase):
    def test_names_are_split_trimmed_and_the_prefix_is_ignored(self):
        self.assertEqual(boardcheck.names_from("board:Wheely, hyperexponential ,ab"), ["wheely", "hyperexponential"])
        self.assertEqual(boardcheck.names_from(None), [])

    def test_sources_match_on_id_or_company(self):
        sources = [{"id": "su-wheely-technologies-ltd", "company": "Wheely Technologies Ltd"}, {"id": "su-plentific", "company": "Plentific"}]
        self.assertEqual([s["id"] for s in boardcheck.matching_sources(["wheely"], sources)], ["su-wheely-technologies-ltd"])
        self.assertEqual([s["id"] for s in boardcheck.matching_sources(["technologies"], sources)], ["su-wheely-technologies-ltd"])

    def test_each_live_job_is_stored_dropped_or_not_stored(self):
        pm = job("Product Manager, Technical")
        self.assertEqual(boardcheck.outcome(pm, TODAY, ("PUBLISHED", None, 83, "")), ("Stored: PUBLISHED", "Fit 83"))
        self.assertEqual(boardcheck.outcome(pm, TODAY, ("EXCLUDED_FIT", "lane_exclude", 52, "Fit 52 below 60"))[1], "Fit 52 | Fit 52 below 60 | lane_exclude")
        self.assertEqual(boardcheck.outcome(pm, TODAY, None)[0], "Returned by the board but not stored")
        self.assertEqual(boardcheck.outcome(job("Senior Software Engineer"), TODAY, None)[0], "Dropped before storing: off_target_title")
        self.assertEqual(boardcheck.outcome(job("Product Manager", "Toronto, Ontario"), TODAY, None)[0], "Dropped before storing: non_target_geography")

    def test_blocks_are_grouped_by_outcome(self):
        jobs = [job("Product Manager"), job("Senior Software Engineer")]
        stored = {identity.url_hash(jobs[0]["url"]): ("PUBLISHED", None, 80, "")}
        blocks, groups = boardcheck.blocks_for("Acme", jobs, TODAY, stored)
        self.assertEqual(groups, {"Stored: PUBLISHED": 1, "Dropped before storing: off_target_title": 1})
        self.assertIn("Acme (2 on the board now)", blocks[0]["heading_1"]["rich_text"][0]["text"]["content"])

    def test_a_dry_run_reads_the_board_counts_only_and_writes_nothing(self):
        source = {"id": "su-acme", "company": "Acme Ltd", "status": "ready", "tier": "employer", "kind": "greenhouse", "slug": "acme"}
        listing = lister.Listing(lister.COMPLETE, [job("Product Manager"), job("Senior Software Engineer")])
        conn = Conn([(identity.url_hash(job("Product Manager")["url"]), "PUBLISHED", None, 80, "")])
        with mock.patch.object(boardcheck.registry, "load", return_value=[source]), mock.patch.object(boardcheck.lister, "list_source", return_value=listing), \
                mock.patch.object(boardcheck.store, "connect", return_value=conn), mock.patch.object(boardcheck, "Client") as client:
            counts = boardcheck.run(0, False, environ={"BOARD_COMPANY": "board:acme"})
        client.assert_not_called()
        self.assertEqual(counts["sources"]["su-acme"]["listed"], 2)
        self.assertNotIn("Product Manager", repr(counts))
        self.assertEqual(boardcheck.run(0, False, environ={"BOARD_COMPANY": "ab"}), {"error": "BOARD_COMPANY_missing_or_short"})

    def test_a_source_with_no_board_or_a_failed_read_is_named_not_hidden(self):
        sources = [{"id": "su-a", "company": "Alpha Ltd", "status": "bespoke", "tier": "employer", "url": "u"},
                   {"id": "su-b", "company": "Beta Ltd", "status": "ready", "tier": "employer", "kind": "greenhouse", "slug": "b"}]
        with mock.patch.object(boardcheck.registry, "load", return_value=sources), \
                mock.patch.object(boardcheck.lister, "list_source", return_value=lister.Listing(lister.FAILED, reason="network")):
            counts = boardcheck.run(0, False, environ={"BOARD_COMPANY": "board:alpha,beta"})
        self.assertEqual(counts["sources"], {"su-a": {"not_readable": "bespoke"}, "su-b": {"board": "failed:network"}})
