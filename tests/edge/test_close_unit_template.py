"""LIFE-700: the Close service unit sends stderr to the journal, so a refused form's fixed reason code (lifeos/sources/apps/signed.refused) can be read; it stays fixed codes, never record text."""
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "lifeos/edge/oracle/install-close.sh"


class CloseUnitTemplate(unittest.TestCase):
    def test_the_per_connection_unit_logs_stderr_to_the_journal(self):
        text = SCRIPT.read_text()
        unit = text.split('cat > "$1/lifeos-close@.service" <<\'EOF\'', 1)[1].split("\nEOF", 1)[0]
        self.assertIn("StandardError=journal", unit)
        self.assertNotIn("StandardError=null", unit)

    def test_the_card_redraw_worker_is_socket_activated_reachable_only_by_the_close_group_and_holds_the_notion_credential_alone(self):
        """LIFE-972: the redraw runs in its own unit, not in a click; only the Close service's group may poke it, and it carries one credential."""
        text = SCRIPT.read_text()
        sock = text.split('cat > "$1/lifeos-close-redraw.socket" <<\'EOF\'', 1)[1].split("\nEOF", 1)[0]
        unit = text.split('cat > "$1/lifeos-close-redraw.service" <<\'EOF\'', 1)[1].split("\nEOF", 1)[0]
        for line in ("ListenStream=/run/lifeos-close-redraw/redraw.sock", "SocketGroup=lifeos-resume", "SocketMode=0660", "Accept=no"):
            self.assertIn(line, sock)
        self.assertEqual([l for l in unit.splitlines() if l.startswith("LoadCredentialEncrypted=")], ["LoadCredentialEncrypted=notion:/etc/credstore.encrypted/lifeos-close.notion"])
        for line in ("DynamicUser=yes", "NoNewPrivileges=yes", "ProtectSystem=strict", "CapabilityBoundingSet="):
            self.assertIn(line, unit)
        self.assertIn("enable --now lifeos-close.socket lifeos-close-redraw.socket", text)


if __name__ == "__main__":
    unittest.main()
