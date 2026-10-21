"""The Fit profile sync: only an approved block is stored, the loader merges only a block that matches its own hash, and any failure leaves the stored row exactly as it was."""
import json
import unittest

from lifeos.jobs.fit import profile_sync as ps
from lifeos.jobs.fit.profile import ProfileError, load

CAP = {"id": "nsync_example", "label": "an invented strength", "class": "direct", "committed": True, "bucket": "skill", "terms": ["invented skill"]}
BLOCK = {"capabilities": [CAP], "functions": []}
BASE = {"years": 5, "capabilities": [{"id": "kept", "label": "kept", "class": "adjacent", "terms": ["kept term"]}]}


class Page:
    def __init__(self, text, approved=None, more=False, language="json"):
        self.children = {"results": [{"type": "code", "code": {"language": language, "rich_text": [{"plain_text": text}]}}], "has_more": more}
        if approved:
            self.children["results"].append({"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": f"APPROVED HASH: {approved}"}]}})

    def call(self, method, path, body=None):
        return self.children


class Vault:
    """The one stored row: (content hash, block)."""

    def __init__(self, row=(None, None)):
        self.row, self.writes, self.broken, self.down = row, 0, False, False

    def read(self):
        if self.down:
            raise RuntimeError("store down")
        return self.row

    def write(self, block):
        self.writes += 1
        if not self.broken:
            self.row = (ps.content_hash(block), json.loads(json.dumps(block)))


ENV = {"NOTION_FIT_SYNC_PAGE_ID": "0" * 32}


def go(page, vault, live=True):
    return ps.run(10, live, environ=ENV, notion=page, vault=vault)


def approved_for(block):
    return ps.content_hash(ps.parse(json.dumps(block)))


class AdoptionTests(unittest.TestCase):
    def test_an_approved_block_is_stored_and_read_back(self):
        vault = Vault()
        out = go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault)
        self.assertEqual((out["code"], vault.writes), ("SYNC_WRITTEN", 1))
        self.assertEqual(vault.row[0], approved_for(BLOCK))

    def test_an_unapproved_or_changed_block_is_never_stored(self):
        for approved in (None, "0" * 16, approved_for(BLOCK)[::-1]):
            vault = Vault()
            out = go(Page(json.dumps(BLOCK), approved), vault)
            self.assertEqual((out["code"], vault.writes), ("SYNC_NOT_APPROVED", 0))
        edited = {"capabilities": [{**CAP, "terms": ["invented skill", "extra"]}], "functions": []}
        vault = Vault()
        self.assertEqual(go(Page(json.dumps(edited), approved_for(BLOCK)), vault)["code"], "SYNC_NOT_APPROVED")     # approval is of the exact content

    def test_prose_that_merely_quotes_the_approval_line_is_not_an_approval(self):
        page = Page(json.dumps(BLOCK))
        digest = approved_for(BLOCK)
        for prose in (f"To approve, write APPROVED HASH: {digest}", f"approved hash: {digest} (pending)", f"note APPROVED HASH: {digest}"):
            page.children["results"] = page.children["results"][:1] + [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": prose}]}}]
            vault = Vault()
            self.assertEqual((go(page, vault)["code"], vault.writes), ("SYNC_NOT_APPROVED", 0), prose)
        page.children["results"][1]["paragraph"]["rich_text"][0]["plain_text"] = f"APPROVED HASH: {digest}"
        self.assertEqual(go(page, Vault())["code"], "SYNC_WRITTEN")

    def test_a_dry_run_changes_nothing_and_says_what_it_would_do(self):
        vault = Vault()
        out = go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault, live=False)
        self.assertEqual((out["code"], vault.writes), ("SYNC_WOULD_WRITE", 0))

    def test_an_unchanged_block_writes_nothing(self):
        vault = Vault()
        page = Page(json.dumps(BLOCK), approved_for(BLOCK))
        go(page, vault)
        self.assertEqual((go(page, vault)["code"], vault.writes), ("SYNC_UNCHANGED", 1))

    def test_a_failed_readback_is_reported_and_never_a_success(self):
        vault = Vault()
        vault.broken = True
        self.assertEqual(go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault)["code"], "SYNC_READBACK_MISMATCH")

    def test_an_unreachable_store_is_a_fixed_code_and_nothing_is_written(self):
        vault = Vault()
        vault.down = True
        self.assertEqual(go(Page(json.dumps(BLOCK), approved_for(BLOCK)), vault)["code"], "SYNC_STORE_UNAVAILABLE")
        self.assertEqual(vault.writes, 0)


class RefusalTests(unittest.TestCase):
    def refused(self, text, code, approved="0" * 16):
        vault = Vault(("keep", {"capabilities": []}))
        self.assertEqual(go(Page(text, approved), vault)["code"], code, text[:60])
        self.assertEqual((vault.writes, vault.row), (0, ("keep", {"capabilities": []})))

    def test_bad_blocks_are_refused_with_a_fixed_code_and_the_stored_row_is_untouched(self):
        self.refused("not json", "SYNC_BLOCK_NOT_JSON")
        self.refused(json.dumps({"capabilities": [CAP], "exclusions": []}), "SYNC_BLOCK_KEYS")                  # the sync may not add exclusions
        self.refused(json.dumps({"capabilities": [{**CAP, "id": "other"}]}), "SYNC_CAPABILITY_ID")             # it may not claim an id it does not own
        self.refused(json.dumps({"capabilities": [{**CAP, "class": "nope", "committed": False}]}), "SYNC_BLOCK_INVALID")
        self.refused(json.dumps({"capabilities": []}), "SYNC_BLOCK_SIZE")
        self.refused(json.dumps({"capabilities": [{**CAP, "terms": ["x" * 30000]}]}), "SYNC_BLOCK_TOO_LARGE")

    def test_page_problems_are_fixed_codes(self):
        self.assertEqual(go(Page(json.dumps(BLOCK), more=True), Vault())["code"], "SYNC_PAGE_TOO_LARGE")
        self.assertEqual(go(Page(json.dumps(BLOCK), language="plain text"), Vault())["code"], "SYNC_NO_BLOCK")
        self.assertEqual(ps.run(10, True, environ={}, notion=Page("{}"), vault=Vault())["code"], "SYNC_PAGE_UNCONFIGURED")

    def test_the_log_line_never_carries_page_content(self):
        out = go(Page(json.dumps(BLOCK), approved_for(BLOCK)), Vault())
        self.assertLessEqual(set(out), {"code", "live", "entries"})
        self.assertNotIn("invented", json.dumps(out))


class StoreReadTests(unittest.TestCase):
    """The real Store.read against a fake connection: a missing table is 'nothing synced', any other database error is not."""

    def store_with(self, error=None, row=None):
        from unittest import mock

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, *args):
                if error:
                    raise error

            def fetchone(self):
                return row

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

        return mock.patch.object(ps.store, "connect", lambda: Connection())

    def test_a_missing_table_means_nothing_was_synced(self):
        with self.store_with(error=Exception(1146, "table does not exist")):
            self.assertEqual(ps.Store().read(), (None, None))

    def test_any_other_database_error_makes_the_profile_unavailable(self):
        with self.store_with(error=Exception(2013, "lost connection")):
            with self.assertRaises(ProfileError):
                ps.stored_block({"LIFEOS_ACQ_DB_NAME": "x"})

    def test_a_stored_row_is_read_back_as_hash_and_block(self):
        block = ps.parse(json.dumps(BLOCK))
        with self.store_with(row=(ps.content_hash(block), json.dumps(block))):
            self.assertEqual(ps.Store().read(), (ps.content_hash(block), block))


class LoaderTests(unittest.TestCase):
    """The pipeline side: what profile.load does with the stored block."""

    ENVIRON = {"FIT_PROFILE_JSON": json.dumps(BASE)}

    def test_the_stored_block_is_added_and_the_secret_profile_is_kept(self):
        block = ps.parse(json.dumps(BLOCK))
        vault = Vault((ps.content_hash(block), block))
        merged = load(self.ENVIRON, ps.stored_block(self.ENVIRON, vault))
        self.assertEqual([c["id"] for c in merged.capabilities], ["kept", "nsync_example"])
        self.assertNotEqual(merged.hash, load(self.ENVIRON).hash)                # a changed profile hash re-scores every job (D94)

    def test_nothing_synced_changes_nothing(self):
        self.assertIsNone(ps.stored_block(self.ENVIRON, Vault()))
        self.assertEqual(load(self.ENVIRON, None).hash, load(self.ENVIRON).hash)

    def test_no_store_configured_means_no_block_not_an_error(self):
        self.assertIsNone(ps.stored_block({}))

    def test_a_row_that_does_not_match_its_hash_or_cannot_be_read_makes_the_profile_unavailable(self):
        block = ps.parse(json.dumps(BLOCK))
        with self.assertRaises(ProfileError):
            ps.stored_block(self.ENVIRON, Vault(("0" * 16, block)))
        down = Vault()
        down.down = True
        with self.assertRaises(ProfileError):
            ps.stored_block(self.ENVIRON, down)

    def test_a_stored_block_that_would_not_load_is_refused(self):
        bad = {"capabilities": [{"id": "nsync_x", "class": "nope", "terms": ["x"]}], "functions": []}
        with self.assertRaises(ProfileError):
            ps.stored_block(self.ENVIRON, Vault((ps.content_hash(bad), bad)))


if __name__ == "__main__":
    unittest.main()
