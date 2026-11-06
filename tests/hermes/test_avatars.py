"""LIFE-326: the avatar manifest and the checked install script. No image is stored in this public repository; the tests make tiny fake files with real image signatures."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERMES = os.path.join(ROOT, "hermes")
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8L" + b"y" * 30
JPG = b"\xff\xd8\xff\xe0" + b"z" * 40
PROFILES = ("main", "claude-hermes", "developer", "librarian", "research", "writer")


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


class Avatars(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        self.src = os.path.join(self.tmp, "src")
        self.backup = os.path.join(self.tmp, "backup")
        os.makedirs(self.src)
        os.makedirs(self.home)
        for p in PROFILES[1:]:
            os.makedirs(os.path.join(self.home, "profiles", p, "assets"))
        os.makedirs(os.path.join(self.home, "assets"))
        for p in PROFILES:
            with open(self.avatar(p, "png"), "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\nOLD " + p.encode())
        self.files = {}
        self.manifest = os.path.join(self.tmp, "avatars.manifest")
        self.write_manifest({p: ("%s.png" % p, PNG + p.encode()) for p in PROFILES})

    def avatar(self, profile, ext):
        base = os.path.join(self.home, "assets") if profile == "main" else os.path.join(self.home, "profiles", profile, "assets")
        return os.path.join(base, "avatar." + ext)

    def write_manifest(self, rows, mangle=None):
        lines = ["# test manifest", ""]
        for profile, (name, blob) in rows.items():
            with open(os.path.join(self.src, name), "wb") as handle:
                handle.write(blob)
            lines.append("%s %s %s %d" % (profile, name, sha(blob), len(blob)))
        if mangle:
            lines = mangle(lines)
        with open(self.manifest, "w") as handle:
            handle.write("\n".join(lines) + "\n")

    def run_script(self, *args):
        done = subprocess.run(["bash", os.path.join(HERMES, "deploy_avatars.sh"), "--src", self.src, "--home", self.home, "--manifest", self.manifest, *args], capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def test_syntax_and_the_default_is_a_dry_run_that_writes_nothing(self):
        self.assertEqual(subprocess.run(["bash", "-n", os.path.join(HERMES, "deploy_avatars.sh")]).returncode, 0)
        status, out = self.run_script()
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("6 profile(s) would change", out)
        for p in PROFILES:
            self.assertTrue(self.read(self.avatar(p, "png")).startswith(b"\x89PNG\r\n\x1a\nOLD"), p)
        self.assertFalse(os.path.exists(self.backup))

    def test_apply_backs_up_installs_reads_back_and_a_second_apply_changes_nothing(self):
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        self.assertIn("read-back OK", out)
        for p in PROFILES:
            self.assertEqual(self.read(self.avatar(p, "png")), PNG + p.encode(), p)
            self.assertEqual(os.stat(self.avatar(p, "png")).st_mode & 0o777, 0o600, p)
            self.assertTrue(self.read(os.path.join(self.backup, p, "avatar.png")).startswith(b"\x89PNG\r\n\x1a\nOLD"), p)
            self.assertFalse(os.path.exists(self.avatar(p, "png") + ".tmp"), p)
        self.assertIn("ROLLBACK writer", out)
        # same extension old and new: the rollback only restores, it never deletes the file it just restored
        line = [l for l in out.splitlines() if l.startswith("ROLLBACK writer")][0]
        self.assertIn("avatar.png", line)
        self.assertNotIn("rm -f", line)
        status, again = self.run_script("--apply", "--backup", os.path.join(self.tmp, "b2"))
        self.assertEqual(status, 0, again)
        self.assertIn("0 profile(s) would change", again)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "b2")))

    def test_a_webp_replaces_the_png_so_one_canonical_file_remains(self):
        rows = {p: ("%s.png" % p, PNG + p.encode()) for p in PROFILES}
        rows["writer"] = ("writer.webp", WEBP)
        rows["research"] = ("research.jpg", JPG)
        self.write_manifest(rows)
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        self.assertEqual(self.read(self.avatar("writer", "webp")), WEBP)
        self.assertFalse(os.path.exists(self.avatar("writer", "png")))
        self.assertEqual(self.read(self.avatar("research", "jpg")), JPG)
        self.assertFalse(os.path.exists(self.avatar("research", "png")))
        self.assertTrue(os.path.exists(os.path.join(self.backup, "writer", "avatar.png")))

    def test_the_rollback_line_removes_the_new_file_only_when_its_extension_differs(self):
        rows = {p: ("%s.png" % p, PNG + p.encode()) for p in PROFILES}
        rows["writer"] = ("writer.webp", WEBP)
        self.write_manifest(rows)
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        line = [l for l in out.splitlines() if l.startswith("ROLLBACK writer")][0]
        self.assertIn("&& rm -f", line)
        self.assertIn("avatar.webp", line.split("&& rm -f")[1])

    def test_a_stale_second_extension_is_removed_even_when_the_new_file_is_already_in_place(self):
        self.assertEqual(self.run_script("--apply", "--backup", self.backup)[0], 0)
        with open(self.avatar("writer", "webp"), "wb") as handle:
            handle.write(WEBP)
        status, out = self.run_script("--apply", "--backup", os.path.join(self.tmp, "b3"))
        self.assertEqual(status, 0, out)
        self.assertFalse(os.path.exists(self.avatar("writer", "webp")))
        self.assertTrue(os.path.exists(self.avatar("writer", "png")))

    def test_refusals_happen_before_anything_is_written(self):
        # wrong hash
        rows = {p: ("%s.png" % p, PNG + p.encode()) for p in PROFILES}
        self.write_manifest(rows)
        with open(os.path.join(self.src, "writer.png"), "ab") as handle:
            handle.write(b"tampered")
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 1, out)
        self.assertIn("is %d bytes, the manifest says" % (len(PNG + b"writer") + 8), out)
        for p in PROFILES:
            self.assertTrue(self.read(self.avatar(p, "png")).startswith(b"\x89PNG\r\n\x1a\nOLD"), p)
        self.assertFalse(os.path.exists(self.backup))

    def test_same_size_different_content_is_caught_by_the_hash(self):
        self.write_manifest({p: ("%s.png" % p, PNG + p.encode()) for p in PROFILES})
        blob = self.read(os.path.join(self.src, "writer.png"))
        with open(os.path.join(self.src, "writer.png"), "wb") as handle:
            handle.write(blob[:-1] + b"!")
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 1, out)
        self.assertIn("does not match the manifest hash", out)

    def test_not_an_image_too_big_missing_and_bad_lines_are_refused(self):
        for name, blob, needle in (("text", b"just some text, not an image at all.", "not a PNG, JPEG or WebP"),
                                   ("big", PNG + b"q" * 2_000_001, "over 2000000 bytes"),
                                   ("empty", b"", "empty or over")):
            self.write_manifest({"writer": ("w.png", blob)})
            status, out = self.run_script("--apply", "--backup", self.backup)
            self.assertEqual(status, 1, (name, out))
            self.assertIn(needle, out)
        self.write_manifest({"writer": ("gone.png", PNG)})
        os.remove(os.path.join(self.src, "gone.png"))
        status, out = self.run_script()
        self.assertEqual(status, 1, out)
        self.assertIn("is not in", out)
        for line, needle in (("writer ../../etc/passwd %s 5" % ("a" * 64), "plain file name"), ("writer w.png nothex 5", "bad sha256"),
                             ("writer w.png %s five" % ("a" * 64), "bad size"), ("onlytwo fields", "bad manifest line"),
                             ("Writer w.png %s 5" % ("a" * 64), "not a profile name"), ("nobody w.png %s 5" % ("a" * 64), "no profile folder")):
            with open(self.manifest, "w") as handle:
                handle.write(line + "\n")
            status, out = self.run_script()
            self.assertEqual(status, 1, (line, out))
            self.assertIn(needle, out)
        with open(self.manifest, "w") as handle:
            handle.write("# only a comment\n")
        self.assertIn("lists nothing", self.run_script()[1])

    def test_a_profile_listed_twice_and_a_missing_src_are_refused(self):
        self.write_manifest({"writer": ("w.png", PNG)})
        with open(self.manifest, "a") as handle:
            handle.write("writer w.png %s %d\n" % (sha(PNG), len(PNG)))
        status, out = self.run_script()
        self.assertEqual(status, 1, out)
        self.assertIn("listed twice", out)
        done = subprocess.run(["bash", os.path.join(HERMES, "deploy_avatars.sh"), "--home", self.home], capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("--src DIR is required", done.stdout)

    def test_output_never_contains_image_bytes(self):
        status, out = self.run_script("--apply", "--backup", self.backup)
        self.assertEqual(status, 0, out)
        self.assertNotIn("PNG", out.replace("PNG, JPEG or WebP", ""))
        self.assertNotIn("xxxx", out)


class RepoFiles(unittest.TestCase):
    def test_the_manifest_lists_the_six_profiles_with_well_formed_rows_and_no_image_is_in_the_repo(self):
        with open(os.path.join(HERMES, "avatars.manifest")) as handle:
            rows = [l.split("#", 1)[0].split() for l in handle if l.split("#", 1)[0].strip()]
        self.assertEqual(sorted(r[0] for r in rows), sorted(PROFILES))
        for p, f, digest, size in rows:
            self.assertRegex(f, r"^[A-Za-z0-9._-]{1,80}$")
            self.assertRegex(digest, r"^[0-9a-f]{64}$", p)
            self.assertLessEqual(int(size), 2_000_000, p)
            self.assertTrue(f.endswith((".png", ".apng", ".webp", ".jpg")), p)
        images = []
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "hermes")):
            images += [n for n in files if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".apng"))]
        self.assertEqual(images, [], "images belong in the private bot-studio repo, never here")

    def test_the_canon_names_the_manifest_the_script_and_the_private_repo(self):
        with open(os.path.join(ROOT, "docs", "hermes-files.md")) as handle:
            text = handle.read()
        for needle in ("avatars.manifest", "deploy_avatars.sh", "bot-studio", "avatar"):
            self.assertIn(needle, text, needle)


if __name__ == "__main__":
    unittest.main()
