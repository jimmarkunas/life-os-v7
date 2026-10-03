import unittest

from lifeos.jobs.resolve import ats_match


class MatchTests(unittest.TestCase):
    jobs = [("greenhouse", "Senior Data Engineer", "https://boards.greenhouse.io/a/jobs/1", "New York, NY"),
            ("greenhouse", "Senior Data Engineer", "https://boards.greenhouse.io/a/jobs/2", "Austin, TX"),
            ("greenhouse", "Product Manager", "https://boards.greenhouse.io/a/jobs/3", "Remote")]

    def test_unique_title_matches(self):
        self.assertEqual(ats_match.pick(self.jobs, "product manager", "Denver, CO")[2], "https://boards.greenhouse.io/a/jobs/3")

    def test_same_title_is_disambiguated_by_location(self):
        self.assertEqual(ats_match.pick(self.jobs, "Senior Data Engineer", "Austin, Texas, United States")[2],
                         "https://boards.greenhouse.io/a/jobs/2")

    def test_ambiguous_or_missing_is_none(self):
        self.assertIsNone(ats_match.pick(self.jobs, "Senior Data Engineer", "Denver, CO"))
        self.assertIsNone(ats_match.pick(self.jobs, "Staff Data Engineer", "Austin, TX"))

    def test_reasons(self):
        self.assertEqual(ats_match.why([], "X", "")[0], "no_board")
        self.assertEqual(ats_match.why(self.jobs, "Staff Data Engineer", "")[0], "no_title")
        self.assertEqual(ats_match.why(self.jobs, "Senior Data Engineer", "Denver")[0], "ambiguous")
        self.assertEqual(ats_match.why(self.jobs, "Product Manager.", "")[0], "hit")

    def test_slugs(self):
        self.assertEqual(ats_match.slug_candidates("Acme Data Corp, Inc."), ["acmedata", "acme-data", "acme"])


class MoreBoardTests(unittest.TestCase):
    def _board(self, kind, payload):
        from unittest import mock
        with mock.patch.object(ats_match, "_json", lambda url: payload):
            return ats_match.board(kind, "acme")

    def test_each_new_board_shape_is_read(self):
        self.assertEqual(self._board("recruitee", {"offers": [{"title": "PM", "careers_url": "u", "city": "Austin", "country": "US"}]}),
                         [("PM", "u", "Austin US")])
        self.assertEqual(self._board("bamboohr", {"result": [{"id": 5, "jobOpeningName": "PM", "location": {"city": "Austin", "state": "TX"}}]}),
                         [("PM", "https://acme.bamboohr.com/careers/5", "Austin TX")])
        self.assertEqual(self._board("breezy", [{"name": "PM", "url": "u", "location": {"name": "Remote"}}]), [("PM", "u", "Remote")])
        self.assertEqual(self._board("pinpoint", {"data": [{"title": "PM", "url": "u", "location": {"name": "NYC"}}]}),
                         [("PM", "u", "NYC")])

    def test_workable_jobs_keep_the_account_in_the_url(self):
        from unittest import mock
        widget = {"jobs": [{"title": "PM", "shortcode": "AB12", "url": "https://apply.workable.com/j/AB12", "city": "Austin"}]}
        with mock.patch.object(ats_match, "_json", lambda url: widget):
            self.assertEqual(ats_match.board("workable", "acme")[0][1], "https://apply.workable.com/acme/j/AB12/")

    def test_missing_board_is_empty_not_an_error(self):
        for kind in ("recruitee", "bamboohr", "breezy", "pinpoint"):
            self.assertEqual(self._board(kind, None), [])

    def test_smartrecruiters_pages_until_a_short_page(self):
        from unittest import mock
        pages = iter([{"content": [{"id": str(i), "name": "J"} for i in range(100)]}, {"content": [{"id": "x", "name": "J"}]}])
        with mock.patch.object(ats_match, "_json", lambda url: next(pages)):
            self.assertEqual(len(ats_match.board("smartrecruiters", "acme")), 101)


class RobustnessTests(unittest.TestCase):
    def test_a_name_too_long_for_a_hostname_is_no_board_not_a_crash(self):
        from unittest import mock
        with mock.patch.object(ats_match, "fetch", side_effect=AssertionError("must not be requested")):
            self.assertEqual(ats_match.board("recruitee", "x" * 70), [])

    def test_one_failing_company_returns_empty(self):
        from unittest import mock
        with mock.patch.object(ats_match, "board", side_effect=RuntimeError("boom")):
            self.assertEqual(ats_match.boards_for("Acme Corp"), [])

class TitleNoiseTests(unittest.TestCase):
    """Lensa and LinkedIn decorate titles ("(Fully Remote)", "| Company | USA (Remote)"). Only noise that never changes the role is removed."""
    jobs = [("greenhouse", "Product Manager", "https://boards.greenhouse.io/a/jobs/3", "Remote"),
            ("greenhouse", "Senior Project Manager", "https://boards.greenhouse.io/a/jobs/4", "Remote"),
            ("greenhouse", "Staff Nurse", "https://boards.greenhouse.io/a/jobs/5", "Boston"),
            ("greenhouse", "Manager", "https://boards.greenhouse.io/a/jobs/7", "Remote"),
            ("greenhouse", "Client Project Manager", "https://boards.greenhouse.io/a/jobs/6", "Remote")]

    def test_remote_decoration_is_ignored(self):
        for title in ("Product Manager (Fully Remote)", "Remote Product Manager", "Product Manager - Remote", "Product Manager (Remote)"):
            self.assertEqual(ats_match.why(self.jobs, title, "")[0], "hit", title)

    def test_a_company_and_location_tail_is_ignored(self):
        self.assertEqual(ats_match.why(self.jobs, "Client Project Manager | FirstPoint Group | USA (Remote)", "")[0], "hit")

    def test_a_parenthetical_that_changes_the_role_is_kept(self):
        self.assertEqual(ats_match.why(self.jobs, "Staff Nurse (ICU)", "")[0], "no_title")        # not turned into the generic Staff Nurse posting
        self.assertEqual(ats_match.why(self.jobs, "Staff Nurse (Remote)", "")[0], "hit")

    def test_a_cleaned_title_of_one_word_is_never_trusted(self):
        self.assertEqual(ats_match.why(self.jobs, "Remote Manager", "")[0], "no_title")          # would shrink to the bare word Manager

    def test_a_different_level_still_does_not_match(self):
        self.assertEqual(ats_match.why(self.jobs, "Staff Project Manager (Remote)", "")[0], "no_title")
        self.assertEqual(ats_match.why(self.jobs, "Project Manager (Remote)", "")[0], "no_title")

    def test_the_title_as_written_is_always_tried_first(self):
        self.assertEqual(ats_match.title_variants("Product Manager")[0], "product manager")
        self.assertEqual(ats_match.title_variants("PM")[0], "pm")


if __name__ == "__main__":
    unittest.main()
