"""Text checks only"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = ROOT / "lifeos" / "edge" / "concord" / "oracle"
DROPIN = ROOT / "lifeos" / "edge" / "buzz" / "oracle" / "lifeos-buzz-mcp.service.d" / "orchestrator-socket.conf"


def settings(path):
    return [line for line in path.read_text().splitlines() if line and not line.startswith("#")]


class Units(unittest.TestCase):
    def test_the_socket_is_a_unix_socket_for_concord_and_the_bridge_group_only(self):
        rows = settings(ORACLE / "lifeos-concord-gateway.socket")
        for want in (
            "ListenStream=/run/lifeos-concord-gateway/orchestrator.sock",
            "SocketUser=concord",
            "SocketGroup=buzz-mcp",
            "SocketMode=0660",
            "DirectoryMode=0755",
            "Accept=yes",
        ):
            self.assertIn(want, rows)
        self.assertFalse(
            [
                r
                for r in rows
                if re.match(r"ListenStream=(?!/run/)", r) or r.startswith(("ListenDatagram", "ListenFIFO"))
            ]
        )  # no TCP, no other listener

    def test_the_service_runs_as_concord_with_no_new_privileges_a_read_only_system_and_three_writable_paths(self):
        rows = settings(ORACLE / "lifeos-concord-gateway@.service")
        for want in (
            "User=concord",
            "ExecStart=/usr/bin/python3 -I /opt/lifeos-concord/gateway.py",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "PrivateTmp=yes",
            "StandardError=null",
            "ReadWritePaths=/home/concord/life-os-v7/.concord -/home/concord/.config/concord -/home/concord/.cache",
            "StandardInput=socket",
            "StandardOutput=socket",
        ):
            self.assertIn(want, rows)
        self.assertTrue(
            [r for r in rows if r.startswith("RuntimeMaxSec=")] and [r for r in rows if r.startswith("MemoryMax=")]
        )

    def test_the_bridge_drop_in_adds_af_unix_and_blocks_exactly_the_approved_sockets(self):
        rows = settings(DROPIN)
        self.assertEqual(rows[0], "[Service]")
        self.assertEqual(
            [r for r in rows if r.startswith("RestrictAddressFamilies")],
            ["RestrictAddressFamilies=", "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX"],
        )
        blocked = [r for r in rows if r.startswith("InaccessiblePaths=")][0].split("=", 1)[1].split()
        self.assertEqual(
            sorted(blocked),
            sorted(
                [
                    "-/run/dbus",
                    "-/run/snapd.socket",
                    "-/run/snapd-snap.socket",
                    "-/run/tailscale",
                    "-/run/tailscale-buzz",
                    "-/run/tailscale-duet",
                ]
            ),
        )
        self.assertEqual(len(rows), 4)  # nothing else of the bridge unit is touched
        self.assertNotIn(
            "/run/systemd/resolve", DROPIN.read_text().replace("# ", "").split("InaccessiblePaths=")[1].splitlines()[0]
        )  # blocking it would break the bridge's DNS

    def test_the_installer_has_the_four_commands_a_smoke_test_and_a_rollback_for_each(self):
        text = (ORACLE / "install-gateway.sh").read_text()
        for word in (
            "install)",
            "rollback)",
            "bridge-install)",
            "bridge-rollback)",
            "GATEWAY INSTALL FAILED",
            "BRIDGE INSTALL FAILED",
            "set -euo pipefail",
            "systemctl restart lifeos-buzz-mcp",
        ):
            self.assertIn(word, text)
        self.assertNotIn("curl", text)
        self.assertNotIn("ssh ", text)


if __name__ == "__main__":
    unittest.main()
