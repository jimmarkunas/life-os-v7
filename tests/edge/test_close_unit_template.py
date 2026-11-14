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


if __name__ == "__main__":
    unittest.main()
