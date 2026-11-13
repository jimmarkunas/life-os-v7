"""LIFE-636: the vendored Bitwarden wrapper runs only the checked helper program, never downloads or builds anything, and passes the helper's output (its ::add-mask:: lines) through untouched."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "sm-action"
NODE = shutil.which("node")
TRIPLE = {("linux", "x64"): "x86_64-unknown-linux-gnu", ("linux", "arm64"): "aarch64-unknown-linux-gnu", ("darwin", "x64"): "x86_64-apple-darwin", ("darwin", "arm64"): "aarch64-apple-darwin"}


def run_wrapper(source, helper_script):
    """Copy a wrapper into a temp folder with a fixture helper where the wrapper looks for it; -> CompletedProcess."""
    arch = subprocess.run([NODE, "-p", "process.platform+' '+process.arch"], capture_output=True, text=True).stdout.split()
    with tempfile.TemporaryDirectory() as tmp:
        (pathlib.Path(tmp) / "index.js").write_text(source)
        if helper_script is not None:
            helper = pathlib.Path(tmp, "target", TRIPLE[tuple(arch)], "release", "sm-action")
            helper.parent.mkdir(parents=True)
            helper.write_text(helper_script)
        return subprocess.run([NODE, str(pathlib.Path(tmp) / "index.js")], capture_output=True, text=True, timeout=30, env={"PATH": os.environ["PATH"], "INPUT_SECRETS": "x > FIXTURE"})


@unittest.skipIf(NODE is None, "node not installed")
class VendoredWrapperTests(unittest.TestCase):
    source = (ACTION / "index.js").read_text()

    def test_the_helper_s_mask_line_reaches_the_runner_and_with_output_swallowed_the_test_fails(self):
        helper = "#!/bin/sh\necho '::add-mask::FIXTURE-SECRET-VALUE'\necho \"seen:$INPUT_SECRETS\"\n"
        done = run_wrapper(self.source, helper)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(), ["::add-mask::FIXTURE-SECRET-VALUE", "seen:x > FIXTURE"])       # masked before anything else, inputs handed through
        swallowed = run_wrapper(self.source.replace('stdio: "inherit"', 'stdio: "ignore"'), helper)               # fail-then-pass: a wrapper that hides the helper's output leaves the value unmasked
        self.assertNotIn("::add-mask::FIXTURE-SECRET-VALUE", swallowed.stdout)

    def test_a_failing_helper_fails_the_step_and_a_missing_helper_downloads_and_builds_nothing(self):
        self.assertNotEqual(run_wrapper(self.source, "#!/bin/sh\nexit 3\n").returncode, 0)
        missing = run_wrapper(self.source, None)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("nothing is downloaded or built", missing.stderr)

    def test_the_download_and_the_cargo_fallback_are_gone_and_the_helper_fingerprint_check_still_runs(self):
        code = "\n".join(line for line in self.source.splitlines() if not line.lstrip().startswith("//"))
        for word in ("https", "cargo", "rustup", "download(", "buildFromSource", "execSync"):
            self.assertNotIn(word, code)
        fetch = (ROOT / ".github" / "actions" / "bws-helper" / "fetch.sh").read_text()
        self.assertIn("helper.sha256", (ROOT / ".github" / "actions" / "bws-helper" / "action.yml").read_text())
        self.assertIn("valid", fetch)                                                                               # every copy is checked before use
        self.assertIn("does not match the committed fingerprint", fetch)

    def test_the_license_is_kept_and_the_action_runs_node24_like_the_original(self):
        self.assertIn("GNU GENERAL PUBLIC LICENSE", (ACTION / "LICENSE").read_text())
        self.assertIn('using: "node24"', (ACTION / "action.yml").read_text())


if __name__ == "__main__":
    unittest.main()
