"""The per-board Scale-Up diagnostic: counts only, one entry per registry board, no title, link or text, and nothing written."""
import json
import unittest
from unittest import mock

from lifeos.sources.web import boardstate
from lifeos.sources.web import registry


class FakeStore:
    """Answers the diagnostic's three reads in order and records every statement (to prove it only reads)."""

    def __init__(self, sources, items, jobs):
        self.answers = [sources, items, jobs]
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.statements.append(sql)
        self.current = self.answers[len(self.statements) - 1]

    def fetchall(self):
        return self.current


class Diagnostic(unittest.TestCase):
    def run_it(self, sources=(), items=(), jobs=()):
        fake = FakeStore(list(sources), list(items), list(jobs))
        with mock.patch.object(boardstate.store, "connect", return_value=fake):
            return boardstate.run(0, False), fake

    def test_every_registry_board_has_an_entry_even_when_it_was_never_read(self):
        out, _ = self.run_it()
        ids = [s["id"] for s in registry.load(registry.PATHS["Scale-Up"])]
        self.assertEqual(sorted(out["boards"]), sorted(ids))
        self.assertEqual((out["totals"]["sources"], out["totals"]["never_read"], out["totals"]["no_jobs"]), (len(ids), len(ids), len(ids)))

    def test_a_board_reports_its_read_its_listing_and_what_became_of_it(self):
        revolut = "su-revolut-ltd"
        out, _ = self.run_it(
            sources=[(revolut, "COMPLETE", "", 3600, 0, 0)],
            items=[(revolut, "ADMITTED", "", 3), (revolut, "SUPPRESSED", "off_target_title", 300)],
            jobs=[(f"web:{revolut}", "PUBLISHED", 7), (f"web:{revolut}", "EXCLUDED_FIT", 400)])
        board = out["boards"][revolut]
        self.assertEqual((board["read"], board["last_complete"], board["failures"], board["due_now"]), ("COMPLETE", "under_1d", 0, False))
        self.assertEqual((board["listing"], board["items"]), (303, {"ADMITTED": 3, "SUPPRESSED/off_target_title": 300}))
        self.assertEqual(board["jobs"], {"PUBLISHED": 7, "EXCLUDED_FIT": 400})
        self.assertEqual((out["totals"]["with_jobs"], out["totals"]["published"], out["totals"]["listing_jobs"]), (1, 7, 303))

    def test_a_failing_board_is_counted_as_failing(self):
        out, _ = self.run_it(sources=[("su-futuristic-technologies-ltd", "FAILED", "network", 90000, 2, 1)])
        board = out["boards"]["su-futuristic-technologies-ltd"]
        self.assertEqual((board["read"], board["reason"], board["failures"], board["due_now"], board["last_complete"]), ("FAILED", "network", 2, True, "1_to_3d"))
        self.assertEqual(out["totals"]["failing"], 1)

    def test_it_only_reads_and_prints_only_ids_counts_and_fixed_codes(self):
        out, fake = self.run_it(sources=[("su-revolut-ltd", "COMPLETE", "", 10, 0, 0)])
        self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in fake.statements))
        text = json.dumps(out)
        for forbidden in ("http", "title", "company", "description"):
            self.assertNotIn(forbidden, text.lower().replace("listing_jobs", ""), forbidden)


if __name__ == "__main__":
    unittest.main()
