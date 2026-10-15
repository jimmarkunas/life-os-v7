"""LIFE-47 P1 slice A: the Close Opportunity server-side contract, composed with the real D194/D195 decision chain (tests/hiring/test_decision.Chain). Invented company, role and requisition only."""
import copy
import unittest
from pathlib import Path

from lifeos.hiring import close, decision as D
from lifeos.platform.notion_client import NotionError
from tests.hiring.test_decision import COMPANY, ROLE, SIBLING, TARGET, Chain, seen

KEY, USER, ALLOWED, NOW = b"unit-test-signing-key", "jim@example.com", {"jim@example.com"}, 1_000_000
JOB = {"id": TARGET, "company": COMPANY, "role": ROLE, "req": "DEMO-2046", "stage": "Interviewing"}


def form(body, **changes):
    return {"csrf": close.csrf(KEY, USER, body), "reason": "Rejected", "notes": "", "confirm": "yes", **changes}


def closing(job=JOB, now=NOW, user=USER, stage="Interviewing", **changes):
    body, mac = close.link(KEY, job, NOW)
    return close.closing(KEY, user, ALLOWED, body, mac, form(body, **changes), stage, now)


def code(call):
    with unittest.TestCase().assertRaises(NotionError) as error:
        call()
    return str(error.exception)


class LinkTests(unittest.TestCase):
    def test_a_link_names_the_exact_pursuit_and_carries_no_credential(self):
        body, mac = close.link(KEY, JOB, NOW)
        claims = close.opened(KEY, body, mac, NOW)
        self.assertEqual({k: claims[k] for k in JOB}, JOB)
        self.assertNotIn(KEY.decode(), body + mac)

    def test_opening_a_link_writes_nothing(self):
        chain = Chain()
        chain.project()
        chain.source.log.clear()
        body, mac = close.link(KEY, JOB, NOW)
        close.opened(KEY, body, mac, NOW)
        self.assertEqual((chain.source.log, chain.source.patches()), ([], []))

    def test_a_changed_wrong_foreign_or_expired_link_is_refused(self):
        body, mac = close.link(KEY, JOB, NOW)
        other = body.replace(TARGET, SIBLING)
        for args, want in (((KEY, other, mac, NOW), "DECISION_LINK_INVALID"), ((b"another-key", body, mac, NOW), "DECISION_LINK_INVALID"), ((KEY, body, "", NOW), "DECISION_LINK_INVALID"),
                           ((KEY, body, None, NOW), "DECISION_LINK_INVALID"), ((KEY, body, mac, NOW + 3 * 86400 + 1), "DECISION_LINK_EXPIRED")):
            self.assertEqual(code(lambda a=args: close.opened(*a)), want)


class ClosingTests(unittest.TestCase):
    def test_an_unauthorized_user_a_missing_or_wrong_csrf_or_no_confirmation_is_refused(self):
        self.assertEqual(code(lambda: closing(user="mallory@example.com")), "DECISION_FORBIDDEN")
        body, mac = close.link(KEY, JOB, NOW)
        for bad in ({"csrf": None}, {"csrf": "x"}, {"csrf": close.csrf(KEY, "mallory@example.com", body)}):
            self.assertEqual(code(lambda b=bad: close.closing(KEY, USER, ALLOWED, body, mac, form(body, **b), "Interviewing", NOW)), "DECISION_CSRF")
        for bad in ({"confirm": None}, {"confirm": "no"}, {"reason": "Fired"}, {"reason": None}):
            self.assertEqual(code(lambda b=bad: closing(**b)), "DECISION_REQUEST_INVALID")

    def test_notes_are_validated_and_other_needs_an_explanation(self):
        self.assertEqual(code(lambda: closing(reason="Other", notes="  ")), "DECISION_REQUEST_INVALID")
        self.assertEqual(code(lambda: closing(notes="x" * 201)), "DECISION_REQUEST_INVALID")
        self.assertEqual(closing(notes="x" * 200)["reason"], "x" * 200)
        self.assertEqual(closing()["reason"], "Rejected")

    def test_a_reason_the_canonical_record_cannot_carry_fails_closed_and_nothing_is_written(self):
        chain = Chain()
        for reason in ("Withdrew", "Position filled", "Declined offer", "Hired", "Other"):
            self.assertEqual(code(lambda r=reason: closing(reason=r, notes="why")), "DECISION_REASON_UNSUPPORTED")
        self.assertEqual(chain.source.patches(), [])

    def test_a_job_that_changed_since_the_link_was_made_is_a_conflict(self):
        self.assertEqual(code(lambda: closing(stage="Offer")), "DECISION_STALE_STATE")

    def test_a_confirmed_closing_is_written_verified_read_back_and_isolated_to_that_job(self):
        chain = Chain()
        chain.project()
        before = copy.deepcopy(chain.source.pages)
        done = chain.confirm(closing(notes="Employer email received"))
        self.assertEqual((done["status"], done["verified"], done["cleared"]), ("recorded", True, True))
        self.assertEqual(chain.source.decision(), {"name": "Rejected"})
        self.assertNotIn(f"{COMPANY} — {ROLE}", seen(chain.tree))
        self.assertEqual({k: v for k, v in chain.source.pages.items() if k != TARGET}, {k: v for k, v in before.items() if k != TARGET})

    def test_retrying_the_same_confirmation_is_idempotent_and_a_different_one_is_a_conflict(self):
        chain = Chain()
        first, again = chain.confirm(closing()), chain.confirm(closing())
        self.assertEqual((first["status"], again["status"], len(chain.source.patches())), ("recorded", "already_recorded", 1))
        with self.assertRaises(D.DecisionError) as error:
            chain.confirm(closing(notes="a different note"))
        self.assertEqual((str(error.exception), len(chain.source.patches())), ("DECISION_CONFLICT", 1))

    def test_a_link_for_one_job_cannot_close_another(self):
        chain = Chain()
        with self.assertRaises(D.DecisionError) as error:
            chain.confirm(closing(job={**JOB, "id": SIBLING}))
        self.assertEqual((str(error.exception), chain.source.patches()), ("DECISION_STALE_IDENTITY", []))

    def test_a_backend_failure_is_never_reported_as_success(self):
        chain = Chain()
        chain.source.fail_write = True
        with self.assertRaises(NotionError):
            chain.confirm(closing())
        self.assertEqual(chain.source.decision(), None)

    def test_the_contract_is_not_reachable_from_any_stage_or_workflow(self):
        root = Path(__file__).resolve().parents[2]
        for path in (root / ".github/workflows").glob("*.yml"):
            self.assertNotIn("hiring.close", path.read_text(), path.name)
        from lifeos import run as entry
        self.assertFalse([n for n, stage in entry.STAGES.items() if "hiring.close" in str(getattr(stage, "target", ""))])


if __name__ == "__main__":
    unittest.main()
