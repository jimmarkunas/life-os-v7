"""LIFE-10 §2: the read-only sandbox access check. A fake client records every call; nothing real is reached."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from lifeos import run as runner
from lifeos.platform.notion_client import Client, NotionError
from lifeos.sources import notion_sandbox_access as S


def refused(code, detail=""):
    error = NotionError(code)
    error.detail = detail
    return error


class Fake:
    """Answers the three reads; `refuse` maps a path prefix to the error that read raises. Any write method fails the test."""
    def __init__(self, refuse=None, rows=1):
        self.refuse, self.rows, self.calls = refuse or {}, rows, []

    def _answer(self, method, path, body=None):
        self.calls.append((method, path, body))
        for prefix, error in self.refuse.items():
            if path.startswith(prefix):
                raise error
        return {"results": [{"object": "page"}] * self.rows} if path.endswith("/query") else {"object": "x"}

    def call(self, method, path, body=None):
        return self._answer(method, path, body)

    def query_data_source(self, source_id=None, body=None):
        return self._answer("POST", f"/data_sources/{source_id}/query", body)

    def get_page(self, page_id):
        return self._answer("GET", f"/pages/{page_id}")

    def __getattr__(self, name):
        raise AssertionError(f"write path reached: {name}")


class SandboxAccess(unittest.TestCase):
    def test_reads_only_the_three_fixed_sandbox_targets_and_reports_200s(self):
        fake = Fake()
        done = S.run(0, False, environ={}, client=fake)
        self.assertEqual(done, {"notion_sandbox_access": "read_only", "data_source": "200", "query": "200", "rows": 1, "page": "200", "failed": 0})
        self.assertEqual(fake.calls, [("GET", "/data_sources/d364a2ba-194c-484d-b0e3-ebf838765145", None),
                                      ("POST", "/data_sources/d364a2ba-194c-484d-b0e3-ebf838765145/query", {"page_size": 1}),
                                      ("GET", "/pages/3fa3c5a0592681fb94bce9ebd20ee65d", None)])

    def test_an_unshared_sandbox_reports_notions_codes_and_counts_each_refused_read(self):
        missing = refused("NOTION_HTTP_404", "object_not_found")
        done = S.run(0, False, environ={}, client=Fake({"/data_sources/": missing, "/pages/": missing}))
        self.assertEqual(done, {"notion_sandbox_access": "read_only", "data_source": "NOTION_HTTP_404 object_not_found", "query": "NOTION_HTTP_404 object_not_found",
                                "rows": 0, "page": "NOTION_HTTP_404 object_not_found", "failed": 3})

    def test_live_is_refused_before_any_call(self):
        fake = Fake()
        with self.assertRaises(NotionError) as caught:
            S.run(0, True, environ={}, client=fake)
        self.assertEqual((str(caught.exception), fake.calls), ("NOTION_SANDBOX_ACCESS_READ_ONLY", []))

    def test_the_default_client_uses_the_jobs_token_and_the_sandbox_source_never_the_production_ledger(self):
        made = {}
        def capture(environ, **kw):
            made.update(environ=dict(environ), **kw)
            return Fake()
        with patch.object(S, "Client", side_effect=capture):
            S.run(0, False, environ={"NOTION_API_TOKEN": "t", "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "production"})
        self.assertEqual(made, {"environ": {"NOTION_API_TOKEN": "t", "SANDBOX_SOURCE": S.SANDBOX_SOURCE}, "source_name": "SANDBOX_SOURCE"})
        self.assertEqual(Client(made["environ"], source_name="SANDBOX_SOURCE").source, S.SANDBOX_SOURCE)

    def test_a_refused_read_fails_the_step_with_a_fixed_code_and_still_prints_the_codes(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(S, "Client", return_value=Fake({"/pages/": refused("NOTION_HTTP_404", "object_not_found")})), \
                patch.dict("os.environ", {"NOTION_API_TOKEN": "t"}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runner.main(["notion-sandbox-access", "--limit", "0"])
        self.assertEqual((code, err.getvalue()), (1, "NOTION-SANDBOX-ACCESS FAILED: NOTION_SANDBOX_ACCESS_DENIED\n"))
        printed = json.loads(out.getvalue().split(": ", 1)[1])
        self.assertEqual((printed["page"], printed["failed"]), ("NOTION_HTTP_404 object_not_found", 1))


if __name__ == "__main__":
    unittest.main()
