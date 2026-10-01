import unittest
from unittest import mock

from lifeos.jobs.resolve import stage


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class BatchTests(unittest.TestCase):
    def _run(self, rows, clock, **kw):
        saved = []
        with mock.patch.object(stage.store, "connect", lambda: FakeConn()), \
                mock.patch.object(stage.store, "ensure_schema", lambda c: None), \
                mock.patch.object(stage, "pick_rows", lambda c, s, l: rows), \
                mock.patch.object(stage, "apply_result", lambda c, i, r: saved.append(i) or "resolved"):
            counts = stage.run_rows("lensa", 999, True,
                                            lambda part: [{"outcome": "landed", "kind": "ats", "url": "u"} for _ in part],
                                            clock=clock, **kw)
        return counts, saved

    def test_every_batch_is_saved_as_it_finishes(self):
        rows = [(i, "u", "c", "t", "l") for i in range(7)]
        counts, saved = self._run(rows, lambda: 0, batch=3)
        self.assertEqual((counts["batches"], counts["resolved"], saved), (3, 7, list(range(7))))

    def test_deadline_stops_new_batches_and_keeps_finished_work(self):
        rows = [(i, "u", "c", "t", "l") for i in range(9)]
        ticks = iter([0, 0, 100000, 100000])
        counts, saved = self._run(rows, lambda: next(ticks), batch=3, deadline_minutes=1)
        self.assertEqual((counts["batches"], counts["resolved"], counts["not_reached"]), (1, 3, 6))


if __name__ == "__main__":
    unittest.main()
