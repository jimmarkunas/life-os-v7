"""LIFE-577: temp folders for the backup tests whose paths go through the secret guard (offsite_backup.DENY).
tempfile picks 8 random characters; once in a few thousand names holds a guarded word ("key", "bws", "auth", ...), and the guard then refuses the run with backup_secret_path.
That made test_a_secret_folder_is_never_a_way_around_the_rest fail on a clean checkout. These helpers draw again until the whole path is clear of the guard."""
import shutil
import tempfile

from lifeos.edge.backup import offsite_backup as ob


def mkdtemp(**kwargs):
    while True:
        path = tempfile.mkdtemp(**kwargs)
        if not ob.denied(path):
            return path
        shutil.rmtree(path, ignore_errors=True)


class TemporaryDirectory(tempfile.TemporaryDirectory):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        while ob.denied(self.name):
            self.cleanup()
            super().__init__(**kwargs)
