"""LIFE-87 (D234): Build resume on the private Close service, driven the way the Edge gate calls it. One synthetic Ledger job page, a stand-in resume worker and a
stand-in Notion upload; nothing is live and every name is invented."""
import base64
import hashlib
import html
import json
import os
import re
import socket
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime
from unittest import mock

from lifeos.edge import close_gate
from lifeos.platform import gdrive, notion_files
from lifeos.platform.notion_client import NotionError
from lifeos.sources import close_app, resume_app

SECRET, TOKEN, LEDGER = b"link-signing-key-0123456789abcdef", b"notion-token-secret", "11111111-2222-3333-4444-555555555555"
PID = "0a0b0c0d-1111-2222-3333-444455556666"
WHO = {"login": "jim@example.com", "node": "node-1"}
NOW = datetime(2026, 10, 20, 1, 40, tzinfo=close_app.TZ)
DOCX = b"PK\x03\x04" + b"0" * 400
FILENAME = "Jim_Markunas_Resume_Acme_TPM_V9.docx"
GDRIVE = b'{"client_id": "cid", "client_secret": "csecret", "refresh_token": "rtoken"}'


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
        self.blocks, self.appended, self.lose, self.lose_column, self.writes = body, [], lose_attachments, lose_column, []
        self.source = LEDGER                                                        # the gate's ReadOnly facade names the client's data source

    def get_page(self, pid):
        return {"id": PID, "parent": {"data_source_id": LEDGER}, "properties": self.props}

    def call(self, method, path, body=None):
        assert method == "GET" and path.replace("-", "").startswith(f"/blocks/{PID.replace('-', '')}/children"), (method, path)
        return {"results": self.blocks + ([] if self.lose else self.appended), "has_more": False}

    def call_once(self, method, path, body=None):
        if path.replace("-", "") == f"/pages/{PID.replace('-', '')}":
            assert method == "PATCH" and len(body["properties"]) == 1 and set(body["properties"]) <= {"Resume", resume_app.SUBMITTED_COLUMN}, body   # one column, nothing else
            if resume_app.SUBMITTED_COLUMN in body["properties"]:
                if not self.lose_column:
                    self.props[resume_app.SUBMITTED_COLUMN] = {"type": "files", "files": [dict(f) for f in body["properties"][resume_app.SUBMITTED_COLUMN]["files"]]}
                self.writes.append(body)
                return {}
            self.writes.append(body)
            if not self.lose_column:
                self.props["Resume"] = {"type": "files", "files": [{"name": f["name"], "type": "file"} if f["type"] == "file_upload" else dict(f)
                                                                   for f in body["properties"]["Resume"]["files"]]}
            return {}
        assert method == "PATCH" and path.replace("-", "") == f"/blocks/{PID.replace('-', '')}/children", (method, path)
        self.writes.append(body)
        for child in body["children"]:
            kept = {"external": child["file"]["external"]} if child["file"]["type"] == "external" else {}
            self.appended.append({"type": "file", "file": {"type": child["file"]["type"].replace("file_upload", "file"), "name": child["file"]["name"],
                                                           "caption": child["file"]["caption"], **kept}})
        return {"results": body["children"]}


class Run:
    def __init__(self, page=None, reply=None, drive=None):
        self.page, self.asked, self.uploads, self.drive = page or Page(), [], [], drive
        self.reply = reply if reply is not None else {"status": "OK", "filename": FILENAME, "docx": base64.b64encode(DOCX).decode()}

    def worker(self, payload):
        self.asked.append(payload)
        return self.reply

    def upload(self, token, filename, data, content_type):
        self.uploads.append((token, filename, data, content_type))
        return "upload-1"

    def call(self, method, query=None, form=None):
        req = {"method": method, "path": "/resume", "query": query or {}, "headers": {}, "body": b"", "form": form or {}}
        secrets = {"link": SECRET, "config": {"ledger": LEDGER}, **({"gdrive": GDRIVE} if self.drive is not None and method == "POST" else {})}
        if method == "GET":
            secrets["reader"] = close_gate.ReadOnly(self.page)
        else:
            secrets["notion"] = TOKEN

        def factory(cfg):
            assert method != "GET", "GET must not build a writing client"
            return self.page
        with mock.patch.object(resume_app, "_now", lambda: NOW), mock.patch.object(close_app, "_now", lambda: NOW), mock.patch.object(resume_app, "Client", factory), \
                mock.patch.object(resume_app, "ask_worker", self.worker), mock.patch.object(resume_app.notion_files, "upload", self.upload), \
                mock.patch.object(gdrive, "Drive", lambda raw: self.drive.bind(raw)):
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
        self.assertIn('value="build" data-wait="90" data-label="Building your résumé" data-late="If nothing appears by 3:30', body)   # Jim, Oct 25: a counting wait line, not a dead spinner
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


class FakeDrive:
    """A stand-in for gdrive.Drive: LIFE OS's own files only, as the drive.file scope sees them."""
    def __init__(self, lose=False, fail=None):
        self.files, self.folders, self.lose, self.fail, self.raw, self.locks, self.no_lock = {}, [], lose, fail, None, [], False

    def bind(self, raw):
        self.raw = raw
        return self

    def folder(self):
        if self.fail:
            raise gdrive.DriveError(self.fail)
        self.folders.append("folder-1")
        return "folder-1"

    def upload(self, folder_id, name, data, mime, tags):
        fid = f"file-{len(self.files) + 1}-0123456789"
        self.files[fid] = {"id": fid, "name": name, "md5Checksum": hashlib.md5(data).hexdigest(), "webViewLink": f"https://drive.google.com/file/d/{fid}/view",
                           "parents": [folder_id], "mimeType": mime, "appProperties": dict(tags), "trashed": False, "data": data}
        return dict(self.files[fid])

    def get(self, fid):
        return {**self.files[fid], "trashed": self.lose}

    def lock(self, fid, reason):
        self.locks.append((fid, reason))
        if not self.no_lock:
            self.files[fid]["contentRestrictions"] = [{"readOnly": True, "reason": reason}]
        return dict(self.files[fid])


class DriveBuild(unittest.TestCase):
    def test_with_the_drive_credential_the_docx_goes_to_drive_and_notion_links_it(self):
        r = Run(drive=FakeDrive())
        status, body = r.build()
        self.assertEqual(status, 200, body)
        self.assertIn("saved in Google Drive (My Drive / LIFE OS / Résumés)", body)
        self.assertEqual(r.uploads, [])                                                       # nothing is uploaded to Notion
        self.assertEqual(r.drive.raw, GDRIVE)
        stored = r.drive.files["file-1-0123456789"]
        self.assertEqual((stored["name"], stored["data"], stored["mimeType"], stored["parents"]), (FILENAME, DOCX, resume_app.DOCX, ["folder-1"]))
        self.assertEqual(stored["appProperties"], {"lifeos": "resume", "job": PID.replace("-", "")})
        link = stored["webViewLink"]
        self.assertEqual(r.page.props["Resume"]["files"], [{"type": "external", "external": {"url": link}, "name": FILENAME}])
        self.assertEqual((r.page.appended[0]["file"]["external"]["url"], r.page.appended[0]["file"]["name"]), (link, FILENAME))
        self.assertIn("Google Drive", r.page.appended[0]["file"]["caption"][0]["text"]["content"])

    def test_drive_success_is_shown_only_after_drive_the_page_and_the_column_read_back(self):
        for page, drive in ((Page(), FakeDrive(lose=True)), (Page(lose_attachments=True), FakeDrive()), (Page(lose_column=True), FakeDrive())):
            status, body = Run(page, drive=drive).build()
            self.assertEqual(status, 500)
            self.assertIn("not read back", body)

    def test_a_drive_failure_is_a_fixed_code_and_nothing_reaches_notion(self):
        r = Run(drive=FakeDrive(fail="GDRIVE_HTTP_403"))
        status, body = r.build()
        self.assertEqual(status, 500)
        self.assertIn("DEGRADED · GDRIVE_HTTP_403", body)
        self.assertEqual((r.page.appended, r.page.props["Resume"]["files"], r.uploads), ([], [], []))

    def test_a_narrow_grant_says_to_rerun_the_sign_in_and_attaches_nothing(self):
        r = Run(drive=FakeDrive(fail="GDRIVE_SCOPE_NARROW"))
        status, body = r.build()
        self.assertEqual(status, 500)
        self.assertIn("rerun it (docs/DRIVE_RESUMES.md)", body)
        self.assertEqual((r.page.appended, r.uploads), ([], []))

    def test_get_never_sees_the_drive_credential(self):
        r = Run(drive=FakeDrive())
        r.call("GET", query={"j": PID})
        self.assertIsNone(r.drive.raw)


class Applied(unittest.TestCase):
    """Jim, Oct 24: "I applied with this résumé" records which built résumé was sent: that Drive file is locked read-only and set as Submitted Résumé."""

    def built(self, **drive):
        r = Run(drive=FakeDrive(**drive))
        self.assertEqual(r.build()[0], 200)
        r.page.writes.clear()
        return r

    def offer(self, r):
        status, body = r.call("GET", query={"j": PID})
        self.assertEqual(status, 200, body)
        forms = re.findall(r'<form method="post" action="/resume">(.*?)</form>', body, re.S)
        return body, [{k: html.unescape(v) for k, v in re.findall(r'type="hidden" name="(\w+)" value="([^"]*)"', f)} for f in forms if 'name="a"' in f]

    def record(self, r, form):
        review = r.call("POST", form={**form, "step": "submit"})
        self.assertEqual(review[0], 200, review[1])
        fields = {k: html.unescape(v) for k, v in re.findall(r'type="hidden" name="(\w+)" value="([^"]*)"', review[1])}
        return review, r.call("POST", form={**fields, "step": "record"})

    def test_the_column_name_is_the_one_the_interview_page_reads(self):
        from lifeos.interview import view
        self.assertEqual(resume_app.SUBMITTED_COLUMN, view.SUBMITTED)

    def test_each_drive_build_is_offered_and_recording_locks_it_and_sets_submitted_resume(self):
        r = self.built()
        body, forms = self.offer(r)
        self.assertIn("I applied with this résumé", body)
        self.assertEqual([f["n"] for f in forms], [FILENAME])
        (review, done) = self.record(r, forms[0])
        self.assertIn("Record it", review[1])
        self.assertEqual(done[0], 200, done[1])
        self.assertIn("Submitted résumé recorded", done[1])
        link = r.drive.files["file-1-0123456789"]["webViewLink"]
        self.assertEqual(r.page.props[resume_app.SUBMITTED_COLUMN]["files"], [{"type": "external", "external": {"url": link}, "name": FILENAME}])
        self.assertEqual(r.drive.locks, [("file-1-0123456789", resume_app.LOCK_REASON)])
        self.assertEqual(len(r.page.writes), 1)                                                    # one property write; the review step wrote nothing
        body, forms = self.offer(r)
        self.assertIn("Recorded: " + FILENAME, body)
        self.assertEqual(forms, [])

    def test_a_replay_writes_nothing_and_a_different_recorded_file_is_refused(self):
        r = self.built()
        _, forms = self.offer(r)
        _, done = self.record(r, forms[0])
        fields = {**forms[0], "step": "record"}
        again = r.call("POST", form=fields)
        self.assertIn("already recorded as submitted", again[1])
        self.assertEqual((len(r.page.writes), len(r.drive.locks)), (1, 1))
        r.page.props[resume_app.SUBMITTED_COLUMN] = {"type": "files", "files": [{"type": "external", "external": {"url": "https://x"}, "name": "Other.docx"}]}
        refused = r.call("POST", form=fields)
        self.assertEqual(refused[0], 409)
        self.assertIn("already recorded", refused[1])
        self.assertEqual(len(r.drive.locks), 1)

    def test_tampered_forms_write_nothing(self):
        r = self.built()
        _, forms = self.offer(r)
        for patch in ({"f": "file-9-0123456789"}, {"n": "Other.docx"}, {"a": "bad"}, {"j": "0" * 32}, {"exp": "1"}, {"co": "Mallory"}):
            self.assertEqual(r.call("POST", form={**forms[0], **patch, "step": "record"})[0], 403, patch)
        self.assertEqual((r.page.writes, r.drive.locks), ([], []))

    def test_a_file_not_built_for_this_job_or_no_drive_credential_records_nothing(self):
        r = self.built()
        _, forms = self.offer(r)
        r.drive.files["file-1-0123456789"]["appProperties"]["job"] = "f" * 32
        status, body = r.call("POST", form={**forms[0], "step": "record"})
        self.assertEqual(status, 409)
        self.assertIn("not a résumé built for this job", body)
        r.drive.files["file-1-0123456789"]["appProperties"]["job"] = PID.replace("-", "")
        drive, r.drive = r.drive, None
        status, body = r.call("POST", form={**forms[0], "step": "record"})
        self.assertEqual(status, 409)
        self.assertIn("Google Drive is not connected", body)
        self.assertEqual((r.page.writes, drive.locks), ([], []))

    def test_success_only_after_the_lock_and_the_column_read_back(self):
        for setup in ("no_lock", "lose_column"):
            r = self.built()
            _, forms = self.offer(r)
            if setup == "no_lock":
                r.drive.no_lock = True
            else:
                r.page.lose_column = True
            status, body = r.call("POST", form={**forms[0], "step": "record"})
            self.assertEqual(status, 500, setup)
            self.assertIn("not read back", body)

    def test_a_drive_or_notion_failure_while_recording_is_a_fixed_code_and_says_retry_is_safe(self):
        r = self.built()
        _, forms = self.offer(r)

        def broken(*a):
            raise gdrive.DriveError("GDRIVE_HTTP_503")
        r.drive.lock = broken
        status, body = r.call("POST", form={**forms[0], "step": "record"})
        self.assertEqual(status, 500)
        self.assertIn("DEGRADED · GDRIVE_HTTP_503", body)
        self.assertIn("trying again is safe", body)
        self.assertEqual(r.page.writes, [])
        r = self.built()
        _, forms = self.offer(r)
        once = r.page.call_once

        def notion_down(method, path, body=None):
            if resume_app.SUBMITTED_COLUMN in ((body or {}).get("properties") or {}):
                raise NotionError("NOTION_HTTP_502")
            return once(method, path, body)
        r.page.call_once = notion_down
        status, body = r.call("POST", form={**forms[0], "step": "record"})
        self.assertEqual(status, 500)
        self.assertIn("NOTION_HTTP_502", body)
        self.assertEqual(len(r.drive.locks), 1)                                                    # locked, not recorded: the page says so and a retry is safe

    def test_a_trashed_renamed_or_unlinked_build_records_nothing(self):
        for change in ("trashed", "renamed", "unlinked"):
            r = self.built()
            _, forms = self.offer(r)
            stored = r.drive.files["file-1-0123456789"]
            if change == "trashed":
                r.drive.lose = True
            elif change == "renamed":
                stored["name"] = "Other.docx"
            else:
                r.page.appended.clear()
            status, _ = r.call("POST", form={**forms[0], "step": "record"})
            self.assertEqual(status, 409, change)
            self.assertEqual((r.page.writes, r.drive.locks), ([], []), change)

    def test_an_unreadable_page_still_shows_build(self):
        r = self.built()
        with mock.patch.object(resume_app, "recorded", side_effect=NotionError("NOTION_HTTP_500")):
            status, body = r.call("GET", query={"j": PID})
        self.assertEqual(status, 200)
        self.assertIn("Build resume", body)
        self.assertIn("Submitted résumé: unavailable this time", body)

    def test_a_refused_read_while_recording_names_the_record_step_not_a_build(self):
        r = self.built()
        _, forms = self.offer(r)
        with mock.patch.object(resume_app, "job", side_effect=NotionError("DECISION_NOT_FOUND")):
            status, body = r.call("POST", form={**forms[0], "step": "record"})
        self.assertEqual(status, 409)
        self.assertIn("A submitted résumé cannot be recorded for this job here", body)
        self.assertNotIn("cannot be built", body)

    def test_cancel_records_nothing(self):
        r = self.built()
        _, forms = self.offer(r)
        status, body = r.call("POST", form={**forms[0], "step": "cancel"})
        self.assertEqual(status, 200)
        self.assertIn("Nothing recorded", body)
        self.assertEqual((r.page.writes, r.drive.locks), ([], []))


class DriveClient(unittest.TestCase):
    class Opener:
        def __init__(self, *replies):
            self.replies, self.sent = list(replies), []

        def __call__(self, request, timeout=None):
            self.sent.append((request.get_method(), request.full_url, dict(request.header_items()), request.data))
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply

            class R:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    return False

                def read(s):
                    return json.dumps(reply).encode()
            return R()

    def test_the_credential_must_be_complete_and_is_never_echoed(self):
        for raw in (b"", b"{}", b'{"client_id": "a", "client_secret": "b"}', b'{"client_id": "a", "client_secret": "b", "refresh_token": ""}', b"[1]"):
            with self.assertRaises(gdrive.DriveError) as caught:
                gdrive.Drive(raw)
            self.assertEqual(str(caught.exception), "GDRIVE_CONFIG_INVALID")

    def test_the_folder_is_my_drive_life_os_resumes_found_or_created_inside_it_once(self):
        opener = self.Opener({"access_token": "at", "scope": gdrive.SCOPE}, {"files": [{"id": "home-1"}]}, {"files": []}, {"id": "folder-9"},
                             {"files": [{"id": "home-1"}]}, {"files": [{"id": "folder-9"}]})
        drive = gdrive.Drive(GDRIVE, opener)
        self.assertEqual(drive.folder(), "folder-9")
        self.assertEqual(drive.folder(), "folder-9")
        token, home, search, made, home2, again = opener.sent
        self.assertEqual(token[1], gdrive.TOKEN_URL)
        self.assertNotIn("Authorization", token[2])
        self.assertIn(b"refresh_token=rtoken", token[3])
        self.assertIn("name%3D%27LIFE+OS%27+and+%27root%27+in+parents", home[1])                          # exactly the LIFE OS folder at the top of My Drive
        self.assertIn("%27home-1%27+in+parents", search[1])
        self.assertIn(urllib.parse.quote_plus("name='Re\u0301sume\u0301s'"), search[1])                      # the decomposed spelling a Mac may store matches too
        self.assertEqual(home[2]["Authorization"], "Bearer at")
        self.assertEqual(json.loads(made[3]), {"name": "Résumés", "mimeType": gdrive.FOLDER_MIME, "parents": ["home-1"], "appProperties": {"lifeos": "resumes"}})
        self.assertEqual(again[0], "GET")

    def test_no_or_two_life_os_folders_or_a_narrow_grant_are_refused_before_anything_is_made(self):
        for homes, code in (([], "GDRIVE_LIFE_OS_FOLDER_MISSING"), ([{"id": "a"}, {"id": "b"}], "GDRIVE_LIFE_OS_FOLDER_AMBIGUOUS")):
            opener = self.Opener({"access_token": "at"}, {"files": homes})
            with self.assertRaises(gdrive.DriveError) as caught:
                gdrive.Drive(GDRIVE, opener).folder()
            self.assertEqual(str(caught.exception), code)
            self.assertEqual([m for m, *_ in opener.sent], ["POST", "GET"])                                   # the token, one search, nothing created
        opener = self.Opener({"access_token": "at", "scope": "https://www.googleapis.com/auth/drive.file"})
        with self.assertRaises(gdrive.DriveError) as caught:
            gdrive.Drive(GDRIVE, opener).folder()
        self.assertEqual(str(caught.exception), "GDRIVE_SCOPE_NARROW")                                         # the pre-"full" grant: rerun the sign-in

    def test_an_upload_is_the_docx_itself_and_its_checksum_must_match(self):
        good = {"id": "f1", "md5Checksum": hashlib.md5(DOCX).hexdigest(), "webViewLink": "https://drive.google.com/file/d/f1/view"}
        opener = self.Opener({"access_token": "at"}, good)
        meta = gdrive.Drive(GDRIVE, opener).upload("folder-9", FILENAME, DOCX, resume_app.DOCX, {"lifeos": "resume"})
        self.assertEqual(meta["id"], "f1")
        method, url, headers, body = opener.sent[1]
        self.assertTrue(url.startswith(gdrive.UPLOAD + "?uploadType=multipart"))
        self.assertTrue(headers["Content-type"].startswith("multipart/related; boundary=lifeos-"))
        self.assertIn(DOCX, body)
        self.assertIn(b'"parents": ["folder-9"]', body)
        self.assertNotIn(b"google-apps.document", body)                                       # never converted to a Google Doc
        for wrong in ({**good, "md5Checksum": "0" * 32}, {**good, "webViewLink": "http://x"}):
            with self.assertRaises(gdrive.DriveError) as caught:
                gdrive.Drive(GDRIVE, self.Opener({"access_token": "at"}, wrong)).upload("folder-9", FILENAME, DOCX, resume_app.DOCX, {})
            self.assertEqual(str(caught.exception), "GDRIVE_UPLOAD_UNVERIFIED")

    def test_http_and_network_failures_are_fixed_codes(self):
        import urllib.error
        for reply, code in ((urllib.error.HTTPError("u", 403, "no", {}, None), "GDRIVE_HTTP_403"), (OSError("down"), "GDRIVE_NETWORK")):
            with self.assertRaises(gdrive.DriveError) as caught:
                gdrive.Drive(GDRIVE, self.Opener(reply)).get("f1")
            self.assertEqual(str(caught.exception), code)
        with self.assertRaises(gdrive.DriveError) as caught:
            gdrive.Drive(GDRIVE, self.Opener({})).get("f1")
        self.assertEqual(str(caught.exception), "GDRIVE_TOKEN_REFUSED")

    def test_lock_sets_a_read_only_restriction(self):
        opener = self.Opener({"access_token": "at"}, {"id": "f1", "contentRestrictions": [{"readOnly": True}]})
        meta = gdrive.Drive(GDRIVE, opener).lock("f1", "Submitted with an application")
        self.assertTrue(gdrive.locked(meta))
        self.assertEqual(json.loads(opener.sent[1][3]), {"contentRestrictions": [{"readOnly": True, "reason": "Submitted with an application"}]})
        self.assertFalse(gdrive.locked({"contentRestrictions": [{"readOnly": False}]}))


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
