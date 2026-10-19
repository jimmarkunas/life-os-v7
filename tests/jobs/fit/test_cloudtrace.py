import json
import unittest
from unittest import mock

from lifeos.jobs import store
from lifeos.jobs.fit import cloudtrace
from tests.kit.db import FakeConn


def conn(status=(("HELD", "PUBLISHED_WOULD_EXCLUDE", 2), ("OK", None, 5), ("SKIPPED", "NO_SUBSTANTIVE_TEXT", 1)),
         scores=(("OK", 30, 3), ("OK", 80, 2), ("HELD", 46, 2)), days=(("2026-10-19", "OK", 5),), held=2, never=9136):
    seen = []

    def handler(sql, args, cursor):
        seen.append(sql)
        rows = None
        if "GROUP BY cloud_status, cloud_reason" in sql:
            rows = list(status)
        elif "GROUP BY cloud_status, cloud_score" in sql:
            rows = list(scores)
        elif "GROUP BY DATE(cloud_at)" in sql:
            rows = list(days)
        elif "JOIN v7_jobs" in sql:
            rows = [(held,)]
        elif "cloud_status IS NULL" in sql:
            rows = [(never,)]
        cursor.fetchall = lambda rows=rows: rows

    return FakeConn(handler=handler), seen


class CloudTraceTests(unittest.TestCase):
    def go(self, **kw):
        c, seen = conn(**kw)
        with mock.patch.object(cloudtrace.store, "connect", return_value=c):
            return cloudtrace.run(0, False), seen

    def test_it_returns_only_fixed_codes_bands_and_counts(self):
        out, seen = self.go()
        self.assertEqual(out["by_status"], {"HELD|PUBLISHED_WOULD_EXCLUDE": 2, "OK|": 5, "SKIPPED|NO_SUBSTANTIVE_TEXT": 1})
        self.assertEqual(out["score_bands"], {"HELD|25_49": 2, "OK|25_49": 3, "OK|75_100": 2})
        self.assertEqual((out["by_day"], out["held_published"], out["never_asked"]), ({"2026-10-19|OK": 5}, 2, 9136))
        self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in seen))      # read-only
        self.assertNotIn("job_id", json.dumps(out))                                          # no job identity, score value or text leaves the store

    def test_a_non_code_value_prints_as_other_never_as_text(self):
        out, _ = self.go(status=(("OK", "free text with a Title", 1),), scores=(), days=())
        self.assertEqual(out["by_status"], {"OK|OTHER": 1})

    def test_band_edges(self):
        self.assertEqual([cloudtrace._band(n) for n in (0, 24, 25, 49, 50, 74, 75, 100)], ["0_24", "0_24", "25_49", "25_49", "50_74", "50_74", "75_100", "75_100"])

    def test_a_write_is_refused_before_the_driver(self):
        with self.assertRaises(store.StoreError):
            cloudtrace._select(FakeConn().cursor(), "UPDATE v7_job_fit SET cloud_status=NULL")


if __name__ == "__main__":
    unittest.main()
