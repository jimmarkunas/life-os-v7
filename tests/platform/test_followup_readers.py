import unittest
from datetime import datetime, timedelta, timezone

from lifeos.platform import followup_readers as R

NOW = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)
OLD, NEW1, NEW2 = "2026-11-16T17:00:00+00:00", "2026-11-16T19:00:00+00:00", "2026-11-16T19:30:00Z"
BASE = "https://example.atlassian.net"

ROWS = {
    "gmail": [
        {
            "id": "m1",
            "link": "https://mail.example/m1",
            "at": NEW1,
            "subject": "Re: deliverable",
            "body": "BODY MUST NOT LEAVE",
            "to": "x@example.com",
        },
        {"id": "m0", "link": "https://mail.example/m0", "at": OLD},
    ],
    "outlook": [
        {"id": "o1", "link": "https://outlook.example/o1", "at": NEW1, "subject": "hello", "snippet": "NOR THIS"}
    ],
    "calendar": [
        {
            "id": "e1",
            "htmlLink": "https://cal.example/e1",
            "updated": NEW2,
            "summary": "Sync",
            "description": "NOR THIS",
            "attendees": ["a"],
        }
    ],
    "jira": [{"key": "LIFE-9", "fields": {"updated": NEW1, "summary": "A ticket", "description": "NOR THIS"}}],
    "todoist": [
        {
            "id": "t1",
            "url": "https://todoist.example/t1",
            "updated_at": NEW1,
            "content": "call back",
            "description": "NOR THIS",
        }
    ],
    "notion": [
        {
            "id": "n1",
            "url": "https://notion.example/n1",
            "last_edited_time": NEW1,
            "title": "Page",
            "properties": {"x": 1},
        }
    ],
    "interview_os": [
        {"id": "i1", "url": "https://interview.example/i1", "at": NEW1, "title": "Panel", "notes": "NOR THIS"}
    ],
}


def grant(source, tenant="personal", account="acct-1", **kw):
    return R.Grant(
        **{
            "source": source,
            "tenant": tenant,
            "account": account,
            "credential_ref": f"bws-{source}-{tenant}",
            "expires": NOW + timedelta(hours=1),
            **kw,
        }
    )


class Harness:
    def __init__(self, grants=None, rows=None, boom=None, audit_boom=False):
        self.grants = tuple(grants if grants is not None else [grant(s, t) for s in R.SOURCES for t in R.TENANTS])
        self.rows, self.receipts, self.fetched, self.boom, self.audit_boom = (
            rows if rows is not None else ROWS,
            [],
            [],
            boom or set(),
            audit_boom,
        )

    def audit(self, receipt):
        if self.audit_boom:
            raise OSError("cannot write")
        self.receipts.append(receipt)

    def fetcher(self, source):
        def fetch(ref, since):
            self.fetched.append((source, ref, since))
            if source in self.boom:
                raise RuntimeError("secret detail that must not appear")
            return self.rows[source]

        return fetch

    def read(self, source, tenant="personal", account="acct-1", since=SINCE, rows_override=None, rid="r1"):
        fetch = (lambda ref, s: rows_override) if rows_override is not None else self.fetcher(source)
        return R.read_delta(source, tenant, account, since, self.grants, fetch, self.audit, NOW, rid, jira_base=BASE)


class Deltas(unittest.TestCase):
    def test_each_source_returns_only_evidence_newer_than_the_accepted_checkpoint_with_minimal_fields(self):
        h = Harness()
        for source in R.SOURCES:
            d = h.read(source)
            self.assertEqual((d.status, d.reason), ("ok", "ok"), source)
            self.assertTrue(
                d.items
                and all(set(i) == {"source", "native_id", "link", "kind", "observed_at", "title"} for i in d.items),
                source,
            )
            self.assertNotIn("NOR THIS", repr(d.items))
            self.assertNotIn("BODY MUST NOT LEAVE", repr(d.items))
            self.assertTrue(all(i["link"].startswith("https://") for i in d.items))
        gm = h.read("gmail")
        self.assertEqual([i["native_id"] for i in gm.items], ["m1"])  # m0 is older than the checkpoint
        self.assertEqual(gm.next_checkpoint, datetime.fromisoformat(NEW1))
        self.assertEqual(gm.last_accepted, SINCE)

    def test_a_first_read_has_no_checkpoint_and_returns_everything_in_order(self):
        d = Harness().read("gmail", since=None)
        self.assertEqual([i["native_id"] for i in d.items], ["m0", "m1"])

    def test_an_empty_completed_read_is_ok_and_keeps_the_checkpoint(self):
        d = Harness().read("gmail", rows_override=[])
        self.assertEqual((d.status, d.items, d.next_checkpoint), ("ok", (), SINCE))

    def test_titles_are_cut_and_nothing_else_is_copied(self):
        rows = [{"id": "t", "url": "https://todoist.example/t", "updated_at": NEW1, "content": "x" * 500}]
        d = Harness().read("todoist", rows_override=rows)
        self.assertEqual(len(d.items[0]["title"]), R.TITLE_MAX)


class Degraded(unittest.TestCase):
    def test_a_failed_source_is_degraded_with_the_last_accepted_time_and_never_a_zero(self):
        h = Harness(boom={"jira"})
        d = h.read("jira")
        self.assertEqual(
            (d.status, d.reason, d.items, d.last_accepted, d.next_checkpoint),
            ("degraded", "fetch_failed", (), SINCE, None),
        )
        self.assertNotIn("secret detail", repr(h.receipts))  # the error message is never recorded

    def test_an_incomplete_or_truncated_answer_is_degraded(self):
        h = Harness()
        self.assertEqual(h.read("gmail", rows_override={"items": []}).reason, "listing_incomplete")
        self.assertEqual(
            h.read(
                "gmail",
                rows_override=[{"id": str(n), "link": "https://m/x", "at": NEW1} for n in range(R.MAX_ROWS + 1)],
            ).reason,
            "truncated",
        )

    def test_one_malformed_row_degrades_the_read_and_the_checkpoint_does_not_advance(self):
        rows = [
            {"id": "ok", "link": "https://mail.example/ok", "at": NEW1},
            {"id": "bad", "link": "http://not-https", "at": NEW1},
            {"id": "naive", "link": "https://x/y", "at": "2026-11-16T19:00:00"},
        ]
        d = Harness().read("gmail", rows_override=rows)
        self.assertEqual((d.status, d.reason, d.next_checkpoint), ("degraded", "row_invalid", None))
        self.assertEqual([i["native_id"] for i in d.items], ["ok"])

    def test_a_secret_in_the_output_degrades_the_read(self):
        rows = [{"id": "s", "link": "https://mail.example/s", "at": NEW1, "subject": "token: abcdefgh12345678"}]
        d = Harness().read("gmail", rows_override=rows)
        self.assertEqual((d.status, d.items), ("degraded", ()))

    def test_one_failing_source_does_not_hide_the_others(self):
        h = Harness(boom={"gmail"})
        reqs = [(s, "personal", "acct-1") for s in R.SOURCES]
        out = R.read_all(reqs, h.grants, {s: h.fetcher(s) for s in R.SOURCES}, h.audit, NOW, jira_base=BASE)
        self.assertEqual(out[("gmail", "personal", "acct-1")].status, "degraded")
        self.assertEqual([k[0] for k, d in out.items() if d.status == "ok"], [s for s in R.SOURCES if s != "gmail"])

    def test_a_source_with_no_reader_is_degraded_not_empty(self):
        h = Harness()
        out = R.read_all([("notion", "personal", "acct-1")], h.grants, {}, h.audit, NOW)
        self.assertEqual(
            (out[("notion", "personal", "acct-1")].status, out[("notion", "personal", "acct-1")].reason),
            ("degraded", "no_reader"),
        )


class Refusals(unittest.TestCase):
    def test_asana_is_never_read_and_nothing_is_fetched(self):
        h = Harness()
        d = h.read("asana")
        self.assertEqual((d.status, d.reason, h.fetched), ("refused", "asana_not_read", []))
        out = R.read_all([("asana", "personal", "acct-1")], h.grants, {"asana": h.fetcher("gmail")}, h.audit, NOW)
        self.assertEqual(out[("asana", "personal", "acct-1")].reason, "asana_not_read")
        self.assertEqual(h.fetched, [])

    def test_tgp_and_unknown_tenants_are_excluded_before_any_grant_lookup(self):
        h = Harness(grants=[grant("gmail", "tgp"), grant("gmail", "other")])
        self.assertEqual(h.read("gmail", tenant="TGP").reason, "tenant_excluded")
        self.assertEqual(h.read("gmail", tenant="other").reason, "unknown_tenant")
        self.assertEqual(h.fetched, [])

    def test_unknown_source_no_grant_revoked_and_expired_all_refuse_without_a_read(self):
        h = Harness()
        self.assertEqual(h.read("slack").reason, "unknown_source")
        self.assertEqual(h.read("gmail", account="other-account").reason, "no_grant")
        self.assertEqual(Harness(grants=[grant("gmail", revoked=True)]).read("gmail").reason, "grant_revoked")
        self.assertEqual(
            Harness(grants=[grant("gmail", expires=NOW - timedelta(seconds=1))]).read("gmail").reason, "grant_expired"
        )
        self.assertEqual(h.fetched, [])

    def test_personal_and_bytalos_never_share_a_grant_credential_or_result(self):
        h = Harness(grants=[grant("gmail", "personal")])
        self.assertEqual(
            h.read("gmail", tenant="bytalos").reason, "no_grant"
        )  # a personal grant never opens a bytalos read
        both = Harness()
        a, b = both.read("gmail", "personal"), both.read("gmail", "bytalos", rid="r2")
        self.assertEqual(
            [f[1] for f in both.fetched], ["bws-gmail-personal", "bws-gmail-bytalos"]
        )  # each tenant uses only its own credential name
        self.assertIsNot(a.items, b.items)

    def test_revoking_one_source_or_tenant_leaves_the_others_readable(self):
        h = Harness()
        grants = R.revoke(h.grants, source="gmail", tenant="personal")
        h.grants = grants
        self.assertEqual(h.read("gmail", "personal").reason, "grant_revoked")
        self.assertEqual(h.read("gmail", "bytalos").reason, "ok")
        self.assertEqual(h.read("jira", "personal").reason, "ok")

    def test_a_receipt_comes_first_and_if_it_cannot_be_written_nothing_is_read(self):
        h = Harness(audit_boom=True)
        with self.assertRaises(OSError):
            h.read("gmail")
        self.assertEqual(h.fetched, [])
        ok = Harness()
        ok.read("gmail")
        self.assertEqual([r["outcome"] for r in ok.receipts], ["allow", "ok"])
        self.assertEqual(set(ok.receipts[0]), {"t", "source", "tenant", "account", "request_id", "outcome", "reason"})

    def test_the_module_imports_nothing_that_can_write_or_reach_a_network(self):
        import ast
        import inspect

        imported = {
            n.module if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(ast.parse(inspect.getsource(R)))
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [n])
        }
        self.assertEqual(imported, {"re", "dataclasses", "datetime"})


if __name__ == "__main__":
    unittest.main()
