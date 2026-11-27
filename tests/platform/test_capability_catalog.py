"""(F01, synthetic): the capability catalog resolver."""
import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from lifeos.platform import capability_catalog as C

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)
DOC = {
    "version": 1,
    "principals": [{"id": "client-a", "kind": "agent", "workspace": "ws-one"}, {"id": "client-b", "kind": "session", "workspace": "ws-two"}],
    "accounts": [{"alias": "acct-a", "provider": "prov", "tenant": "tenant-a"}, {"alias": "acct-b", "provider": "prov", "tenant": "tenant-b"}],
    "capabilities": [
        {"principal": "client-a", "tool": "tool1", "action": "read", "provider": "prov", "account_alias": "acct-a", "host": "host-one", "source": "src/a.py", "as_of": "2026-11-20", "max_age_days": 30, "status": "working"},
        {"principal": "client-b", "tool": "tool1", "action": "read", "provider": "prov", "account_alias": "acct-b", "host": "host-two", "source": "src/b.py", "as_of": "2026-11-20", "max_age_days": 30, "status": "synthetic"},
        {"principal": "client-a", "tool": "tool2", "action": "write", "provider": "prov", "account_alias": None, "host": "host-one", "source": "src/c.py", "as_of": "2026-11-20", "max_age_days": 30, "status": "missing"},
    ],
}


def cat(patch=None):
    doc = copy.deepcopy(DOC)
    if patch:
        patch(doc)
    return C.load(json.dumps(doc))


class Resolve(unittest.TestCase):
    def test_two_clients_resolve_the_same_tool_to_their_own_account_host_and_source(self):
        c = cat()
        a, b = C.resolve(c, "client-a", "tool1", "read", "ws-one", NOW), C.resolve(c, "client-b", "tool1", "read", "ws-two", NOW)
        self.assertEqual((a["tenant"], a["host"], a["allowed_source"], a["synthetic"]), ("tenant-a", "host-one", "src/a.py", False))
        self.assertEqual((b["tenant"], b["host"], b["allowed_source"], b["synthetic"]), ("tenant-b", "host-two", "src/b.py", True))
        self.assertEqual(a["provenance"], {"source": "src/a.py", "as_of": "2026-11-20"})

    def test_a_resolution_never_carries_a_credential_or_an_endpoint(self):
        out = C.resolve(cat(), "client-a", "tool1", "read", "ws-one", NOW)
        self.assertFalse([k for k in out if C.FORBIDDEN_KEYS.search(k)])

    def test_refusals_use_fixed_codes_and_invent_no_route(self):
        c = cat()
        cases = [(c, "stranger", "tool1", "read", "ws-one", "unknown_principal"), (c, "client-a", "tool1", "read", "ws-two", "wrong_workspace"),
                 (c, "client-a", "tool9", "read", "ws-one", "unknown_capability"), (c, "client-a", "tool2", "write", "ws-one", "unavailable"),
                 (None, "client-a", "tool1", "read", "ws-one", "catalog_unreadable"), (c, None, "tool1", "read", "ws-one", "unknown_principal")]
        for catalog, who, tool, action, ws, code in cases:
            out = C.resolve(catalog, who, tool, action, ws, NOW)
            self.assertEqual((out["ok"], out["code"]), (False, code))
            self.assertNotIn("host", out)

    def test_a_missing_or_duplicate_alias_refuses(self):
        gone = cat(lambda d: d["accounts"].pop(0))
        twice = cat(lambda d: d["accounts"].append(dict(d["accounts"][0], tenant="tenant-z")))
        self.assertEqual(C.resolve(gone, "client-a", "tool1", "read", "ws-one", NOW)["code"], "alias_missing")
        self.assertEqual(C.resolve(twice, "client-a", "tool1", "read", "ws-one", NOW)["code"], "alias_duplicate")

    def test_an_alias_of_another_provider_does_not_count(self):
        c = cat(lambda d: d["accounts"].__setitem__(0, dict(d["accounts"][0], provider="other")))
        self.assertEqual(C.resolve(c, "client-a", "tool1", "read", "ws-one", NOW)["code"], "alias_missing")

    def test_a_stale_source_refuses_at_the_age_limit(self):
        c = cat()
        self.assertTrue(C.resolve(c, "client-a", "tool1", "read", "ws-one", datetime(2026, 12, 20, tzinfo=timezone.utc))["ok"])
        out = C.resolve(c, "client-a", "tool1", "read", "ws-one", datetime(2026, 12, 21, tzinfo=timezone.utc))
        self.assertEqual((out["ok"], out["code"]), (False, "source_stale"))


class Load(unittest.TestCase):
    def test_bad_documents_raise_fixed_codes(self):
        for text, code in [("nope", "not_json"), ("[]", "bad_shape"), (json.dumps({**DOC, "version": 2}), "bad_version"), (json.dumps({**DOC, "extra": 1}), "bad_shape")]:
            with self.assertRaises(C.CatalogError) as caught:
                C.load(text)
            self.assertEqual(str(caught.exception), code)

    def test_a_credential_or_endpoint_in_the_catalog_is_refused(self):
        bad = [lambda d: d["capabilities"][0].update(source="ghp_" + "a" * 36), lambda d: d["accounts"][0].update(api_key="x"),
               lambda d: d["capabilities"][0].update(host="0" * 40), lambda d: d["capabilities"][0].update(endpoint="https://example.com")]
        for patch in bad:
            with self.assertRaises(C.CatalogError) as caught:
                cat(patch)
            self.assertEqual(str(caught.exception), "credential_in_catalog")

    def test_bad_status_and_bad_rows_are_refused(self):
        for patch, code in [(lambda d: d["capabilities"][0].update(status="live"), "bad_status"), (lambda d: d["capabilities"][0].update(max_age_days=0), "bad_row"),
                            (lambda d: d["capabilities"][0].update(as_of="soon"), "bad_row"), (lambda d: d["principals"][0].update(kind="robot"), "bad_row"),
                            (lambda d: d["capabilities"][0].pop("host"), "bad_row")]:
            with self.assertRaises(C.CatalogError) as caught:
                cat(patch)
            self.assertEqual(str(caught.exception), code)


class Registry(unittest.TestCase):
    def test_the_repo_registry_loads_and_classifies_working_synthetic_and_missing(self):
        c = C.load((ROOT / "lifeos/platform/capability_catalog.json").read_text())
        statuses = {row["status"] for row in c["capabilities"]}
        self.assertEqual(statuses, set(C.STATUSES))
        for row in c["capabilities"]:
            if row["status"] != "missing":
                self.assertTrue((ROOT / row["source"]).exists(), row["source"])  # a registered source is a real file

    def test_the_missing_secret_provider_resolves_as_unavailable(self):
        c = C.load((ROOT / "lifeos/platform/capability_catalog.json").read_text())
        out = C.resolve(c, "claude-code", "doppler", "read_secret", "v7-mac", NOW)
        self.assertEqual((out["ok"], out["code"]), (False, "unavailable"))


if __name__ == "__main__":
    unittest.main()
