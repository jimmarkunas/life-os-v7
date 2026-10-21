"""The Fit profile sync: only an approved block is adopted, only sync-owned entries change, and any failure leaves the secret exactly as it was."""
import json
import unittest

from lifeos.jobs.fit import profile_sync as ps
from lifeos.jobs.fit.profile import Profile

CAP = {"id": "nsync_example", "label": "an invented strength", "class": "direct", "committed": True, "bucket": "skill", "terms": ["invented skill"]}
BLOCK = {"capabilities": [CAP], "functions": []}
OLD = {"capabilities": [{"id": "keep_me", "label": "kept", "class": "adjacent", "terms": ["kept term"]}], "specialization": ["kept"]}


class Page:
    def __init__(self, text, approved=None, more=False, language="json"):
        self.children = {"results": [{"type": "code", "code": {"language": language, "rich_text": [{"plain_text": text}]}}], "has_more": more}
        if approved:
            self.children["results"].append({"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": f"APPROVED HASH: {approved}"}]}})

    def call(self, method, path, body=None):
        return self.children


class Vault:
    def __init__(self, data):
        self.data, self.writes, self.broken = json.loads(json.dumps(data)), 0, False

    def read(self):
        return json.loads(json.dumps(self.data))

    def write(self, data):
        self.writes += 1
        if not self.broken:
            self.data = json.loads(json.dumps(data))


ENV = {"NOTION_FIT_SYNC_PAGE_ID": "0" * 32}


def go(page, vault, live=True):
    return ps.run(10, live, environ=ENV, notion=page, vault=vault)


def approved_for(block):
    return ps.content_hash(ps.parse(json.dumps(block)))


class AdoptionTests(unittest.TestCase):
    def test_an_approved_block_is_written_and_every_unowned_entry_is_kept(self):
        vault = Vault(OLD)
        out = go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault)
        self.assertEqual(out["code"], "SYNC_WRITTEN")
        self.assertEqual([c["id"] for c in vault.data["capabilities"]], ["keep_me", "nsync_example"])
        self.assertEqual(vault.data["specialization"], ["kept"])
        Profile({"years": 1, **vault.data})                                         # the whole secret still loads

    def test_an_unapproved_or_changed_block_is_never_adopted(self):
        for approved in (None, "0" * 16, approved_for(BLOCK)[::-1]):
            vault = Vault(OLD)
            out = go(Page(json.dumps(BLOCK), approved), vault)
            self.assertEqual((out["code"], vault.writes), ("SYNC_NOT_APPROVED", 0))
        edited = {"capabilities": [{**CAP, "terms": ["invented skill", "extra"]}], "functions": []}
        vault = Vault(OLD)
        self.assertEqual(go(Page(json.dumps(edited), approved_for(BLOCK)), vault)["code"], "SYNC_NOT_APPROVED")     # approval is of the exact content

    def test_a_dry_run_changes_nothing_and_says_what_it_would_do(self):
        vault = Vault(OLD)
        out = go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault, live=False)
        self.assertEqual((out["code"], vault.writes), ("SYNC_WOULD_WRITE", 0))

    def test_an_unchanged_block_writes_nothing(self):
        vault = Vault(OLD)
        page = Page(json.dumps(BLOCK), approved_for(BLOCK))
        go(page, vault)
        self.assertEqual((go(page, vault)["code"], vault.writes), ("SYNC_UNCHANGED", 1))

    def test_a_removed_entry_leaves_the_secret_but_only_sync_owned_ones_go(self):
        vault = Vault(OLD)
        go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault)
        smaller = {"capabilities": [{**CAP, "id": "nsync_other", "terms": ["other"]}], "functions": []}
        go(Page(json.dumps(smaller), approved_for(smaller)), vault)
        self.assertEqual([c["id"] for c in vault.data["capabilities"]], ["keep_me", "nsync_other"])

    def test_a_failed_readback_is_reported_and_never_a_success(self):
        vault = Vault(OLD)
        vault.broken = True
        self.assertEqual(go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault)["code"], "SYNC_READBACK_MISMATCH")


class RefusalTests(unittest.TestCase):
    def refused(self, text, code, approved="0" * 16):
        vault = Vault(OLD)
        self.assertEqual(go(Page(text, approved), vault)["code"], code, text[:60])
        self.assertEqual((vault.writes, vault.data), (0, OLD))

    def test_bad_blocks_are_refused_with_a_fixed_code_and_the_secret_is_untouched(self):
        self.refused("not json", "SYNC_BLOCK_NOT_JSON")
        self.refused(json.dumps({"capabilities": [CAP], "exclusions": []}), "SYNC_BLOCK_KEYS")                  # the sync may not add exclusions
        self.refused(json.dumps({"capabilities": [{**CAP, "id": "other"}]}), "SYNC_CAPABILITY_ID")             # it may not claim an id it does not own
        self.refused(json.dumps({"capabilities": [{**CAP, "class": "nope", "committed": False}]}), "SYNC_BLOCK_INVALID")
        self.refused(json.dumps({"capabilities": []}), "SYNC_BLOCK_SIZE")
        self.refused(json.dumps({"capabilities": [{**CAP, "terms": ["x" * 30000]}]}), "SYNC_BLOCK_TOO_LARGE")

    def test_page_problems_are_fixed_codes(self):
        self.assertEqual(go(Page(json.dumps(BLOCK), more=True), Vault(OLD))["code"], "SYNC_PAGE_TOO_LARGE")
        self.assertEqual(go(Page(json.dumps(BLOCK), language="plain text"), Vault(OLD))["code"], "SYNC_NO_BLOCK")
        self.assertEqual(ps.run(10, True, environ={}, notion=Page("{}"), vault=Vault(OLD))["code"], "SYNC_PAGE_UNCONFIGURED")

    def test_a_secret_that_is_not_json_is_never_overwritten(self):
        class Bad(Vault):
            def read(self):
                raise ps.SyncError("SYNC_SECRET_NOT_JSON")
        vault = Bad(OLD)
        self.assertEqual(go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault)["code"], "SYNC_SECRET_NOT_JSON")
        self.assertEqual(vault.writes, 0)

    def test_the_log_line_never_carries_page_content(self):
        out = go(Page(json.dumps(BLOCK), approved_for(BLOCK)), Vault(OLD))
        self.assertLessEqual(set(out), {"code", "live", "entries"})
        self.assertNotIn("invented", json.dumps(out))


if __name__ == "__main__":
    unittest.main()
