"""LIFE-577: temp folders whose whole path is clear of the backup secret guard (offsite_backup.denied). tempfile's random 8 characters once in a few thousand names hold a guarded word
("key", "auth"), and the guard then refuses the run with backup_secret_path. These draw again until the path is clear."""
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
