import unittest

from lifeos.jobs import guard, hiring_pipeline as hp
from lifeos.platform.notion_client import NotionError


BASE = {"Applied": {"checkbox": False}, "Applied On": {"date": None}, "Saturn Decision": {"select": None}}


class Page:
    def __init__(self, props=None, fail=False):
        self.props, self.fail = {**BASE, **(props or {})}, fail

    def call(self, method, path, body=None):
        if self.fail:
            raise NotionError("NOTION_HTTP_500")
        return {"properties": self.props}


OPP = [hp.Opportunity("o1", "Acme — Program Manager", hp.ACTIVE, 0, "Acme", "Program Manager")]


class Guard(unittest.TestCase):
    def test_each_human_state_protects(self):
        self.assertEqual(guard.protection(Page({"Applied": {"checkbox": True}}), "p", "X", "Y"), "applied")
        self.assertEqual(guard.protection(Page({"Applied On": {"date": {"start": "2026-09-01"}}}), "p", "X", "Y"), "applied_on")
        self.assertEqual(guard.protection(Page({"Saturn Decision": {"select": {"name": "Pursue"}}}), "p", "X", "Y"), "saturn_decision")
        self.assertEqual(guard.protection(Page(), "p", "Acme Inc", "Programme Manager", OPP), "hiring_pipeline")

    def test_unreadable_fails_closed_and_nothing_to_protect_is_none(self):
        self.assertEqual(guard.protection(Page(fail=True), "p", "X", "Y"), "unreadable")
        self.assertIsNone(guard.protection(Page({"Applied": {"checkbox": False}, "Saturn Decision": {"select": None}}), "p", "X", "Y", OPP))


class AuditNeverDestroysAPursuit(unittest.TestCase):
    def test_audit_consults_the_guard_before_trashing(self):
        import inspect
        from lifeos.jobs import audit
        source = inspect.getsource(audit.run)
        self.assertLess(source.index("guard.protection"), source.index('"in_trash"'))     # no trash call can precede the check


class ProvenLinkTests(unittest.TestCase):
    def test_a_title_proven_job_is_not_demoted_for_an_ambiguous_link_shape(self):
        from lifeos.jobs import audit
        text = "Responsibilities: build pipelines and own data quality across the platform. You will need experience with SQL and Python skills. " * 6
        url = "https://careers-acme.icims.com/jobs/intro"
        self.assertEqual(audit.judge(url, text), "audit_url_no_job_id")
        self.assertIsNone(audit.judge(url, text, proven=True))
        thin = "A short description of a role. " * 5
        self.assertEqual(audit.judge("https://careers.example.com/jobs/12345", thin), "audit_jd_thin")
        self.assertIsNone(audit.judge("https://careers.example.com/jobs/12345", thin, thin_ok=True))               # Jim, Oct 21: a Scale-Up sponsor's thin page is Review
        self.assertEqual(audit.judge("https://careers.example.com/jobs/12345", "{{x}} {{y}} {{z}} " * 5, thin_ok=True), "audit_jd_template")     # only THIN is let through
        self.assertEqual(audit.judge("https://careers-acme.icims.com/", text, proven=True), "audit_url_root")      # a root is never excused


class DuplicatePageTests(unittest.TestCase):
    def test_later_pages_for_the_same_opening_are_counted_and_the_earliest_stays(self):
        self.assertEqual(self.audit([(1, "PUBLISHED", "https://x.example/1", "p1", None, "ServiceNow", "Director, TA Infrastructure"),
                                     (2, "PUBLISHED", "https://x.example/2", "p2", None, "ServiceNow", "Director, TA Infrastructure"),
                                     (3, "PUBLISHED", "https://x.example/3", "p3", None, "ServiceNow", "Director, TA Infrastructure"),
                                     (4, "PUBLISHED", "https://x.example/4", "p4", None, "Adobe", "Product Manager"),
                                     (5, "PUBLISHED", "https://x.example/5", "p5", None, "Adobe", "Product Manager")]), 2)       # ids 2 and 3; two-word titles never merge

    def test_pages_whose_links_name_different_vacancies_are_never_duplicates_and_one_vacancy_still_is(self):     # D205
        gh = "https://www.acme.example/careers?gh_jid={}"
        self.assertEqual(self.audit([(1, "PUBLISHED", gh.format(1001), "p1", None, "Acme", "Senior Technical Program Manager"),
                                     (2, "PUBLISHED", gh.format(2002), "p2", None, "Acme", "Senior Technical Program Manager"),
                                     (3, "PUBLISHED", "https://boards.greenhouse.io/acme/jobs/2002", "p3", None, "Acme", "Senior Technical Program Manager"),
                                     (4, "PUBLISHED", "https://www.linkedin.com/jobs/view/44", "p4", None, "Acme", "Senior Technical Program Manager")]), 1)   # only 3 (= 2): 4 has no id, so not shown
        self.assertEqual(self.audit([(1, "PUBLISHED", "https://careers.acme.example/a", "p1", None, "Acme", "Senior Technical Program Manager"),
                                     (2, "PUBLISHED", "https://careers.acme.example/b", "p2", None, "Acme", "Senior Technical Program Manager")]), 1)   # no ids: D90 as before

    def test_an_aggregator_page_at_an_ats_vacancys_place_goes_whichever_came_first(self):     # LIFE-64: Clarus, Oct 24
        wd = "https://independencepetgroup.wd12.myworkdayjobs.com/clarus/job/Chicago-IL/Project-Manager---Advanced_JR102945"
        li = "https://www.linkedin.com/jobs/view/4482175447"
        pages = [(1, "PUBLISHED", li, "p1", None, "Clarus Technology", "Project Manager - Advanced"),         # the LinkedIn page came first
                 (2, "PUBLISHED", wd, "p2", None, "Clarus Technology", "Project Manager - Advanced")]
        from lifeos.jobs import audit
        group = {("clarus technology", "project manager advanced"): pages}
        self.assertEqual([(r[0], twin) for r, twin in audit.twins(group, {1: "Chicago, IL", 2: "Remote / Enterprise Software"}, set())], [(1, 2)])   # the aggregator page goes
        self.assertEqual(audit.twins(group, {1: "Austin, TX", 2: "Remote"}, set()), [])
        self.assertEqual(audit.twins(group, {1: "Remote", 2: "Remote"}, set()), [])
        self.assertEqual(audit.twins(group, {1: "Chicago, IL"}, {2}), [])                     # the ATS page is itself going: nothing is kept in its place
        self.assertEqual(self.audit(pages, {1: "Chicago, IL", 2: "Remote"}), 1)                # and the run counts it
        self.assertEqual(audit.twins(group, {1: "Chicago, IL"}, {1}), [])                     # a page already going (e.g. excluded by Fit) is not counted twice

    def audit(self, pages, places=None):
        from unittest import mock
        from lifeos.jobs import audit

        pages = list(pages)

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, args=()):
                self.rows = pages if "notion_page_id IS NOT NULL ORDER BY id" in sql and "status='PUBLISHED'" in sql and "NULL, company" in sql else []
                if sql.startswith("SELECT id, location_text"):
                    self.rows = list((places or {}).items())

            def fetchall(self):
                return self.rows

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cur()

        with mock.patch.object(audit.store, "connect", return_value=Conn()), mock.patch.object(audit.store, "ensure_schema"):
            counts = audit.run(100, False, environ={})
        self.assertEqual(counts["failed"], counts["by_reason"].get("duplicate_page", 0))
        return counts["by_reason"].get("duplicate_page", 0)


class PageCapTests(unittest.TestCase):
    def test_a_large_excluded_pile_is_trashed_a_bounded_slice_per_run_and_the_rest_counted(self):     # D246
        from unittest import mock
        from lifeos.jobs import audit
        from lifeos.platform import limits

        excluded = [(i, "PUBLISHED", f"https://x.example/{i}", f"p{i}", None, f"Co{i}", "Program Manager") for i in range(1, 101)]

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, args=()):
                self.rows = excluded if "f.admission='EXCLUDE'" in sql else []

            def fetchall(self):
                return self.rows

        class Conn(Cur):
            def cursor(self):
                return Cur()

        client = mock.Mock()
        with mock.patch.object(audit.store, "connect", return_value=Conn()), mock.patch.object(audit.store, "ensure_schema"), \
                mock.patch.object(audit.notion_client, "Client", return_value=client), mock.patch.object(audit.ledger, "verify", return_value=audit.ledger.OK), \
                mock.patch.object(audit.guard, "protection", return_value=None):
            counts = audit.run(2000, True, environ={"V7_FIT_GATE": "true"})
        self.assertEqual((counts["excluded_waiting"], counts["trashed"], counts["deferred"]), (100, limits.AUDIT_PAGES_PER_RUN, 100 - limits.AUDIT_PAGES_PER_RUN))
        self.assertEqual(counts["excluded_cleared"], limits.AUDIT_PAGES_PER_RUN)
        self.assertEqual(client.call.call_count, limits.AUDIT_PAGES_PER_RUN)


class AlreadyTrashedPageTests(unittest.TestCase):
    """A page deleted by hand refuses every write, so its trash call failed on every run and its row never cleared."""

    def _run(self, page_after_error):
        from unittest import mock
        from lifeos.jobs import audit

        rows = [(1, "PUBLISHED", "https://x.example/1", "p1", None, "Co", "Program Manager")]

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, args=()):
                self.rows = rows if "f.admission='EXCLUDE'" in sql else []

            def fetchall(self):
                return self.rows

        class Conn(Cur):
            def cursor(self):
                return Cur()

        client = mock.Mock()

        def call(method, path, body=None):
            if method == "PATCH":
                raise NotionError("NOTION_HTTP_400")
            if page_after_error == "unreadable":
                raise NotionError("NOTION_HTTP_500")
            return page_after_error

        client.call.side_effect = call
        with mock.patch.object(audit.store, "connect", return_value=Conn()), mock.patch.object(audit.store, "ensure_schema"), \
                mock.patch.object(audit.notion_client, "Client", return_value=client), mock.patch.object(audit.ledger, "verify", return_value=audit.ledger.OK), \
                mock.patch.object(audit.guard, "protection", return_value=None):
            return audit.run(100, True, environ={"V7_FIT_GATE": "true"})

    def test_a_page_already_in_the_trash_counts_as_trashed_and_the_row_is_cleared(self):
        counts = self._run({"in_trash": True})
        self.assertEqual((counts["trash_errors"], counts.get("already_trashed"), counts["excluded_cleared"]), (0, 1, 1))

    def test_an_open_or_unreadable_page_that_refuses_the_trash_stays_an_error_and_the_row_stays(self):
        for page in ({"in_trash": False, "archived": False}, "unreadable"):
            counts = self._run(page)
            self.assertEqual((counts["trash_errors"], counts.get("already_trashed"), counts.get("excluded_cleared")), (1, None, None))


class ReadyGateTests(unittest.TestCase):
    """LIFE-76: the gate before publish judges only the READY rows, with the same guards, and never reads or trashes a page; the full pass runs after publish."""
    def _run(self, ready_only):
        from unittest import mock
        from lifeos.jobs import audit
        text = "Responsibilities: build pipelines and own data quality across the platform. You will need experience with SQL and Python skills. " * 6
        ready = [(1, "READY", "https://careers-acme.icims.com/", None, text, "Acme", "Program Manager"),
                 (2, "READY", "https://careers.example.com/jobs/12345", None, text, "Beta", "Program Manager")]
        published = [(3, "PUBLISHED", "https://careers.example.com/", "p3", text, "Gamma", "Program Manager")]
        sql = []

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, statement, args=()):
                sql.append(statement)
                if "d.full_text" in statement:
                    self.rows = ready + ([] if "('READY')" in statement else published)
                elif "notion_page_id IS NOT NULL" in statement and "location_text" not in statement:
                    self.rows = published
                else:
                    self.rows = []

            def fetchall(self):
                return self.rows

        class Conn(Cur):
            def cursor(self):
                return Cur()

        client = mock.Mock()
        with mock.patch.object(audit.store, "connect", return_value=Conn()), mock.patch.object(audit.store, "ensure_schema"), \
                mock.patch.object(audit.notion_client, "Client", return_value=client) as made, \
                mock.patch.object(audit.ledger, "verify", return_value=audit.ledger.OK), mock.patch.object(audit.guard, "protection", return_value=None):
            counts = audit.run(2000, True, environ={"V7_FIT_GATE": "true"}, ready_only=ready_only)
        return counts, sql, made, client

    def test_the_gate_demotes_a_bad_ready_row_without_any_page_read(self):
        counts, sql, made, client = self._run(True)
        self.assertEqual((counts["checked"], counts["failed"], counts["demoted"], counts["trashed"]), (2, 1, 1, 0))
        self.assertEqual(counts["by_reason"], {"audit_url_root": 1})
        self.assertFalse(made.called)
        self.assertFalse(client.call.called)
        self.assertFalse(any("notion_page_id IS NOT NULL" in s or "f.admission" in s for s in sql))     # no page list, place list or Exclude pile is read
        self.assertTrue(any("d.full_text" in s and "IN ('READY')" in s for s in sql))             # and only READY descriptions

    def test_the_full_pass_still_judges_and_trashes_published_pages(self):
        counts, _sql, made, client = self._run(False)
        self.assertEqual((counts["checked"], counts["trashed"], counts["demoted"]), (3, 1, 2))
        self.assertTrue(made.called)
        client.call.assert_called_once_with("PATCH", "/pages/p3", {"in_trash": True})
