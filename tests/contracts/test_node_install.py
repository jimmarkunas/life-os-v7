"""LIFE-639: Node comes from a checked download (not actions/setup-node) and Python from the runner image in a virtual environment (not actions/setup-python)."""
import hashlib
import io
import os
import pathlib
import subprocess
import tarfile
import tempfile
import unittest

try:
    import yaml
except ImportError:
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[2]
NODE = ROOT / ".github" / "actions" / "node"


def make_tarball(path, payload=b"fixture-node"):
    with tarfile.open(path, "w:xz") as tar:
        info = tarfile.TarInfo("node-v24.22.3-linux-x64/bin/node")
        info.size, info.mode = len(payload), 0o755
        tar.addfile(info, io.BytesIO(payload))


def run_install(url, recorded, tmp):
    fingerprint = pathlib.Path(tmp, "recorded.sha256")
    fingerprint.write_text(recorded + "\n")
    path_file = pathlib.Path(tmp, "path")
    work = pathlib.Path(tmp, "work")
    work.mkdir()
    env = {"PATH": os.environ["PATH"], "NODE_URL": url, "NODE_SHA256_FILE": str(fingerprint), "NODE_DIR": str(pathlib.Path(tmp, "node")), "GITHUB_PATH": str(path_file), "NODE_TMP": str(work)}
    done = subprocess.run(["bash", str(NODE / "install.sh")], env=env, capture_output=True, text=True, timeout=60)
    return done, path_file


class NodeInstall(unittest.TestCase):
    def test_a_matching_download_is_unpacked_and_put_on_the_path_and_a_changed_one_is_refused_with_nothing_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = pathlib.Path(tmp, "good.tar.xz")
            make_tarball(good)
            recorded = hashlib.sha256(good.read_bytes()).hexdigest()
            done, path_file = run_install(good.as_uri(), recorded, tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertTrue(pathlib.Path(tmp, "node", "bin", "node").is_file())
            self.assertEqual(path_file.read_text().strip(), str(pathlib.Path(tmp, "node", "bin")))
        with tempfile.TemporaryDirectory() as tmp:                          # fail-then-pass: the same recorded value, one different byte in the download
            bad = pathlib.Path(tmp, "bad.tar.xz")
            make_tarball(bad, b"fixture-node-TAMPERED")
            done, path_file = run_install(bad.as_uri(), recorded, tmp)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("does not match the recorded SHA-256", done.stdout)
            self.assertFalse(pathlib.Path(tmp, "node").exists())
            self.assertFalse(path_file.exists())

    def test_a_malformed_recorded_fingerprint_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = pathlib.Path(tmp, "good.tar.xz")
            make_tarball(good)
            self.assertNotEqual(run_install(good.as_uri(), "nope", tmp)[0].returncode, 0)

    def test_the_repository_records_a_real_fingerprint_for_one_official_version(self):
        self.assertRegex((NODE / "node.sha256").read_text().strip(), r"^[0-9a-f]{64}$")
        script = (NODE / "install.sh").read_text()
        self.assertIn('version="v24.22.3"', script)
        self.assertIn("https://nodejs.org/dist/${version}/${name}.tar.xz", script)


@unittest.skipIf(yaml is None, "PyYAML not installed")
class NoSetupActions(unittest.TestCase):
    def test_no_workflow_or_action_uses_setup_python_or_setup_node(self):
        for path in sorted((ROOT / ".github").rglob("*.yml")):
            text = path.read_text()
            self.assertNotIn("uses: actions/setup-python", text, path.name)
            self.assertNotIn("uses: actions/setup-node", text, path.name)

    def test_the_python_action_checks_the_image_python_is_3_12_makes_a_venv_and_installs_the_hashed_lock(self):
        steps = yaml.safe_load((ROOT / ".github" / "actions" / "python" / "action.yml").read_text())["runs"]["steps"]
        first = steps[0]["run"]
        self.assertIn("Python 3\\.12\\.", first)
        self.assertIn('python3 -m venv "$RUNNER_TEMP/venv"', first)
        self.assertIn('"$RUNNER_TEMP/venv/bin" >> "$GITHUB_PATH"', first)
        self.assertEqual(steps[1]["run"], "pip install --require-hashes -r requirements.lock")


if __name__ == "__main__":
    unittest.main()
