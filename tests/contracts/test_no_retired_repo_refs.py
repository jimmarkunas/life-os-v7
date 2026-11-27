"""No tracked file names the retired V1 repository or the retired V2 runtime (LIFE-1072).

The old names sent agents to the wrong repository. The needles are built from fragments so this file does not
contain them itself. ALLOWED lists any file that may still hold one (by path, with the reason); it is empty on purpose.
"""

import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEEDLES = ("life-os-" + "automation", "life-os-" + "v2", "lifeos" + "_v2")
ALLOWED: dict[str, str] = {}


def hits(files, read):
    found = []
    for name in files:
        if name in ALLOWED:
            continue
        text = read(name).lower()
        found.extend((name, needle) for needle in NEEDLES if needle in text)
    return found


def _tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _read(name):
    path = ROOT / name
    return path.read_bytes().decode("utf-8", errors="ignore") if path.is_file() else ""


class NoRetiredRepoReferences(unittest.TestCase):
    def test_tracked_tree_names_no_retired_repository_or_runtime(self):
        self.assertEqual(hits(_tracked(), _read), [])

    def test_the_check_catches_each_needle_and_honours_an_exception(self):
        files = {
            "a.md": "see " + NEEDLES[0] + "/main",
            "b.py": "import " + NEEDLES[2],
            "c.txt": "Life-OS-V2",
            "d.md": "ok",
        }
        found = hits(sorted(files), files.get)
        self.assertEqual(sorted(found), [("a.md", NEEDLES[0]), ("b.py", NEEDLES[2]), ("c.txt", NEEDLES[1])])
        ALLOWED["a.md"] = "negative control"
        try:
            self.assertEqual([name for name, _ in hits(sorted(files), files.get)], ["b.py", "c.txt"])
        finally:
            del ALLOWED["a.md"]


if __name__ == "__main__":
    unittest.main()
