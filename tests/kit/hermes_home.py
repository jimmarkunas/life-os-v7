"""A throwaway Hermes home for the tests of the config-fixing scripts: a temp directory holding config.yaml (mode 0640) and a backup path."""

import os
import shutil
import tempfile


class HermesHome:
    """Mixin for a unittest.TestCase: set CONFIG to the starting config.yaml text; the test gets tmp, home, conf, backup and write()."""

    CONFIG = ""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "hermes")
        os.makedirs(self.home)
        self.conf = os.path.join(self.home, "config.yaml")
        self.write(self.CONFIG)
        os.chmod(self.conf, 0o640)
        self.backup = os.path.join(self.tmp, "backup")

    def write(self, text):
        with open(self.conf, "w") as handle:
            handle.write(text)
