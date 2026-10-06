import unittest
from datetime import date, datetime, timedelta, timezone

from lifeos.megibow import classify as C, project as P, render as R, warnings as W
from lifeos.megibow.windows import CHI, visible_weeks, week_of, window_utc, elapsed_business_days

NOW = datetime(2026, 10, 7, 13, 0, tzinfo=CHI)  # Wednesday
TODAY = NOW.date()


def ctx(**kw):
    base = {"self": {"jim@example.com"}, "known": {"lensa"}, "excluded": set(), "overrides": {}, "contacts": {}, "sent_to": {}}
    base.update(kw)
    return base


def msg(i, to, sent=NOW, subject="hello", bulk=False):
    return {"id": i, "sent_at": sent, "to": to, "cc": [], "subject": subject, "snippet": "", "bulk": bulk}


def ev(uid, emails, start, created, title="Intro", response="accepted", **kw):
    e = {"uid": uid, "occ": f"{uid}@{start.isoformat()}", "created": created, "start": start, "end": start + timedelta(hours=1), "title": title, "preview": "",
         "cancelled": False, "all_day": False, "my_response": response, "attendees": [{"email": a, "response": "accepted"} for a in emails], "bridged": False}
    e.update(kw)
    return e


class Windows(unittest.TestCase):
    def test_week_boundary_chicago(self):
        late_sunday = datetime(2026, 10, 5, 4, 59, tzinfo=timezone.utc)  # Sunday 11:59 PM CDT
        self.assertEqual(week_of(late_sunday), date(2026, 9, 28))
        self.assertEqual(week_of(late_sunday + timedelta(minutes=2)), date(2026, 10, 5))

    def test_eight_weeks_and_dst(self):
        weeks = visible_weeks(date(2026, 11, 4))
        self.assertEqual(len(weeks), 8)
        self.assertEqual(weeks[-1], date(2026, 11, 2))
        start, end = window_utc(date(2026, 11, 4))
        self.assertEqual(end - start, timedelta(days=56) + timedelta(hours=1))  # clocks fall back in the window

    def test_elapsed(self):
        self.assertEqual(elapsed_business_days(NOW), 3)
        self.assertEqual(elapsed_business_days(NOW + timedelta(days=4)), 5)


class Messages(unittest.TestCase):
    def test_known_company_counts(self):
        o = C.message(msg("m1", ["pat@lensa.com"]), ctx())
        self.assertEqual((o["status"], o["activity"]), (C.COUNTED, C.OUTREACH))

    def test_exclusions(self):
        for m in (msg("a", ["noreply@lensa.com"]), msg("b", ["jim@example.com"]), msg("c", ["pat@lensa.com"], bulk=True), msg("d", ["pat@jobright.ai"], subject="invoice")):
            self.assertEqual(C.message(m, ctx())["status"], C.EXCLUDED)

    def test_plausible_goes_to_review(self):
        o = C.message(msg("m2", ["lee@jobright.ai"], subject="Introduction and career question"), ctx())
        self.assertEqual(o["status"], C.REVIEW)

    def test_override_applies(self):
        o = C.message(msg("m2", ["lee@jobright.ai"], subject="career"), ctx())
        done = C.message(msg("m2", ["lee@jobright.ai"], subject="career"), ctx(overrides={o["key"]: "Count as Outreach"}))
        self.assertEqual((done["status"], done["activity"]), (C.COUNTED, C.OUTREACH))
        gone = C.message(msg("m2", ["lee@jobright.ai"], subject="career"), ctx(overrides={o["key"]: "Exclude"}))
        self.assertEqual(gone["status"], C.EXCLUDED)

    def test_same_message_counts_once(self):
        a = C.message(msg("m1", ["pat@lensa.com"]), ctx())
        proj = P.project([a, dict(a)], TODAY)
        self.assertEqual(proj["counts"][date(2026, 10, 5)][C.OUTREACH], 1)


class Events(unittest.TestCase):
    start = NOW - timedelta(days=1, hours=2)

    def run_event(self, e, c=None, now=NOW):
        return C.event(e, c or ctx(), now)

    def test_scheduled_uses_created_week_and_reschedule_is_same_key(self):
        created = NOW - timedelta(days=9)
        a = self.run_event(ev("u1", ["pat@lensa.com"], NOW + timedelta(days=3), created))
        b = self.run_event(ev("u1", ["pat@lensa.com"], NOW + timedelta(days=6), created))
        self.assertEqual(a[0]["week"], week_of(created))
        self.assertEqual(a[0]["key"], b[0]["key"])
        self.assertEqual(a[0]["status"], C.COUNTED)

    def test_unknown_creation_is_review(self):
        o = self.run_event(ev("u2", ["pat@lensa.com"], NOW + timedelta(days=2), None))
        self.assertEqual((o[0]["status"], o[0]["reason"]), (C.REVIEW, "creation_time_unknown"))

    def test_bridged_cancelled_declined_and_alone(self):
        for kw in ({"bridged": True}, {"cancelled": True}, {"response": "declined"}):
            r = kw.pop("response", "accepted")
            o = self.run_event(ev("u3", ["pat@lensa.com"], self.start, NOW, response=r, **kw))
            self.assertEqual([x["status"] for x in o], [C.EXCLUDED])
        self.assertEqual(self.run_event(ev("u4", [], self.start, NOW))[0]["status"], C.EXCLUDED)

    def test_call_without_corroboration_is_review_occurrence(self):
        o = self.run_event(ev("u5", ["pat@lensa.com"], self.start, NOW - timedelta(days=3)))
        self.assertEqual((o[1]["status"], o[1]["reason"]), (C.REVIEW, "occurrence"))

    def test_company_recruiter_networking(self):
        sent = {"pat@lensa.com": [NOW - timedelta(hours=1)], "rec@lensa.com": [NOW - timedelta(hours=1)], "sam@jobright.ai": [NOW - timedelta(hours=1)]}
        company = self.run_event(ev("c1", ["pat@lensa.com"], self.start, NOW - timedelta(days=3)), ctx(sent_to=sent))
        self.assertEqual(company[1]["activity"], C.COMPANY)
        recruiter = self.run_event(ev("c2", ["rec@lensa.com"], self.start, NOW - timedelta(days=3), title="Recruiter screen"), ctx(sent_to=sent))
        self.assertEqual(recruiter[1]["activity"], C.RECRUITER)
        net = self.run_event(ev("c3", ["sam@jobright.ai"], self.start, NOW - timedelta(days=3), title="Networking coffee chat"), ctx(sent_to=sent))
        self.assertEqual(net[1]["activity"], C.NETWORKING)

    def test_ambiguous_contact_type(self):
        sent = {"sam@jobright.ai": [NOW - timedelta(hours=1)]}
        o = self.run_event(ev("c4", ["sam@jobright.ai"], self.start, NOW - timedelta(days=3), title="Job opportunity chat"), ctx(sent_to=sent))
        self.assertEqual(o[1]["reason"], "contact_type")

    def test_future_call_not_counted(self):
        o = self.run_event(ev("f1", ["pat@lensa.com"], NOW + timedelta(days=1), NOW))
        self.assertEqual(len(o), 1)


class Projection(unittest.TestCase):
    def test_cumulative_and_cutover(self):
        w0 = date(2026, 10, 5)
        frozen = {date(2026, 9, 28): {a: 1 for a in C.ACTIVITIES}}
        out = [{"status": C.COUNTED, "activity": C.OUTREACH, "week": w0, "reason": "", "key": "k1", "candidate": None}]
        legacy = {a: 10 for a in C.ACTIVITIES}
        p = P.project(out, TODAY, frozen=frozen, legacy=legacy, cutover=date(2026, 9, 28))
        self.assertIsNone(p["counts"][date(2026, 9, 21)])
        self.assertEqual(p["counts"][date(2026, 9, 28)][C.OUTREACH], 1)
        self.assertEqual(p["cumulative"][C.OUTREACH], 12)
        rows = R.table(p)
        self.assertEqual((len(rows), len(rows[0])), (7, 10))
        self.assertEqual(rows[1][0], "Jim — Total")
        self.assertEqual(rows[0][2:4], ["Aug 17", "Aug 24"])
        self.assertEqual(rows[0][-1], "Oct 5 (current)")

    def test_current_week_moves_down(self):
        out = [{"status": C.COUNTED, "activity": C.OUTREACH, "week": date(2026, 10, 5), "reason": "", "key": "k1", "candidate": None}]
        self.assertEqual(P.project(out, TODAY)["cumulative"][C.OUTREACH], 1)
        self.assertEqual(P.project([], TODAY)["cumulative"][C.OUTREACH], 0)


def counts(o=0, s=0, n=0, r=0, c=0):
    return {C.OUTREACH: o, C.SCHEDULED: s, C.NETWORKING: n, C.RECRUITER: r, C.COMPANY: c}


class Warnings(unittest.TestCase):
    thursday = datetime(2026, 10, 8, 10, 0, tzinfo=CHI)
    base = [counts(5, 2, 1, 1, 1)] * 7

    def test_no_meetings_and_forward_empty(self):
        out = W.evaluate(counts(), self.base, self.thursday, 0)
        self.assertEqual(out[:2], [W.NO_MEETINGS, W.FORWARD_EMPTY])

    def test_thin_only_thursday_plus(self):
        self.assertEqual(W.evaluate(counts(o=5, s=1), self.base, NOW, 1), [])
        self.assertIn(W.FORWARD_THIN, W.evaluate(counts(o=5, s=1), self.base, self.thursday, 1))

    def test_outreach_drop_pace(self):
        out = W.evaluate(counts(o=1, s=1), self.base, NOW, 3)
        self.assertEqual(out, [W.OUTREACH_DROP])

    def test_consuming_pipeline(self):
        out = W.evaluate(counts(o=5, s=1, n=2, c=1), self.base, self.thursday, 3)
        self.assertEqual(out, [W.CONSUMING])

    def test_trend_suppressed_with_short_history(self):
        self.assertEqual(W.evaluate(counts(o=0, s=1), self.base[:3], NOW, 3), [])

    def test_a_failed_source_says_so_and_never_claims_unresolved_review_items(self):
        out = W.evaluate(counts(), self.base, self.thursday, 0, degraded=True)
        self.assertEqual(out, [W.SOURCE_UNAVAILABLE])
        self.assertIn("temporarily unavailable", out[0])
        self.assertIn("last accepted counts retained", out[0])
        self.assertNotIn("Review queue", out[0])
        self.assertNotIn("unresolved", out[0])
        self.assertEqual(W.evaluate(counts(), self.base, self.thursday, 0, degraded=True, unresolved=5), [W.SOURCE_UNAVAILABLE])

    def test_two_or_more_unresolved_items_point_to_the_review_queue_one_does_not(self):
        self.assertIn(W.EVIDENCE, W.evaluate(counts(), self.base, self.thursday, 3, unresolved=2))
        self.assertIn("MegIBOW Review queue", W.EVIDENCE)
        self.assertNotIn(W.EVIDENCE, W.evaluate(counts(), self.base, self.thursday, 3, unresolved=1))
        self.assertNotIn(W.SOURCE_UNAVAILABLE, W.evaluate(counts(), self.base, self.thursday, 3, unresolved=2))

    def test_max_three_and_order(self):
        out = W.evaluate(counts(), self.base, self.thursday, 0, unresolved=2)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], W.EVIDENCE)

    def test_healthy_line(self):
        self.assertEqual(R.notes([]), W.HEALTHY)
        self.assertEqual(R.notes([], degraded=True), "")

    def test_status_lines(self):
        self.assertIn("Last refreshed Wed Oct 7, 1:00 PM CT", R.status(NOW))
        self.assertTrue(R.status(NOW, degraded=True).startswith("DEGRADED"))
        self.assertIn("Review needed — 2", R.status(NOW, review=2))


if __name__ == "__main__":
    unittest.main()


class CutoverDefaultTests(unittest.TestCase):
    def test_the_default_cutover_is_the_first_visible_week_so_no_visible_week_is_dashed(self):
        from datetime import date
        from lifeos.sources import megibow
        from lifeos.megibow.windows import visible_weeks
        self.assertEqual(date.fromisoformat(megibow.CUTOVER_DEFAULT), visible_weeks(date(2026, 10, 6))[0])
