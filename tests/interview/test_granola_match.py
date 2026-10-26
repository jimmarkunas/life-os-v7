import unittest

from lifeos.interview import granola_match as gm

ROW = gm.Round("page-1", "2026-10-13", "Becton Dickinson", "Senior Technical Program Manager", "Dana Reyes", "REQ-4471", "bd.com", "insightglobal.com")


def meeting(mid="m1", start="2026-10-13T17:30:00Z", title="", description="", attendees=()):
    return gm.Meeting(mid, start, title, description, tuple(attendees))


class CentralDay(unittest.TestCase):
    def test_a_late_evening_central_meeting_is_still_that_central_day(self):
        self.assertEqual(gm.central_day("2026-10-14T02:30:00Z"), "2026-10-13")        # 9:30 PM CDT
        self.assertEqual(gm.central_day("2026-10-13T17:30:00Z"), "2026-10-13")        # 12:30 PM CDT

    def test_missing_or_naive_times_are_none(self):
        self.assertIsNone(gm.central_day(""))
        self.assertIsNone(gm.central_day("2026-10-13T12:30:00"))


class Reasons(unittest.TestCase):
    def test_company_in_the_title(self):
        self.assertEqual(gm.reasons(meeting(title="Becton Dickinson follow-up"), ROW), {"company"})

    def test_a_staffing_firm_title_alone_is_not_the_hiring_company(self):
        self.assertEqual(gm.reasons(meeting(title="Insight Global follow-up sync"), ROW), set())

    def test_requisition_or_two_role_words(self):
        self.assertEqual(gm.reasons(meeting(description="Re REQ-4471"), ROW), {"role"})
        self.assertEqual(gm.reasons(meeting(title="Technical Program chat"), ROW), {"role"})
        self.assertEqual(gm.reasons(meeting(title="Program chat"), ROW), set())            # one word is too weak

    def test_interviewer_by_name_or_email(self):
        self.assertEqual(gm.reasons(meeting(attendees=["Dana Reyes"]), ROW), {"interviewer"})
        self.assertEqual(gm.reasons(meeting(attendees=["dana.reyes@example.com"]), ROW), {"interviewer"})

    def test_company_or_agency_domain(self):
        self.assertEqual(gm.reasons(meeting(attendees=["pat@InsightGlobal.com"]), ROW), {"domain"})
        self.assertEqual(gm.reasons(meeting(attendees=["pat@bd.com"]), ROW), {"domain"})
        self.assertEqual(gm.reasons(meeting(attendees=["pat@elsewhere.com"]), ROW), set())


class Match(unittest.TestCase):
    def test_one_meeting_one_round_on_the_same_day_matches(self):
        out = gm.match([meeting(attendees=["pat@insightglobal.com"])], [ROW])
        self.assertEqual(out, {"m1": ("MATCH", "page-1")})

    def test_the_wrong_day_never_matches_however_strong_the_signals(self):
        out = gm.match([meeting(start="2026-10-14T17:30:00Z", title="Becton Dickinson", attendees=["pat@bd.com"])], [ROW])
        self.assertEqual(out["m1"][0], "MATCH_NONE")

    def test_no_signal_is_no_match(self):
        self.assertEqual(gm.match([meeting(title="Lunch")], [ROW])["m1"], ("MATCH_NONE", None))

    def test_two_rounds_matching_one_meeting_is_ambiguous(self):
        other = gm.Round("page-2", "2026-10-13", "Becton Dickinson", "Other Role", "", "", "", "")
        out = gm.match([meeting(title="Becton Dickinson chat")], [ROW, other])
        self.assertEqual(out["m1"], ("MATCH_MANY_ROUNDS", None))

    def test_two_meetings_matching_one_round_is_ambiguous(self):
        out = gm.match([meeting("m1", title="Becton Dickinson a"), meeting("m2", "2026-10-13T19:00:00Z", title="Becton Dickinson b")], [ROW])
        self.assertEqual({k: v[0] for k, v in out.items()}, {"m1": "MATCH_MANY_MEETINGS", "m2": "MATCH_MANY_MEETINGS"})

    def test_an_unreadable_time_is_a_fixed_code(self):
        self.assertEqual(gm.match([meeting(start="soon")], [ROW])["m1"], ("MEETING_TIME_INVALID", None))
