import unittest

from pipeline import ats_match


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


if __name__ == "__main__":
    unittest.main()


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
