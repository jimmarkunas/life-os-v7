"""LIFE-87 (D229): Build resume on the private Close service, driven the way the Edge gate calls it. One synthetic Ledger job page, a stand-in resume worker and a
stand-in Notion upload; nothing is live and every name is invented."""
import base64
import html
import json
import os
import re
import socket
import tempfile
import threading
import unittest
from datetime import datetime
from unittest import mock

from lifeos.edge import close_gate
from lifeos.platform import notion_files
from lifeos.platform.notion_client import NotionError
from lifeos.sources import close_app, resume_app

SECRET, TOKEN, LEDGER = b"link-signing-key-0123456789abcdef", b"notion-token-secret", "11111111-2222-3333-4444-555555555555"
PID = "0a0b0c0d-1111-2222-3333-444455556666"
WHO = {"login": "jim@example.com", "node": "node-1"}
NOW = datetime(2026, 10, 20, 1, 40, tzinfo=close_app.TZ)
DOCX = b"PK\x03\x04" + b"0" * 400
FILENAME = "Jim_Markunas_Resume_Acme_TPM_V9.docx"


def rt(text):
    return [{"plain_text": text, "text": {"content": text}}]


class Page:
    """One open Ledger job page: its properties and the body V7 published (v7-jd marker, sections), then whatever is appended."""
    def __init__(self, decision=None, sections=True, lose_attachments=False, lose_column=False):
        self.props = {"Company": {"type": "rich_text", "rich_text": rt("Acme Grid")}, "Job": {"type": "title", "title": rt("Technical Program Manager")},
                      "Hiring Decision": {"type": "select", "select": {"name": decision} if decision else None},
                      "Location / Work Mode": {"type": "rich_text", "rich_text": rt("USA - Remote")}, "Resume": {"type": "files", "files": []}}
        body = [{"type": "paragraph", "paragraph": {"rich_text": rt("v7-jd:1 | key=abc")}}]
        if sections:
            body += [{"type": "heading_2", "heading_2": {"rich_text": rt("Summary")}},
                     {"type": "paragraph", "paragraph": {"rich_text": rt("We build digital twins of electricity grids for the largest utilities. " * 3)}},
                     {"type": "heading_2", "heading_2": {"rich_text": rt("Responsibilities")}},
                     {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt("Lead end-to-end delivery of complex enterprise programs.")}}]
        body += [{"type": "toggle", "toggle": {"rich_text": rt("Network Leads")}}, {"type": "paragraph", "paragraph": {"rich_text": rt("not part of the description")}}]
        self.blocks, self.appended, self.lose, self.lose_column = body, [], lose_attachments, lose_column
        self.source = LEDGER                                                        # the gate's ReadOnly facade names the client's data source

    def get_page(self, pid):
        return {"id": PID, "parent": {"data_source_id": LEDGER}, "properties": self.props}

    def call(self, method, path, body=None):
        assert method == "GET" and path.replace("-", "").startswith(f"/blocks/{PID.replace('-', '')}/children"), (method, path)
        return {"results": self.blocks + ([] if self.lose else self.appended), "has_more": False}

    def call_once(self, method, path, body=None):
        if path.replace("-", "") == f"/pages/{PID.replace('-', '')}":
            assert method == "PATCH" and set(body["properties"]) == {"Resume"}, body                  # the build writes no other property
            if not self.lose_column:
                self.props["Resume"] = {"type": "files", "files": [{"name": f["name"], "type": "file"} for f in body["properties"]["Resume"]["files"]]}
            return {}
        assert method == "PATCH" and path.replace("-", "") == f"/blocks/{PID.replace('-', '')}/children", (method, path)
        for child in body["children"]:
            self.appended.append({"type": "file", "file": {"type": "file", "name": child["file"]["name"], "caption": child["file"]["caption"]}})
        return {"results": body["children"]}


class Run:
    def __init__(self, page=None, reply=None):
        self.page, self.asked, self.uploads = page or Page(), [], []
        self.reply = reply if reply is not None else {"status": "OK", "filename": FILENAME, "docx": base64.b64encode(DOCX).decode()}

    def worker(self, payload):
        self.asked.append(payload)
        return self.reply

    def upload(self, token, filename, data, content_type):
        self.uploads.append((token, filename, data, content_type))
        return "upload-1"

    def call(self, method, query=None, form=None):
        req = {"method": method, "path": "/resume", "query": query or {}, "headers": {}, "body": b"", "form": form or {}}
        secrets = {"link": SECRET, "config": {"ledger": LEDGER}}
        if method == "GET":
            secrets["reader"] = close_gate.ReadOnly(self.page)
        else:
            secrets["notion"] = TOKEN

        def factory(cfg):
            assert method != "GET", "GET must not build a writing client"
            return self.page
        with mock.patch.object(resume_app, "_now", lambda: NOW), mock.patch.object(close_app, "_now", lambda: NOW), mock.patch.object(resume_app, "Client", factory), \
                mock.patch.object(resume_app, "ask_worker", self.worker), mock.patch.object(resume_app.notion_files, "upload", self.upload):
            status, _, body, _ = close_app.handle(req, WHO, secrets)
        return status, body.decode()

    def form(self):
        status, body = self.call("GET", query={"j": PID})
        assert status == 200, body
        return {k: html.unescape(v) for k, v in re.findall(r'type="hidden" name="(\w+)" value="([^"]*)"', body)}

    def build(self, **override):
        return self.call("POST", form={**self.form(), "step": "build", **override})


class BuildResume(unittest.TestCase):
    def test_the_job_page_shows_a_signed_build_button_and_get_writes_nothing(self):
        r = Run()
        status, body = r.call("GET", query={"j": PID})
        self.assertEqual(status, 200)
        self.assertIn("Build resume", body)
        self.assertIn('action="/resume"', body)
        self.assertEqual((r.asked, r.page.appended), ([], []))

    def test_build_sends_the_published_description_attaches_the_docx_and_reads_it_back(self):
        r = Run()
        status, body = r.build()
        self.assertEqual(status, 200, body)
        self.assertIn("Resume attached", body)
        job = r.asked[0]["job"]
        self.assertEqual((job["company"], job["title"], job["location"]), ("Acme Grid", "Technical Program Manager", "USA - Remote"))
        self.assertTrue(job["description"].startswith("Summary\nWe build digital twins"))
        self.assertIn("- Lead end-to-end delivery", job["description"])
        self.assertNotIn("Network Leads", job["description"])                        # the description stops where the published sections end
        self.assertEqual(r.uploads, [(TOKEN.decode(), FILENAME, DOCX, resume_app.DOCX)] * 2)        # one copy for the page, one for the Resume column
        self.assertEqual([f["name"] for f in r.page.props["Resume"]["files"]], [FILENAME])
        self.assertIn('href="https://www.notion.so/0a0b0c0d111122223333444455556666"', body)          # straight to the job's Ledger page
        self.assertIn("Open the job in Notion", body)
        self.assertEqual(r.page.appended[0]["file"]["name"], FILENAME)
        self.assertEqual(r.page.appended[0]["file"]["caption"][0]["text"]["content"], "Tailored resume · built Oct 20, 1:40 AM CT · LIFE OS")

    def test_a_tampered_or_expired_form_runs_nothing(self):
        r = Run()
        for override in ({"t": "0" * 64}, {"co": "Other Co"}, {"exp": "1"}, {"step": "confirm"}):
            status, _ = r.build(**override)
            self.assertEqual(status, 403, override)
        self.assertEqual(r.asked, [])

    def test_a_closed_job_or_one_without_its_description_runs_nothing(self):
        r = Run(Page(decision="Rejected"))
        status, body = r.call("GET", query={"j": PID})
        self.assertIn("JOB_CLOSED", body)
        r = Run(Page(sections=False))
        status, body = r.build()
        self.assertEqual(status, 409)
        self.assertIn("JD_MISSING", body)
        self.assertEqual(r.asked, [])

    def test_a_worker_refusal_is_named_and_nothing_is_attached(self):
        for reply, words in (({"status": "QUOTA_RESERVE"}, "50% reserve"), ({"status": "REJECTED", "codes": ["NUMBER_NOT_IN_EVIDENCE@summary:37"]}, "NUMBER_NOT_IN_EVIDENCE"),
                             ({"status": "WORKER_UNAVAILABLE"}, "WORKER_UNAVAILABLE")):
            r = Run(reply=reply)
            status, body = r.build()
            self.assertIn(status, (409, 500))
            self.assertIn(words, html.unescape(body))
            self.assertEqual((r.uploads, r.page.appended), ([], []))

    def test_a_malformed_reply_is_never_attached(self):
        for reply in ({"status": "OK", "filename": "resume.docx", "docx": base64.b64encode(DOCX).decode()},
                      {"status": "OK", "filename": FILENAME, "docx": base64.b64encode(b"not a docx").decode()},
                      {"status": "OK", "filename": FILENAME, "docx": "%%%"}):
            r = Run(reply=reply)
            status, body = r.build()
            self.assertEqual(status, 500)
            self.assertEqual(r.uploads, [])

    def test_success_is_shown_only_after_the_attachment_is_read_back(self):
        for page in (Page(lose_attachments=True), Page(lose_column=True)):
            r = Run(page)
            status, body = r.build()
            self.assertEqual(status, 500)
            self.assertIn("not read back", body)

    def test_a_second_build_replaces_the_column_and_keeps_both_files_on_the_page(self):
        r = Run()
        r.build()
        r.reply = {"status": "OK", "filename": "Jim_Markunas_Resume_Acme_TPM_V9b.docx", "docx": base64.b64encode(DOCX).decode()}
        r.build()
        self.assertEqual([f["name"] for f in r.page.props["Resume"]["files"]], ["Jim_Markunas_Resume_Acme_TPM_V9b.docx"])
        self.assertEqual([b["file"]["name"] for b in r.page.appended], [FILENAME, "Jim_Markunas_Resume_Acme_TPM_V9b.docx"])

    def test_the_route_and_its_modules_are_in_the_release(self):
        for module in ("lifeos/sources/resume_app.py", "lifeos/platform/notion_files.py"):
            self.assertIn(module, close_app.CLOSURE)


class Worker(unittest.TestCase):
    def test_one_json_line_each_way_over_the_private_socket(self):
        path = os.path.join(tempfile.mkdtemp(), "worker.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        got = []

        def serve():
            conn, _ = server.accept()
            data = b""
            while not data.endswith(b"\n"):
                data += conn.recv(4096)
            got.append(json.loads(data))
            conn.sendall(json.dumps({"status": "OK", "filename": FILENAME}).encode() + b"\n")
            conn.close()
        threading.Thread(target=serve).start()
        self.assertEqual(resume_app.ask_worker({"job": {"company": "Acme"}}, path=path, wait=5)["status"], "OK")
        self.assertEqual(got, [{"job": {"company": "Acme"}}])
        server.close()

    def test_no_worker_is_a_named_refusal(self):
        self.assertEqual(resume_app.ask_worker({}, path="/nonexistent/worker.sock", wait=1), {"status": "WORKER_UNAVAILABLE"})


class Upload(unittest.TestCase):
    class Opener:
        def __init__(self, *replies):
            self.replies, self.requests = list(replies), []

        def __call__(self, request, timeout=None):
            self.requests.append(request)
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            body = json.dumps(reply).encode()
            return mock.MagicMock(__enter__=lambda s: mock.Mock(read=lambda: body), __exit__=lambda *a: False)

    def test_create_then_send_the_bytes_as_multipart(self):
        opener = self.Opener({"id": "up-1"}, {"status": "uploaded"})
        self.assertEqual(notion_files.upload("tok", FILENAME, DOCX, "application/x-test", opener=opener), "up-1")
        create, send = opener.requests
        self.assertTrue(create.full_url.endswith("/file_uploads"))
        self.assertEqual(json.loads(create.data), {"mode": "single_part", "filename": FILENAME, "content_type": "application/x-test"})
        self.assertTrue(send.full_url.endswith("/file_uploads/up-1/send"))
        self.assertIn(DOCX, send.data)
        self.assertIn(f'filename="{FILENAME}"'.encode(), send.data)
        self.assertTrue(send.headers["Content-type"].startswith("multipart/form-data; boundary=lifeos-"))

    def test_failures_are_fixed_codes(self):
        import urllib.error
        for opener, code in ((self.Opener({"id": "up-1"}, {"status": "pending"}), "NOTION_UPLOAD_INCOMPLETE"), (self.Opener({}), "NOTION_UPLOAD_NOT_CREATED"),
                             (self.Opener(urllib.error.HTTPError("u", 403, "x", {}, None)), "NOTION_UPLOAD_HTTP_403"), (self.Opener(OSError()), "NOTION_UPLOAD_NETWORK")):
            with self.assertRaises(NotionError) as ctx:
                notion_files.upload("tok", FILENAME, DOCX, "application/x-test", opener=opener)
            self.assertEqual(str(ctx.exception), code)
        with self.assertRaises(NotionError):
            notion_files.upload("tok", FILENAME, b"", "x")
