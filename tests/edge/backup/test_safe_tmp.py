"""LIFE-577: a random temp folder name that holds a guarded word never reaches the backup tests (the cause of the flaky secret-folder test)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from lifeos.edge.backup import offsite_backup as ob
from tests.edge.backup import safe_tmp


def draws(*names):
    """A mkdtemp stand-in that hands out these folder names in order."""
    root = tempfile.mkdtemp(prefix="st-", dir="/tmp")
    made, queue = [], list(names)

    def fake(**kwargs):
        path = os.path.join(root, queue.pop(0))
        os.makedirs(path)
        made.append(path)
        return path
    return fake, made, root


class SafeTmp(unittest.TestCase):
    def test_the_guard_does_refuse_a_path_with_a_guarded_word(self):
        self.assertTrue(ob.denied("/tmp/tmpkeyabcd/src"))                    # the failure: 'key' inside the random part of the folder name
        self.assertTrue(ob.denied("/tmp/tmpxbwsabc/src"))
        self.assertFalse(ob.denied("/tmp/tmpq7m2x9zc/src"))

    def test_mkdtemp_draws_again_until_the_whole_path_is_clear(self):
        fake, made, root = draws("tmpkeyabcd", "tmpauthabc", "tmpq7m2x9zc")
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        with mock.patch.object(safe_tmp.tempfile, "mkdtemp", fake):
            path = safe_tmp.mkdtemp()
        self.assertEqual(os.path.basename(path), "tmpq7m2x9zc")
        self.assertEqual([os.path.exists(p) for p in made], [False, False, True])    # the refused folders are removed

    def test_temporary_directory_draws_again_too(self):
        names = iter(["tmpkeyabcd", "tmpq7m2x9zc"])
        real = tempfile.mkdtemp

        def fake_mkdtemp(suffix=None, prefix=None, dir=None):
            return real(prefix="tmp", suffix=next(names)[3:], dir="/tmp")   # tmp + (key...) keeps a guarded word in the final path
        with mock.patch.object(tempfile, "mkdtemp", fake_mkdtemp):
            with safe_tmp.TemporaryDirectory() as name:
                self.assertFalse(ob.denied(name))

    def test_the_secret_folder_test_passes_even_when_the_first_draw_is_guarded(self):
        from tests.edge.backup import test_offsite_backup as t
        fake, _, root = draws("tmpkeyabcd", "tmpq7m2x9zc")
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        suite = unittest.defaultTestLoader.loadTestsFromName("test_a_secret_folder_is_never_a_way_around_the_rest", t.Collect)
        with mock.patch.object(safe_tmp.tempfile, "mkdtemp", fake), open(os.devnull, "w") as quiet:
            result = unittest.TextTestRunner(stream=quiet).run(suite)
        self.assertTrue(result.wasSuccessful(), result.errors)


if __name__ == "__main__":
    unittest.main()
