"""temp folders whose whole path is clear of the backup secret guard (offsite_backup.denied)."""

import os
import tempfile

from lifeos.edge.backup import offsite_backup as ob


def mkdtemp(**kwargs):
    while ob.denied(path := tempfile.mkdtemp(**kwargs)):
        os.rmdir(path)
    return path


class TemporaryDirectory(tempfile.TemporaryDirectory):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        while ob.denied(self.name):
            self.cleanup()
            super().__init__(**kwargs)
