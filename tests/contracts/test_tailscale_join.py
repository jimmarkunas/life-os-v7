"""LIFE-637: our own tailnet join. The download is installed only when its SHA-256 equals the recorded one; the OAuth secret is masked first and never printed; no workflow uses the third-party action any more."""
import hashlib
import io
import os
import pathlib
import socket
import stat
import subprocess
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "tailscale"
SECRET = "tskey-client-FIXTURE-0123456789"


def make_tarball(path, payload=b"fixture-binary"):
    with tarfile.open(path, "w:gz") as tar:
        for name in ("tailscale", "tailscaled"):
            info = tarfile.TarInfo(f"tailscale_1.94.2_amd64/{name}")
            info.size, info.mode = len(payload), 0o755
            tar.addfile(info, io.BytesIO(payload))


def run_install(url, recorded, bin_dir, tmp):
    fingerprint = pathlib.Path(tmp, "recorded.sha256")
    fingerprint.write_text(recorded + "\n")
    env = {"PATH": os.environ["PATH"], "TS_URL": url, "TS_SHA256_FILE": str(fingerprint), "TS_BIN_DIR": str(bin_dir), "TS_SUDO": "", "TS_TMP": str(pathlib.Path(tmp, "work"))}
    pathlib.Path(tmp, "work").mkdir()
    return subprocess.run(["bash", str(ACTION / "install.sh")], env=env, capture_output=True, text=True, timeout=60)


class InstallTests(unittest.TestCase):
    def test_a_matching_download_is_installed_and_a_changed_one_is_refused_with_nothing_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = pathlib.Path(tmp, "good.tgz")
            make_tarball(good)
            recorded = hashlib.sha256(good.read_bytes()).hexdigest()
            bin_dir = pathlib.Path(tmp, "bin")
            bin_dir.mkdir()
            done = run_install(good.as_uri(), recorded, bin_dir, tmp)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(sorted(p.name for p in bin_dir.iterdir()), ["tailscale", "tailscaled"])
        with tempfile.TemporaryDirectory() as tmp:                          # fail-then-pass: the same recorded value, a download with one different byte
            bad = pathlib.Path(tmp, "bad.tgz")
            make_tarball(bad, b"fixture-binary-TAMPERED")
            bin_dir = pathlib.Path(tmp, "bin")
            bin_dir.mkdir()
            done = run_install(bad.as_uri(), recorded, bin_dir, tmp)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("does not match the recorded SHA-256", done.stdout)
            self.assertEqual(list(bin_dir.iterdir()), [])

    def test_a_malformed_recorded_fingerprint_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = pathlib.Path(tmp, "good.tgz")
            make_tarball(good)
            self.assertNotEqual(run_install(good.as_uri(), "not-a-hash", pathlib.Path(tmp), tmp).returncode, 0)

    def test_the_repository_records_a_real_fingerprint_and_the_official_address_for_one_version(self):
        self.assertRegex((ACTION / "tailscale.sha256").read_text().strip(), r"^[0-9a-f]{64}$")
        script = (ACTION / "install.sh").read_text()
        self.assertIn('version="1.94.2"', script)
        self.assertIn("https://pkgs.tailscale.com/stable/tailscale_${version}_amd64.tgz", script)


FAKE_TAILSCALE = """#!/bin/sh
echo "$@" >> "$TS_CALLS"
case "$1" in
  up) [ -n "$TS_FAIL_UP" ] && exit 1; exit 0 ;;
  status) echo '{"BackendState": "Running"}' ;;
esac
"""
FAKE_TAILSCALED = "#!/bin/sh\nexit 0\n"


def run_join(source, fail_up=False):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        for name, body in (("tailscale", FAKE_TAILSCALE), ("tailscaled", FAKE_TAILSCALED)):
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755 | stat.S_IXUSR)
        script = tmp / "join.sh"
        script.write_text(source)
        sock_path = tmp / "ts.sock"
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(sock_path))
        calls = tmp / "calls"
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp), "TS_SUDO": "", "TS_SOCKET": str(sock_path), "TS_CALLS": str(calls), "TS_FAIL_UP": "1" if fail_up else "",
               "TS_OAUTH_SECRET": SECRET, "TS_TAGS": "tag:v7-runner", "TS_HOSTNAME": "github-fixture", "TS_RETRY_PAUSE": "0"}
        try:
            done = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60)
        finally:
            listener.close()
        return done, calls.read_text() if calls.exists() else ""


class JoinTests(unittest.TestCase):
    source = (ACTION / "join.sh").read_text()

    def test_the_secret_is_masked_first_and_appears_nowhere_else_in_the_output(self):
        done, calls = run_join(self.source)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], f"::add-mask::{SECRET}")
        self.assertNotIn(SECRET, "\n".join(lines[1:]) + done.stderr)
        self.assertIn("--advertise-tags=tag:v7-runner --hostname=github-fixture --accept-routes", calls)       # same arguments the action used
        self.assertIn("?preauthorized=true&ephemeral=true", calls)                                              # an ephemeral, pre-authorized node
        mutated, _ = run_join(self.source.replace('echo "::add-mask::$secret"', ":"))                          # fail-then-pass: without the mask line the value is not masked
        self.assertNotIn("::add-mask::", mutated.stdout)

    def test_five_failed_joins_fail_the_step_without_printing_the_secret(self):
        done, calls = run_join(self.source, fail_up=True)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(calls.count("up "), 5)
        self.assertNotIn(SECRET, "\n".join(done.stdout.splitlines()[1:]) + done.stderr)

    def test_no_workflow_uses_the_third_party_action_and_every_join_passes_the_three_inputs(self):
        for path in sorted((ROOT / ".github").rglob("*.yml")):
            text = path.read_text()
            if path.parent.name != "tailscale":
                self.assertNotIn("tailscale/github-action", text.replace("replacing tailscale/github-action", ""), path.name)
            count = text.count("uses: ./.github/actions/tailscale")
            if count:
                self.assertEqual(text.count("oauth-client-id:"), count, path.name)
                self.assertEqual(text.count("oauth-secret:"), count, path.name)
                self.assertEqual(text.count("tags: tag:v7-runner"), count, path.name)


if __name__ == "__main__":
    unittest.main()
