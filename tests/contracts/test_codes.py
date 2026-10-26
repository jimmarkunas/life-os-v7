import functools
import unittest
from pathlib import Path

from lifeos.jobs import alertstate, lanes
from lifeos.jobs.fit import edge
from lifeos.platform import codes

ROOT = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def emitted(root=ROOT / "lifeos"):
    """Every code-shaped string the code can emit: {code: 'path:line'}."""
    found = {}
    for path in sorted(root.rglob("*.py")):
        for code, line in codes.candidates(path.read_text()).items():
            found.setdefault(code, f"{path.relative_to(root.parent)}:{line}")
    return found


def unregistered(found):
    return {code: where for code, where in found.items() if code not in codes.CODES and code not in codes.NOT_CODES}


class RegistryShape(unittest.TestCase):
    def test_every_entry_is_complete_and_valid(self):
        for code, entry in codes.CODES.items():
            with self.subTest(code=code):
                self.assertRegex(code, codes.CANDIDATE)
                self.assertIn(entry.klass, codes.CLASSES)
                self.assertIn(entry.severity, codes.SEVERITIES)
                self.assertIsInstance(entry.attempt, bool)
                self.assertTrue(entry.stage and entry.meaning.strip() and entry.action.strip())

    def test_a_code_is_never_both_registered_and_a_config_name(self):
        self.assertFalse(set(codes.CODES) & codes.NOT_CODES)

    def test_no_code_alerts_until_jim_turns_one_on(self):
        self.assertEqual({c for c, e in codes.CODES.items() if e.severity == "page"}, {"FIT_EDGE_REPLAY_ACCEPTED"})


class EveryCodeIsRegistered(unittest.TestCase):
    def test_every_emitted_code_is_registered_or_a_config_name(self):
        missing = unregistered(emitted())
        self.assertEqual(missing, {}, "register each new code in lifeos/platform/codes.py (stage, class, attempt, meaning, action, severity); a config or environment name goes in NOT_CODES")

    def test_the_check_turns_red_for_an_unregistered_code(self):
        found = {c: "x.py:1" for c in codes.candidates('def f():\n    raise Error("BRAND_NEW_UNREGISTERED_CODE")\n')}
        self.assertEqual(set(unregistered(found)), {"BRAND_NEW_UNREGISTERED_CODE"})
        self.assertEqual(unregistered({"FIT_EDGE_TIMEOUT": "x.py:1"}), {})

    def test_docstrings_are_not_codes(self):
        self.assertEqual(codes.candidates('"""Raises SOME_FAKE_CODE."""\nx = "REAL_CODE"\n'), {"REAL_CODE": 2})


PUBLIC_BEFORE_LIFE_72 = frozenset((
    "none http_N link_mismatch jd_thin jd_template jd_listing description_empty description_empty_tf hold_expired unresolved_after_recovery lane_exclude repost "
    "repost_ambiguous recovery_retry screen_pay listing_url no_job_id dup_other_source dup_of_final_url closed unknown Fit_N_below_N audit_url_listing_url audit_url_root "
    "audit_url_workable_no_account audit_jd_template audit_jd_listing audit_jd_thin no_match_no_board+no_result no_match_no_title+no_result no_match_ambiguous+no_result "
    "no_match_no_board+no_fit no_match_no_title+no_fit no_match_ambiguous+no_fit in_ledger same_opening audit_url_no_job_id").split())
MADE_PUBLIC_OCT_25 = frozenset("excluded_fit released_keep_list released_ledger requeued_age requeued_direct requeued_fit requeued_floor requeued_linkedin "
                               "retired_by_jim title_watch_unseen".split())     # Jim, Oct 25 2026: these fixed words print as themselves, not "other"


def emitted_reasons(root=ROOT / "lifeos"):
    found = {}
    for path in sorted(root.rglob("*.py")):
        for reason in codes.sql_reasons(path.read_text()):
            found.setdefault(reason, str(path.relative_to(root.parent)))
    return found


class StoredReasons(unittest.TestCase):
    """The lowercase words written to a job's unresolved_reason: each is registered, and the public allowlist is built from the registry."""

    def test_every_reason_the_sql_writes_or_tests_is_registered(self):
        missing = {r: w for r, w in emitted_reasons().items() if r not in codes.REASONS}
        self.assertEqual(missing, {}, "register each new unresolved_reason in lifeos/platform/codes.py REASONS")

    def test_every_lane_reason_code_is_registered(self):
        for _, code in lanes.PUBLIC_REASONS:
            self.assertIn(code, codes.REASONS)

    def test_every_entry_is_complete_and_valid(self):
        for code, entry in codes.REASONS.items():
            with self.subTest(code=code):
                self.assertIn(entry.klass, codes.CLASSES)
                self.assertIn(entry.severity, codes.SEVERITIES)
                self.assertTrue(entry.stage and entry.meaning.strip() and entry.action.strip())

    def test_the_public_allowlist_is_built_from_the_registry_and_is_exactly_what_it_was(self):
        self.assertEqual(set(alertstate.REASONS), set(PUBLIC_BEFORE_LIFE_72 | MADE_PUBLIC_OCT_25))
        self.assertEqual(alertstate.REASONS, codes.public_reasons())

    def test_the_reason_check_turns_red_for_an_unregistered_reason(self):
        found = codes.sql_reasons("cursor.execute(\"UPDATE v7_jobs SET unresolved_reason='brand_new_reason' WHERE id=%s\")")
        self.assertEqual(found, {"brand_new_reason"})
        self.assertNotIn("brand_new_reason", codes.REASONS)
        self.assertEqual(codes.sql_reasons("unresolved_reason IN ('jd_thin', 'closed') AND x LIKE 'audit_%%'"), {"jd_thin", "closed"})


class FitFamiliesBuiltFromStrings(unittest.TestCase):
    def test_every_edge_status_and_refusal_reason_fit_reports_is_registered(self):
        for reason in edge.REASONS:
            self.assertIn("FIT_EDGE_REFUSED_" + reason, codes.CODES)
        for status in edge.STATUSES:
            self.assertIn("FIT_EDGE_" + status, codes.CODES)
        self.assertIn("FIT_EDGE_OFF_CONTRACT", codes.CODES)


if __name__ == "__main__":
    unittest.main()
