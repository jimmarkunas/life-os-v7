import unittest
from datetime import datetime
from unittest import mock

from lifeos.jobs import funnel, lanes

NOW = datetime(2026, 10, 10, 12, 0)


class FakeCur:
    def __init__(self):
        self.sql, self.rows = [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        self.sql.append(sql)
        if "j.enrich_attempts" in sql:
            self.rows = [("jobright", "PUBLISHED", "", 0, 1, "ats", "title", "Admitted", 81, 2), ("jobright", "HOLD", "http_403", 3, 0, "", "", None, None, 5),
                         ("lensa", "NEW", "", 0, 0, "", "", None, None, 1)]
        elif "FROM v7_jobs j" in sql:
            self.rows = [("jobright", "Passed / Review", "Fit 67 below 68", 67, 4), ("jobright", "Admitted", "", 81, 2), ("lensa", None, None, None, 1)]
        elif "TIMESTAMPDIFF" in sql:
            self.rows = [("jobright", 3, 5), ("lensa", 30, None)]
        else:
            self.rows = [("jobright", "PUBLISHED", 2), ("jobright", "HOLD", 7), ("unknown", "NEW", 1)]

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self):
        self.cur = FakeCur()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur


class FunnelTests(unittest.TestCase):
    def go(self):
        c = Conn()
        with mock.patch.object(funnel.store, "connect", return_value=c), mock.patch.object(funnel.store, "ensure_schema"):
            return funnel.run(0, False, now=NOW), c.cur.sql

    def test_report_is_counts_by_source_status_band_and_reason(self):
        out, _ = self.go()
        day = out["windows"]["24h"]
        self.assertEqual(day["status"], {"jobright": {"PUBLISHED": 2, "HOLD": 7}, "unknown": {"NEW": 1}})
        self.assertEqual(day["fit_by_band"]["jobright"], {"Passed / Review:60_67": 4, "Admitted:80_up": 2})
        self.assertEqual(day["fit_by_reason"]["jobright"], {"Passed / Review:Fit N below N": 4, "Admitted:none": 2})
        self.assertEqual(day["fit_by_band"]["lensa"], {"none:unscored": 1})
        self.assertEqual(set(out["windows"]), {"24h", "168h"})
        self.assertEqual(out["freshness_hours"], {"jobright": {"since_found": 3, "since_published": 5}, "lensa": {"since_found": 30, "since_published": None}})

    def test_a_short_summary_line_is_printed_first_with_board_and_other_apart(self):
        import io
        import json
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            out, _ = self.go()
        line = next(l for l in buffer.getvalue().splitlines() if l.startswith("funnel-summary: "))
        parsed = json.loads(line.split(": ", 1)[1])
        self.assertEqual(set(parsed), {"24h", "168h"})
        self.assertEqual(parsed["24h"]["other"], {"HOLD": 7, "NEW": 1, "PUBLISHED": 2})
        self.assertEqual(parsed["24h"]["board"], {})
        self.assertEqual(parsed["24h"]["total"], 10)
        self.assertLess(len(line), 2000)

    def test_summary_splits_board_sources_from_the_rest(self):
        windows = {"24h": {"status": {"web:lv-a": {"EXCLUDED_FIT": 2, "PUBLISHED": 1}, "web:lv-b": {"PUBLISHED": 3, "RESOLVED": 1}, "dice": {"PUBLISHED": 4}}}}
        self.assertEqual(funnel.summary(windows), {"24h": {"board": {"EXCLUDED_FIT": 2, "PUBLISHED": 4, "RESOLVED": 1}, "other": {"PUBLISHED": 4}, "total": 11}})

    def test_the_canary_gives_each_source_a_verdict_and_the_trace_of_its_newest_jobs(self):
        out, _ = self.go()
        canary = out["canary"]
        self.assertEqual(canary["jobright"]["verdict"], "flowing")                      # a job published in the last 24 hours
        self.assertEqual(canary["jobright"]["newest"][0], {"stage": "published", "status": "PUBLISHED", "reason": "none", "resolve_attempts": 0,
                                                           "enrich_attempts": 1, "link": "ats", "proof": "title", "fit": "Admitted:80_up", "age_h": 2})
        self.assertEqual(canary["jobright"]["newest"][1]["stage"], "stuck")
        self.assertEqual(canary["unknown"]["verdict"], "stuck_at_link")                 # only NEW jobs, none published
        self.assertEqual(canary["lensa"]["newest"][0]["stage"], "found")

    def test_verdicts(self):
        self.assertEqual(funnel.verdict({}), "silent")
        self.assertEqual(funnel.verdict({"NEW": 5}), "stuck_at_link")
        self.assertEqual(funnel.verdict({"RESOLVED": 4, "EXCLUDED_FIT": 1}), "stuck_at_read")
        self.assertEqual(funnel.verdict({"EXCLUDED_FIT": 5}), "found_none_published")
        self.assertEqual(funnel.verdict({"EXCLUDED_FIT": 5, "PUBLISHED": 1}), "flowing")

    def test_read_only(self):
        _, sql = self.go()
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in sql))

    def test_bands(self):
        self.assertEqual([funnel.band(s) for s in (None, 59, 60, 67, 68, 71, 72, 79, 80)], ["unscored", "lt60", "60_67", "60_67", "68_71", "68_71", "72_79", "72_79", "80_up"])


if __name__ == "__main__":
    unittest.main()


class PublicCodes(unittest.TestCase):
    """M6C: the funnel report and explain print to the public Actions log through funnel.public_code: a fixed store code or a bare Fit floor keeps
    its shape; a lane reason prints only its enumerated code, never a rule name, an employer quote or other text. reason_code itself is unchanged."""

    def test_store_codes_keep_their_shape_and_any_other_reason_is_enumerated(self):
        cases = {None: "none", "": "none", "lane_exclude": "lane_exclude", "no_job_id 3": "other", "http_404": "http_N", "dup_of_final_url": "dup_of_final_url",
                 "no_match_no_board+no_result": "no_match_no_board+no_result", "Fit 67 below 68": "Fit N below N", "remote assumed": "remote_assumed",
                 "excluded: acme-recruiting-rule": "excluded_private_rule", 'not remote: onsite required ("must work onsite at our Chicago office")': "not_remote_onsite_required",
                 "remote assumed; profile: secret-alpha": "other", "Traceback: KeyError 'FIT_PROFILE_JSON'": "other"}
        for reason, code in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(funnel.public_code(reason), code)
        self.assertEqual(funnel.reason_code("excluded: sales_role"), "excluded: sales_role")            # the private review page keeps its words

    def test_a_lowercase_word_is_never_trusted_as_a_code(self):                          # M6I: a private rule can look like a stored code
        for reason in ("private_employer_rule", "secret_company", "special_exclusions", "lane_exclude_secret", "lane_exclude secret", "http_404_acme",
                       "lane_exclude'", '"lane_exclude"', "profile: secret-alpha", "KeyError", "connection reset by peer", "Fit 67 below 68 acme"):
            with self.subTest(reason=reason):
                got = funnel.public_code(reason)
                self.assertIn(got, {c for _, c in lanes.PUBLIC_REASONS} | {"other"})
                for word in ("private", "secret", "special", "acme", "KeyError", "peer"):
                    self.assertNotIn(word, got)

    def test_the_funnel_report_never_prints_a_rule_or_a_quote(self):
        from lifeos.jobs import explain
        got = explain.summarize([(1, "EXCLUDED_FIT", "jobright", 50, "excluded: acme-recruiting-rule", None),
                                 (2, "READY", "web:x", 70, 'not remote: hybrid required ("3 days a week in our Chicago office")', None)])
        text = repr(got)
        for word in ("acme", "Chicago", "3 days"):
            self.assertNotIn(word, text)
