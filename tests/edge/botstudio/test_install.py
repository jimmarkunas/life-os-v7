"""LIFE-327: the private hosting script for the Bot Studio. Real systemctl, tailscale, curl and ss are replaced by small fakes that log what they were asked to do."""
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCRIPT = os.path.join(ROOT, "lifeos", "edge", "botstudio", "install.sh")
INDEX = b"<!doctype html><title>Bot Studio</title>\n"


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "root")
        self.src = os.path.join(self.tmp, "dist")
        self.bin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "calls.log")
        self.ss_file = os.path.join(self.tmp, "ss.txt")
        self.serve_file = os.path.join(self.tmp, "serve.txt")
        for d in (self.root, self.src, self.bin, os.path.join(self.src, "assets")):
            os.makedirs(d, exist_ok=True)
        self.put(os.path.join(self.src, "index.html"), INDEX)
        self.put(os.path.join(self.src, "assets", "app.js"), b"console.log(1)\n")
        self.put(os.path.join(self.src, "assets", "app.css"), b"body{}\n")
        open(self.ss_file, "w").write("State Recv-Q Send-Q Local Address:Port Peer\nLISTEN 0 5 127.0.0.1:8787 0.0.0.0:*\n")
        open(self.serve_file, "w").write("https://lifeos-edge-01.tail96b07f.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:18789\n")
        self.fake("systemctl", 'echo "systemctl $*" >> "$FAKE_LOG"\n[ "$1" = is-active ] && echo active\nexit 0')
        self.fake("tailscale", 'echo "tailscale $*" >> "$FAKE_LOG"\n[ "$1 $2" = "serve status" ] && cat "$FAKE_SERVE"\nexit 0')
        self.fake("curl", 'case "$*" in *http_code*) echo 200 ;; *) cat "$FAKE_INDEX" ;; esac\nexit 0')
        self.fake("ss", 'cat "$FAKE_SS"\nexit 0')
        self.index_copy = os.path.join(self.tmp, "index_served.html")

    def put(self, path, data):
        with open(path, "wb") as handle:
            handle.write(data)

    def fake(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as handle:
            handle.write("#!/bin/bash\n" + body + "\n")
        os.chmod(path, 0o755)

    def run_script(self, verb, *args, env=None):
        e = dict(os.environ, FAKE_LOG=self.log, FAKE_SS=self.ss_file, FAKE_SERVE=self.serve_file, FAKE_INDEX=os.path.join(self.src, "index.html"),
                 SYSTEMCTL=os.path.join(self.bin, "systemctl"), TAILSCALE=os.path.join(self.bin, "tailscale"), CURL=os.path.join(self.bin, "curl"), SS=os.path.join(self.bin, "ss"))
        e.update(env or {})
        done = subprocess.run(["bash", SCRIPT, verb, "--root", self.root, *args], capture_output=True, text=True, env=e)
        return done.returncode, done.stdout + done.stderr

    def calls(self):
        return open(self.log).read() if os.path.exists(self.log) else ""

    def test_syntax_and_the_default_is_a_dry_run_that_writes_nothing(self):
        self.assertEqual(subprocess.run(["bash", "-n", SCRIPT]).returncode, 0)
        status, out = self.run_script("dry", "--src", self.src)
        self.assertEqual(status, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("funnel is never used", out)
        self.assertEqual(os.listdir(self.root), [])
        self.assertNotIn("serve --bg", self.calls())
        self.assertNotIn("enable", self.calls())

    def test_install_copies_the_files_read_only_writes_the_loopback_unit_publishes_to_the_tailnet_and_reads_back(self):
        status, out = self.run_script("install", "--src", self.src)
        self.assertEqual(status, 0, out)
        self.assertIn("read-back OK: HTTP 200", out)
        dist = os.path.join(self.root, "opt", "lifeos-botstudio", "dist")
        self.assertEqual(open(os.path.join(dist, "index.html"), "rb").read(), INDEX)
        self.assertEqual(os.stat(os.path.join(dist, "assets", "app.js")).st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(dist).st_mode & 0o777, 0o755)
        unit = open(os.path.join(self.root, "etc", "systemd", "system", "lifeos-botstudio.service")).read()
        for needle in ("--bind 127.0.0.1", "http.server 8788", "DynamicUser=yes", "NoNewPrivileges=yes", "ProtectSystem=strict", "IPAddressDeny=any", "IPAddressAllow=localhost", "CapabilityBoundingSet="):
            self.assertIn(needle, unit, needle)
        self.assertNotIn("0.0.0.0", unit)
        calls = self.calls()
        self.assertIn("systemctl enable --now lifeos-botstudio.service", calls)
        self.assertIn("tailscale serve --bg --https=8443 http://127.0.0.1:8788", calls)
        self.assertNotIn("funnel", calls)
        self.assertNotIn("--https=443", calls)

    def test_a_second_install_keeps_the_previous_copy_and_replaces_the_files(self):
        self.assertEqual(self.run_script("install", "--src", self.src)[0], 0)
        self.put(os.path.join(self.src, "index.html"), INDEX + b"<!-- v2 -->\n")
        status, out = self.run_script("install", "--src", self.src)
        self.assertEqual(status, 0, out)
        app = os.path.join(self.root, "opt", "lifeos-botstudio")
        self.assertIn(b"v2", open(os.path.join(app, "dist", "index.html"), "rb").read())
        self.assertEqual(open(os.path.join(app, "dist.prev", "index.html"), "rb").read(), INDEX)
        self.assertFalse(os.path.exists(os.path.join(app, "dist.new")))

    def test_bad_sources_are_refused_before_anything_is_written(self):
        def refuse(needle, mutate):
            shutil.rmtree(self.src)
            os.makedirs(os.path.join(self.src, "assets"))
            self.put(os.path.join(self.src, "index.html"), INDEX)
            mutate()
            status, out = self.run_script("install", "--src", self.src)
            self.assertEqual(status, 1, (needle, out))
            self.assertIn(needle, out)
            self.assertEqual(os.listdir(self.root), [], needle)
        refuse("has no index.html", lambda: os.remove(os.path.join(self.src, "index.html")))
        refuse("symbolic link", lambda: os.symlink("/etc/passwd", os.path.join(self.src, "assets", "x.js")))
        refuse("file type not allowed", lambda: self.put(os.path.join(self.src, "assets", "run.sh"), b"#!/bin/sh\n"))
        refuse("file type not allowed", lambda: self.put(os.path.join(self.src, "assets", "x.php"), b"<?php"))
        refuse("unusual file name", lambda: self.put(os.path.join(self.src, "assets", "a b.js"), b"1"))
        refuse("limit", lambda: self.put(os.path.join(self.src, "assets", "big.png"), b"0" * 5_300_000))
        status, out = self.run_script("install")
        self.assertEqual(status, 1, out)
        self.assertIn("--src DIR is required", out)

    def test_ports_that_belong_to_other_services_are_refused(self):
        for env, needle in (({"PORT": "8787"}, "belongs to another service"), ({"PORT": "18789"}, "belongs to another service"), ({"PORT": "443"}, "belongs to another service"),
                            ({"HTTPS_PORT": "443"}, "OpenClaw rule"), ({"PORT": "80x"}, "must be numbers")):
            status, out = self.run_script("dry", "--src", self.src, env=env)
            self.assertEqual(status, 1, (env, out))
            self.assertIn(needle, out)

    def test_a_busy_loopback_port_and_a_taken_tailnet_port_are_refused(self):
        open(self.ss_file, "a").write("LISTEN 0 5 127.0.0.1:8788 0.0.0.0:*\n")
        status, out = self.run_script("dry", "--src", self.src)
        self.assertEqual(status, 1, out)
        self.assertIn("already in use", out)
        open(self.ss_file, "w").write("State\n")
        open(self.serve_file, "a").write("https://lifeos-edge-01.tail96b07f.ts.net:8443 (tailnet only)\n|-- / proxy http://127.0.0.1:9999\n")
        status, out = self.run_script("dry", "--src", self.src)
        self.assertEqual(status, 1, out)
        self.assertIn("already published for something else", out)

    def test_status_reports_the_service_the_page_and_the_publication(self):
        self.assertEqual(self.run_script("install", "--src", self.src)[0], 0)
        open(self.serve_file, "a").write("https://lifeos-edge-01.tail96b07f.ts.net:8443 (tailnet only)\n|-- / proxy http://127.0.0.1:8788\n")
        status, out = self.run_script("status")
        self.assertEqual(status, 0, out)
        self.assertIn("service: active", out)
        self.assertIn("loopback: HTTP 200", out)
        self.assertIn("present, index.html " + hashlib.sha256(INDEX).hexdigest()[:12], out)
        self.assertIn(":8443", out)

    def test_remove_undoes_each_step_and_never_touches_the_443_rule(self):
        self.assertEqual(self.run_script("install", "--src", self.src)[0], 0)
        status, out = self.run_script("remove")
        self.assertEqual(status, 0, out)
        calls = self.calls()
        self.assertIn("tailscale serve --https=8443 off", calls)
        self.assertIn("systemctl disable --now lifeos-botstudio.service", calls)
        self.assertNotIn("--https=443", calls)
        self.assertFalse(os.path.exists(os.path.join(self.root, "opt", "lifeos-botstudio")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc", "systemd", "system", "lifeos-botstudio.service")))

    def test_the_script_can_never_run_funnel(self):
        with open(SCRIPT) as handle:
            code = [l for l in handle if not l.lstrip().startswith("#")]
        for line in code:
            self.assertNotRegex(line, r"\"\$TAILSCALE\"\s+funnel|tailscale\s+funnel", line)
        self.assertNotIn("0.0.0.0", "".join(code).replace("0.0.0.0:*", ""))


if __name__ == "__main__":
    unittest.main()
