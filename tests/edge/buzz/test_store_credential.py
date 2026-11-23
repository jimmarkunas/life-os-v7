"""LIFE-870: no network, no Bitwarden SDK installation, no persistent secrets."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[3] / "lifeos" / "edge" / "buzz" / "selfhost" / "store_credential.py"
SPEC = importlib.util.spec_from_file_location("life870_store", PATH)
M = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


class Vault:
    def __init__(self, write=True):
        self.record = {}
        self.can_write = write
        self.probe = []
    def projects(self):
        return self
    def secrets(self):
        return self
    def get(self, project_id):
        return {"id": project_id}
    def list(self, project_id):
        return list(self.record.values())
    def create(self, name, note, organization_id, value, projects):
        if not self.can_write:
            raise RuntimeError("read_only")
        self.probe.append(value)
        ident = str(len(self.record) + 1)
        self.record[name] = {"key": name, "id": ident}
        return {"id": ident}
    def delete(self, ident):
        self.record = {k: v for k, v in self.record.items() if v["id"] != ident}


class PrivatePilot(unittest.TestCase):
    def test_write_check_must_create_and_delete_its_own_synthetic_secret(self):
        v = Vault()
        status = M.check_write(v, "p", "o", v.delete)
        self.assertEqual((status.name, status.status, v.record), ("LIFE_OS_EDGE_OPENCLAW", "write_ok", {}))

    def test_read_only_or_cleanup_failure_prevents_creating_an_owner_key(self):
        v = Vault(write=False)
        self.assertEqual(M.check_write(v, "p", "o", v.delete).status, "no_write")
        v = Vault()
        x = M.check_write(v, "p", "o", lambda ident: (_ for _ in ()).throw(ValueError()))
        self.assertEqual(x.status, "probe_cleanup_required")
        self.assertEqual(len(v.record), 1)

    def test_owner_generated_once_on_oracle_provisioning_and_never_overwritten(self):
        v = Vault()
        x = M.store(v, M.OWNER, "p", "o")
        self.assertEqual((x.name, x.status), (M.OWNER, "created"))
        self.assertEqual(len(v.probe[0]), 64)
        self.assertEqual(M.store(v, M.OWNER, "p", "o").status, "already_exists")
        self.assertEqual(len(v.probe), 1)

    def test_unapproved_names_and_unverified_inventory_fail_closed(self):
        self.assertEqual(M.store(Vault(), "OTHER_KEY", "p", "o").status, "name_refused")
        class Bad(Vault):
            def list(self, project_id):
                raise RuntimeError("unavailable")
        self.assertEqual(M.store(Bad(), M.OWNER, "p", "o").status, "cannot_check_existing")


if __name__ == "__main__":
    unittest.main()
