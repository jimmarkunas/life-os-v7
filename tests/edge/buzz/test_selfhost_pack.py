"""LIFE-870: the pinned private Buzz relay pilot pack (lifeos/edge/buzz/selfhost). Static checks on the compose file and the unit, and the control script run with fake docker, df, ss, openssl, systemd-creds and curl. No Docker, no network."""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

PACK = Path(__file__).resolve().parents[3] / "lifeos/edge/buzz/selfhost"
SCRIPT = PACK / "lifeos-buzz-selfhost"
SENTINEL = "SYNTHETIC-SECRET-1f2e3d4c"
RELAY_SECRET = "SYNTHETIC-RELAY-KEY-5a6b7c8d"
OWNER = "ab" * 32
FAKES = {
    "docker": '#!/bin/sh\necho "docker $*" >> "$FAKE_LOG"\ncase "$1 $2" in "info -f") echo "$FAKE_DOCKER_ROOT" ;; esac\nexit 0\n',
    "df": '#!/bin/sh\nprintf "Avail\\n%sG\\n" "$FAKE_FREE_GB"\n',
    "ss": '#!/bin/sh\nfor p in $FAKE_PORTS_IN_USE; do echo "LISTEN 0 4096 127.0.0.1:$p 0.0.0.0:*"; done\n',
    "openssl": '#!/bin/sh\necho "SYNTHETIC-SECRET-1f2e3d4c"\n',
    "systemd-creds": '#!/bin/sh\ncase "$1" in\n encrypt) out="$4"; python3 -c "import sys,base64; sys.stdout.write(\'ENC:\'+base64.b64encode(sys.stdin.buffer.read()).decode())" > "$out";;\n decrypt) f="$3"; python3 -c "import sys,base64; d=open(sys.argv[1]).read(); sys.stdout.write(base64.b64decode(d[4:]).decode().strip())" "$f";;\nesac\n',
    "curl": '#!/bin/sh\n[ "$FAKE_HEALTH" = up ]\n',
}


class SelfhostPack(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "bin").mkdir()
        for name, text in FAKES.items():
            (self.tmp / "bin" / name).write_text(text)
            (self.tmp / "bin" / name).chmod(0o755)
        self.creds, self.run_dir, self.relay = self.tmp / "creds", self.tmp / "run", self.tmp / "relay.cred"

    def sh(self, *args, free=20, ports="", health="up"):
        env = {**os.environ, "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "FAKE_LOG": str(self.tmp / "docker.log"), "FAKE_DOCKER_ROOT": str(self.tmp), "FAKE_FREE_GB": str(free), "FAKE_PORTS_IN_USE": ports,
               "FAKE_HEALTH": health, "SELFHOST_CRED_DIR": str(self.creds), "SELFHOST_RELAY_CRED": str(self.relay), "SELFHOST_RUN_DIR": str(self.run_dir)}
        return subprocess.run(["sh", str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=self.tmp)

    def place_relay_key(self):
        import base64
        self.relay.write_text("ENC:" + base64.b64encode(RELAY_SECRET.encode()).decode())

    def everything_written(self):
        return "\n".join(p.read_text(errors="replace") for p in self.tmp.rglob("*") if p.is_file() and p.parent.name != "bin")

    # --- the compose file
    def test_every_image_is_pinned_by_digest_and_every_published_port_is_loopback_only(self):
        doc = yaml.safe_load((PACK / "compose.yml").read_text())
        for name, svc in doc["services"].items():
            self.assertRegex(svc["image"], r"@sha256:[0-9a-f]{64}$", name)
        published = [p for svc in doc["services"].values() for p in svc.get("ports", [])]
        self.assertEqual(published, ["127.0.0.1:3100:3000", "127.0.0.1:3180:8080"])
        self.assertNotIn("0.0.0.0", "".join(published))

    def test_every_long_running_container_has_memory_and_cpu_limits_and_no_secret_is_in_the_file(self):
        doc = yaml.safe_load((PACK / "compose.yml").read_text())
        for name in ("relay", "postgres", "redis", "minio"):
            self.assertTrue(doc["services"][name]["mem_limit"] and doc["services"][name]["cpus"], name)
        text = (PACK / "compose.yml").read_text()
        self.assertFalse(re.search(r"(?i)(password|secret|key)[A-Z_]*:\s+[A-Za-z0-9]{12,}\s*$", text, re.M))      # only ${NAME:?...} references, never a value
        self.assertIn("/run/lifeos-buzz-selfhost/env", text)
        self.assertNotIn("BUZZ_ADMIN", text)                                                                     # no admin dashboard
        self.assertEqual(doc["services"]["relay"]["image"].split("@")[0], "ghcr.io/block/buzz:0.3.0")

    def test_the_unit_runs_the_script_and_the_health_timer_is_every_five_minutes(self):
        self.assertIn("ExecStart=/opt/lifeos-buzz-selfhost/lifeos-buzz-selfhost up", (PACK / "lifeos-buzz-selfhost.service").read_text())
        self.assertIn("OnUnitActiveSec=5min", (PACK / "lifeos-buzz-selfhost-health.timer").read_text())
        self.assertEqual(stat.S_IMODE(SCRIPT.stat().st_mode) & 0o111, 0o111)

    # --- the control script
    def test_init_stores_five_random_secrets_encrypted_and_prints_only_names(self):
        done = self.sh("init", OWNER)
        self.assertEqual(done.stdout.splitlines()[:5], [f"{n} ok" for n in ("postgres_password", "redis_password", "s3_access_key", "s3_secret_key", "git_hook_hmac")])
        self.assertIn("relay_key missing", done.stdout)
        self.assertNotIn(SENTINEL, done.stdout + done.stderr)
        self.assertEqual(sorted(p.name for p in self.creds.iterdir()), ["git_hook_hmac.cred", "owner.pubkey", "postgres_password.cred", "redis_password.cred", "s3_access_key.cred", "s3_secret_key.cred"])
        self.assertTrue(all(p.read_text().startswith("ENC:") for p in self.creds.glob("*.cred")))
        self.assertEqual(self.sh("init", OWNER).stdout.splitlines()[0], "postgres_password exists")             # a second run keeps what it made
        self.assertEqual(self.sh("init", "not-hex").returncode, 64)
        self.assertEqual(self.sh("init", "ab" * 31).returncode, 64)

    def test_render_env_writes_the_tmpfs_file_with_every_setting_and_nothing_when_a_credential_is_missing(self):
        self.sh("init", OWNER)
        self.assertEqual(self.sh("render-env").returncode, 1)                                                      # the relay key is not placed yet
        self.assertFalse((self.run_dir / "env").exists())
        self.place_relay_key()
        done = self.sh("render-env")
        self.assertEqual((done.stdout.strip(), done.returncode), ("render_env ok", 0))
        env = dict(line.split("=", 1) for line in (self.run_dir / "env").read_text().splitlines())
        self.assertEqual((env["BUZZ_RELAY_PRIVATE_KEY"], env["RELAY_OWNER_PUBKEY"], env["POSTGRES_PASSWORD"], env["BUZZ_REQUIRE_RELAY_MEMBERSHIP"]), (RELAY_SECRET, OWNER, SENTINEL, "true"))
        self.assertEqual(stat.S_IMODE((self.run_dir / "env").stat().st_mode), 0o600)
        self.assertNotIn(RELAY_SECRET, done.stdout + done.stderr)

    def test_preflight_refuses_when_the_disk_is_below_the_floor_or_a_port_is_taken_or_a_credential_is_missing(self):
        self.sh("init", OWNER)
        self.place_relay_key()
        self.assertEqual(self.sh("preflight").returncode, 0)
        low = self.sh("preflight", free=5)
        self.assertEqual(low.returncode, 1)
        self.assertIn("disk_above_floor_8gb fail", low.stdout)
        self.assertIn("port_3100_free fail", self.sh("preflight", ports="3100").stdout)
        (self.creds / "redis_password.cred").unlink()
        self.assertIn("credential_redis_password fail", self.sh("preflight").stdout)

    def test_up_runs_compose_with_the_tmpfs_env_only_after_a_passing_preflight_and_refuses_otherwise(self):
        self.sh("init", OWNER)
        self.place_relay_key()
        refused = self.sh("up", free=3)
        self.assertEqual((refused.returncode, "up refused" in refused.stdout), (3, True))
        self.assertFalse((self.tmp / "docker.log").exists() and "compose --env-file" in (self.tmp / "docker.log").read_text())     # nothing was started
        self.assertEqual(self.sh("up").returncode, 0)
        log = (self.tmp / "docker.log").read_text()
        self.assertIn(f"docker compose --env-file {self.run_dir}/env -f {PACK}/compose.yml up -d --wait", log)

    def test_health_says_healthy_unhealthy_or_disk_low_and_never_a_value(self):
        self.assertEqual(self.sh("health").stdout.strip(), "healthy")
        bad = self.sh("health", health="down")
        self.assertEqual((bad.stdout.strip(), bad.returncode), ("unhealthy", 1))
        low = self.sh("health", free=2)
        self.assertEqual((low.stdout.strip(), low.returncode), ("disk_low", 2))

    def test_no_secret_value_reaches_any_output_or_any_file_but_the_encrypted_and_the_tmpfs_ones(self):
        self.sh("init", OWNER)
        self.place_relay_key()
        self.sh("render-env")
        for p in self.tmp.rglob("*"):
            if p.is_file() and p.parent.name != "bin" and p.name != "env":
                self.assertNotIn(SENTINEL, p.read_text(errors="replace"), p.name)                                 # the encrypted credentials hold it only encoded
                self.assertNotIn(RELAY_SECRET, p.read_text(errors="replace"), p.name)


if __name__ == "__main__":
    unittest.main()
