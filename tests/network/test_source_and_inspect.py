"""The door (find and fetch the attached file) and the stage that measures it. Fakes only; no network, no real file, no real name."""
import io
import json
import unittest
import urllib.error
from datetime import date
from unittest.mock import patch

from lifeos import run
from lifeos.network import inspect, source
from lifeos.network.errors import NetworkError

PAGE = "3d73c5a0-5926-806f-866d-c92960349fe9"
OK_URL = "https://prod-files-secure.s3.us-west-2.amazonaws.com/x/y/LI_Connections_20260907.csv?sig=1"
CSV = ("First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
       "Ada,Example,https://www.linkedin.com/in/ada-example,,Acme,Director,05 Sep 2026\n"
       "Bo,Sample,https://www.linkedin.com/in/bo-sample,,Widget Co,Engineer,04 Sep 2026\n")


def file_block(name, url=OK_URL, kind="file", created="2026-09-28T00:00:00.000Z", block_type="file"):
    inner = {"type": kind, "name": name}
    inner[kind] = {"url": url}
    return {"type": block_type, "created_time": created, block_type: inner}


class FakeClient:
    """Serves pages of blocks to GET calls; any other method is a failure, so a write can never pass a test."""
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def call(self, method, path, body=None):
        assert method == "GET", f"unexpected {method}"
        self.calls.append(path)
        return self.pages[len(self.calls) - 1]


def page(results, more=False, cursor=None):
    return {"results": results, "has_more": more, "next_cursor": cursor}


class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return self.data if n < 0 else self.data[:n]


class FindTests(unittest.TestCase):
    def test_only_notion_hosted_csv_files_are_listed(self):
        client = FakeClient([page([
            file_block("LI_Connections_20260907.csv"),
            file_block("notes.pdf"),
            file_block("external.csv", kind="external"),
            {"type": "paragraph", "paragraph": {}},
        ])])
        blocks = source.csv_file_blocks(client, PAGE)
        self.assertEqual([b["name"] for b in blocks], ["LI_Connections_20260907.csv"])

    def test_pagination_is_followed_and_the_newest_file_wins(self):
        client = FakeClient([
            page([file_block("old_20260101.csv", created="2026-01-01T00:00:00.000Z")], more=True, cursor="c1"),
            page([file_block("new_20260907.csv", created="2026-09-07T00:00:00.000Z")]),
        ])
        blocks = source.csv_file_blocks(client, PAGE)
        self.assertEqual(source.newest(blocks)["name"], "new_20260907.csv")
        self.assertIn("start_cursor=c1", client.calls[1])

    def test_an_incomplete_listing_raises_instead_of_guessing(self):
        for bad in (page([], more=True, cursor=None), {"results": "x", "has_more": False}, "nope"):
            with self.assertRaises(NetworkError) as ctx:
                source.csv_file_blocks(FakeClient([bad]), PAGE)
            self.assertEqual(str(ctx.exception), "NETWORK_PAGE_INCOMPLETE")

    def test_a_malformed_page_id_is_refused_before_any_call(self):
        client = FakeClient([])
        with self.assertRaises(NetworkError):
            source.csv_file_blocks(client, "not-an-id")
        self.assertEqual(client.calls, [])

    def test_export_date_comes_from_the_file_name(self):
        self.assertEqual(source.export_date("LI_Connections_20260907.csv"), "2026-09-07")
        self.assertEqual(source.export_date("Connections-2026-09-07.csv"), "2026-09-07")
        self.assertIsNone(source.export_date("connections.csv"))


class DownloadTests(unittest.TestCase):
    def test_refuses_plain_http_and_foreign_hosts(self):
        for url in ("http://s3.amazonaws.com/a.csv", "https://example.com/a.csv", "https://evilamazonaws.com/a.csv"):
            with self.assertRaises(NetworkError) as ctx:
                source.download(url, opener=lambda *a, **k: Response(b""))
            self.assertEqual(str(ctx.exception), "NETWORK_FILE_URL_REFUSED")

    def test_returns_text_and_enforces_the_size_cap(self):
        self.assertEqual(source.download(OK_URL, opener=lambda *a, **k: Response(CSV.encode())), CSV)
        with patch.object(source, "MAX_FILE_BYTES", 10):
            with self.assertRaises(NetworkError) as ctx:
                source.download(OK_URL, opener=lambda *a, **k: Response(b"x" * 50))
            self.assertEqual(str(ctx.exception), "NETWORK_FILE_TOO_LARGE")

    def test_http_errors_become_fixed_codes_and_a_transient_one_is_retried_once(self):
        def err(code):
            return urllib.error.HTTPError(OK_URL, code, "x", {}, io.BytesIO(b""))
        calls = []
        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise err(503)
            return Response(CSV.encode())
        self.assertEqual(source.download(OK_URL, opener=flaky), CSV)
        with self.assertRaises(NetworkError) as ctx:
            source.download(OK_URL, opener=lambda *a, **k: (_ for _ in ()).throw(err(403)))
        self.assertEqual(str(ctx.exception), "NETWORK_FILE_HTTP_403")
        with self.assertRaises(NetworkError) as ctx:
            source.download(OK_URL, opener=lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")))
        self.assertEqual(str(ctx.exception), "NETWORK_FILE_NETWORK")


class InspectTests(unittest.TestCase):
    ENV = {"NETWORK_HANDOFF_PAGE_ID": PAGE, "NOTION_API_TOKEN": "t"}

    def run_with(self, blocks, opener=None, env=None):
        return inspect.run(40, False, environ=env or self.ENV, client=FakeClient([page(blocks)]),
                           opener=opener or (lambda *a, **k: Response(CSV.encode())), today=date(2026, 10, 4))

    def test_reports_counts_the_export_date_and_its_age(self):
        counts = self.run_with([file_block("LI_Connections_20260907.csv")])
        self.assertEqual((counts["rows"], counts["matchable_rows"], counts["csv_files_on_page"]), (2, 2, 1))
        self.assertEqual((counts["export_date"], counts["export_age_days"]), ("2026-09-07", 27))

    def test_output_never_carries_row_content(self):
        blob = json.dumps(self.run_with([file_block("LI_Connections_20260907.csv")])).lower()
        for secret in ("ada", "acme", "widget", "linkedin", "example", "http", "sig="):
            self.assertNotIn(secret, blob)

    def test_missing_configuration_or_file_fail_with_fixed_codes(self):
        with self.assertRaises(NetworkError) as ctx:
            inspect.run(40, False, environ={}, client=FakeClient([]))
        self.assertEqual(str(ctx.exception), "NETWORK_CONFIG_MISSING")
        with self.assertRaises(NetworkError) as ctx:
            self.run_with([file_block("notes.pdf")])
        self.assertEqual(str(ctx.exception), "NETWORK_NO_FILE")

    def test_the_stage_is_registered_and_a_failure_prints_a_fixed_code(self):
        self.assertIn("network-inspect", run.STAGES)
        with patch.dict(run.STAGES, {"network-inspect": lambda limit, live: (_ for _ in ()).throw(NetworkError("NETWORK_NO_FILE"))}):
            self.assertEqual(run.main(["network-inspect"]), 1)


if __name__ == "__main__":
    unittest.main()
