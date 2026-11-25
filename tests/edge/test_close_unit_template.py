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

    def test_the_retired_card_redraw_worker_has_no_unit_and_is_removed_on_install(self):
        """LIFE-973: the Recruiter Outreach card is a live Notion view; the redraw worker's units are no longer written and an install removes the old ones."""
        text = SCRIPT.read_text()
        self.assertNotIn('cat > "$1/lifeos-close-redraw', text)
        self.assertIn("enable --now lifeos-close.socket >/dev/null; systemctl disable --now lifeos-close-redraw.socket lifeos-close-redraw.service", text)


if __name__ == "__main__":
    unittest.main()
