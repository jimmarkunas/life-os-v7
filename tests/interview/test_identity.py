import unittest
from lifeos.interview.models import Child, Ownership, Parent, ParentQuery, Resolution, RoundQuery, Scan, State
from lifeos.interview.identity import resolve_parent, resolve_child, creation_eligibility


class IdentityTests(unittest.TestCase):
    def test_parent_contract(self):
        query = ParentQuery("Example", "Program Manager")
        for parents, complete, code in (
            ((Parent("a", "Example — Program Manager"),), True, "parent_match"),
            ((Parent("a", "Example — Engineer"),), True, "parent_not_found"),
            ((Parent("a", "Example — Program Manager"), Parent("b", "Example — Program Manager")), True, "parent_ambiguous"),
            ((Parent("a", "Example Program Manager"),), True, "identity_invalid"),
            ((Parent("a", "Example — Program Manager", False),), True, "parent_not_found"),
            ((), False, "pipeline_incomplete"),
        ):
            with self.subTest(code=code):
                self.assertEqual(resolve_parent(query, Scan(parents, complete)).code, code)
        self.assertEqual(resolve_parent(ParentQuery("", "Manager"), Scan((), True)).code, "identity_invalid")

    def test_children_never_cross_parent(self):
        """child_scan proves it (see test_notion: a foreign-parent child makes the scan incomplete)."""
        parent = Resolution(State.MATCH, "parent_match", "p")
        child = Child("c", "p", "2026-10-01", "Example Person", 1)
        other = Child("elsewhere", "other", child.interview_date, child.interviewer, 1)
        for query, scan, explicit, code in (
            (RoundQuery(True, explicit_child_page_id="c"), Scan((child,), True), child, "child_match"),
            (RoundQuery(True, explicit_child_page_id="elsewhere"), Scan((child,), True), other, "child_wrong_parent"),
            (RoundQuery(True, interview_date=child.interview_date), Scan((child, Child("c2", "p", child.interview_date)), True), None, "child_ambiguous"),
            (RoundQuery(True, ordinal=1), Scan((), False), None, "child_scan_incomplete"),
            (RoundQuery(True, ordinal=1), Scan((), True), None, "child_not_found"),
            (RoundQuery(True), Scan((), True), None, "round_identity_incomplete"),
        ):
            with self.subTest(code=code):
                self.assertEqual(resolve_child(parent, query, scan, explicit).code, code)

    def test_a_pre_screen_with_ordinal_zero_is_never_round_one_nor_ambiguous_with_it(self):
        """the Becton recruiter screen (ordinal 0, no interviewer in its identity) never matches, blocks or collides with
        the client's own rounds.
        """
        parent = Resolution(State.MATCH, "parent_match", "p")
        screen = Child("screen", "p", "2026-10-13", None, 0, True)
        real = Child("real", "p", "2026-11-02", "Jane Roe", 1, True)
        for query, scan, code, page in (
            (RoundQuery(True, ordinal=1), Scan((screen,), True), "child_not_found", None),
            (RoundQuery(True, interview_date=None, ordinal=1, source="https://mail.example/t"), Scan((screen,), True), "child_not_found", None),
            (RoundQuery(True, ordinal=1), Scan((screen, real), True), "child_match", "real"),
            (RoundQuery(True, interview_date="2026-11-02"), Scan((screen, real), True), "child_match", "real"),
            (RoundQuery(True, interviewer="Jane Roe"), Scan((screen, real), True), "child_match", "real"),
            (RoundQuery(True, interview_date="2026-10-13"), Scan((screen,), True), "child_match", "screen"),
        ):
            with self.subTest(code=code, page=page):
                got = resolve_child(parent, query, scan, None)
                self.assertEqual((got.code, got.page_id), (code, page))

    def test_an_explicit_lookup_still_finds_the_pre_screen_and_a_real_round_one_can_be_created_beside_it(self):
        parent = Resolution(State.MATCH, "parent_match", "p")
        screen = Child("screen", "p", "2026-10-13", None, 0, True)
        self.assertEqual(resolve_child(parent, RoundQuery(True, explicit_child_page_id="screen"), Scan((screen,), True), screen).code, "child_match")
        absent = resolve_child(parent, RoundQuery(True, interview_date="2026-11-02", ordinal=1, interviewer="Jane Roe"), Scan((screen,), True), None)
        self.assertEqual(absent.code, "child_not_found")
        self.assertEqual(creation_eligibility("target_ok", parent, Ownership.MACHINE, absent, Scan((screen,), True), RoundQuery(True, "2026-11-02", "Jane Roe", 1)), "create_allowed")
        self.assertEqual(creation_eligibility("target_ok", parent, Ownership.MACHINE, absent, Scan((screen,), True), RoundQuery(True, "2026-11-02", None, 0)), "round_identity_incomplete")  # an invite never makes ordinal 0

    def test_creation_gate(self):
        parent = Resolution(State.MATCH, "parent_match", "p")
        missing = Resolution(State.NOT_FOUND, "child_not_found")
        for query, owner, complete, code in (
            (RoundQuery(False), Ownership.MACHINE, True, "round_unconfirmed"),
            (RoundQuery(True, ordinal=1), Ownership.MACHINE, True, "round_date_missing"),
            (RoundQuery(True, "2026-10-01"), Ownership.MACHINE, True, "round_identity_incomplete"),
            (RoundQuery(True, "2026-10-01", ordinal=1), Ownership.MACHINE, True, "create_allowed"),
            (RoundQuery(True, "2026-10-01", ordinal=1), Ownership.HUMAN, True, "human_page"),
            (RoundQuery(True, "2026-10-01", ordinal=1), Ownership.UNKNOWN, True, "ownership_unknown"),
            (RoundQuery(True, "2026-10-01", ordinal=1), Ownership.MACHINE, False, "child_scan_incomplete"),
        ):
            with self.subTest(code=code):
                self.assertEqual(creation_eligibility("target_ok", parent, owner, missing, Scan((), complete), query), code)
        self.assertNotEqual(creation_eligibility("target_mismatch", parent, Ownership.MACHINE, missing, Scan((), True), RoundQuery(True)), "create_allowed")

    def test_unknown_existing_round_identity_blocks_missing_claim(self):
        parent = Resolution(State.MATCH, "parent_match", "p")
        result = resolve_child(parent, RoundQuery(True, "2026-10-01", ordinal=1), Scan((Child("legacy", "p"),), True))
        self.assertEqual(result.code, "round_identity_incomplete")
        self.assertEqual(result.state, State.BLOCKED)

    def test_unrelated_malformed_parent_allows_valid_match(self):
        scan = Scan((Parent("bad", "Other Programme Manager"), Parent("good", "Example — Program Manager")), True)
        result = resolve_parent(ParentQuery("Example", "Program Manager"), scan)
        self.assertEqual((result.state, result.code, result.page_id), (State.MATCH, "parent_match", "good"))

    def test_unrelated_malformed_parent_allows_not_found(self):
        result = resolve_parent(ParentQuery("Example", "Engineer"), Scan((Parent("bad", "Other Engineer"),), True))
        self.assertEqual((result.state, result.code), (State.NOT_FOUND, "parent_not_found"))

    def test_relevant_malformed_parent_blocks_even_with_valid_match(self):
        for title in ("Example Global Programme Manager", "Example — "):
            with self.subTest(title=title):
                scan = Scan((Parent("good", "Example — Program Manager"), Parent("bad", title)), True)
                result = resolve_parent(ParentQuery("Example Global Ltd", "Program Manager"), scan)
                self.assertEqual((result.state, result.code), (State.BLOCKED, "identity_invalid"))
                self.assertIsNone(result.page_id)

    def test_malformed_title_never_supplies_guessed_role(self):
        result = resolve_parent(ParentQuery("Example", "Engineer"), Scan((Parent("bad", "Example Program Manager"),), True))
        self.assertEqual((result.state, result.code), (State.BLOCKED, "identity_invalid"))
        self.assertIsNone(result.page_id)

    def test_malformed_relevance_uses_tokens_not_substrings(self):
        result = resolve_parent(ParentQuery("Example", "Engineer"), Scan((Parent("bad", "Exampleton Engineer"),), True))
        self.assertEqual(result.code, "parent_not_found")

    def test_legacy_round_metadata_and_explicit_identity(self):
        parent = resolve_parent(ParentQuery("Example", "Program Manager"), Scan((Parent("p", "Example — Program Manager"),), True))
        self.assertEqual(parent.state, State.MATCH)
        legacy = Child("legacy", "p")
        scan = Scan((legacy,), True)
        for query in (RoundQuery(True, interview_date="2026-10-01"), RoundQuery(True, interviewer="Example Person"), RoundQuery(True, ordinal=1)):
            with self.subTest(query=query):
                result = resolve_child(parent, query, scan)
                self.assertEqual((result.state, result.code), (State.BLOCKED, "round_identity_incomplete"))
        explicit = RoundQuery(True, explicit_child_page_id="legacy")
        result = resolve_child(parent, explicit, scan, legacy)
        self.assertEqual((result.state, result.code), (State.MATCH, "child_match"))
        result = resolve_child(parent, explicit, scan, Child("legacy", "other"))
        self.assertEqual((result.state, result.code), (State.BLOCKED, "child_wrong_parent"))
