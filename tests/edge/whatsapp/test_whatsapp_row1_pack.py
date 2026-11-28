"""the pack that moves WhatsApp Attention to its own user `lifeos-wa`: its shape, a good run, an interrupt, the helper
and the consumer (rollbacks are in test_whatsapp_row1_rollback and test_whatsapp_row1_rollback_more)."""

import unittest

from tests.edge.whatsapp.row1_support import *  # noqa: F403


class PackShapeTests(Fixture):
    def test_the_pack_is_valid_and_declares_exactly_what_it_changes(self):
        manifest = runner.load_manifest(str(self.pack))
        self.assertEqual(manifest["ticket"], "LIFE-643")
        self.assertEqual(
            manifest["services"], ["lifeos-wa-attention.socket", "lifeos-wa-attention.service"]
        )  # socket first: stopping it stops the service too
        self.assertEqual(manifest["files"], [str(self.endpoint), str(self.stats)])
        self.assertEqual(len(manifest["steps"]), 4)
        for name, repo in (("lifeos-wa-attention.service", UNIT), ("lifeos-wa-attention.socket", SOCKET_UNIT)):
            self.assertEqual((self.pack / "files" / name).read_text(), repo.read_text())

    def test_the_real_pack_builds_without_a_prefix_and_passes_the_runners_own_check(self):
        out = self.tmp / "real"
        subprocess.run([sys.executable, str(BUILDER), str(out)], cwd=ROOT, check=True, capture_output=True)
        done = subprocess.run(
            [sys.executable, str(ROOT / "lifeos" / "edge" / "runner" / "runner.py"), "check", str(out)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("PACK ok LIFE-643 steps=4 restore_files=2 restore_services=2", done.stdout)
        self.assertFalse((out / "files" / "ROOT").exists())
        text = (out / "files" / "common.sh").read_text()
        for pinned in (
            "e9686bdd23021e86562619c4c2303bdc259d51481f45ae6126d16d79f435e1af",
            "6e343b8f093a860fc70c06938ef19d34659074daa2e3178865018fa7ce948e7d",
        ):
            self.assertIn(pinned, text)

    def test_the_new_unit_has_the_full_sandbox_and_only_its_own_state_folder_is_writable(self):
        lines = UNIT.read_text().splitlines()
        for want in (
            "User=lifeos-wa",
            "ProtectSystem=strict",
            "ReadWritePaths=/var/lib/lifeos-wa",
            "PrivateTmp=yes",
            "ProtectHome=yes",
            "ProtectKernelTunables=yes",
            "ProtectKernelModules=yes",
            "ProtectControlGroups=yes",
            "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
            "RestrictNamespaces=yes",
            "LockPersonality=yes",
            "RestrictRealtime=yes",
            "RestrictSUIDSGID=yes",
            "SystemCallArchitectures=native",
            "SystemCallFilter=@system-service",
            "SystemCallErrorNumber=EPERM",
            "NoNewPrivileges=yes",
            "MemoryMax=400M",
            "UMask=0077",
            "Restart=on-failure",
            "ExecStart=/opt/lifeos-wa/bin/node /opt/lifeos-wa/app/attention.mjs serve",
            "ExecStopPost=+/opt/lifeos-wa/bin/resume-old.sh",
            "Requires=lifeos-wa-attention.socket",
        ):
            self.assertIn(want, lines)
        self.assertEqual([x for x in lines if x.startswith("ReadWritePaths")], ["ReadWritePaths=/var/lib/lifeos-wa"])
        sock = SOCKET_UNIT.read_text().splitlines()
        for want in (
            "ListenStream=/run/lifeos-wa/attention.sock",
            "SocketUser=lifeos-wa",
            "SocketGroup=ubuntu",
            "SocketMode=0660",
        ):
            self.assertIn(want, sock)

    def test_no_download_install_or_new_code_in_any_step(self):
        text = "".join(
            p.read_text()
            for p in list((PACK_SRC / "steps").glob("*.sh"))
            + [PACK_SRC / "files" / "common.sh", PACK_SRC / "files" / "resume-old.sh"]
        )
        for banned in ("npm ", "curl ", "wget ", "apt", "pip ", "git clone", "sudo "):
            self.assertNotIn(banned, text)

    def test_only_the_old_user_unit_and_the_new_units_are_ever_started_or_stopped(self):
        self.run_pack()
        verbs = [c.replace("--user ", "") for c in self.calls()]
        verbs = [c for c in verbs if c.split()[0] in ("start", "stop", "enable", "disable", "restart")]
        for call in verbs:
            self.assertTrue(
                any(name in call for name in ("lifeos-whatsapp-attention.service", "lifeos-wa-attention")), call
            )


class GoodRunTests(Fixture):
    def test_a_good_run_switches_proves_and_reads_back(self):
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("ok", None), result)
        # the new side
        self.assertTrue(
            self.on("new-active")
            and self.on("enabled-lifeos-wa-attention.service")
            and self.on("enabled-lifeos-wa-attention.socket")
        )
        copied, original = tree_listing(self.new_state), self.before_old
        self.assertEqual(
            {k: v[2:] for k, v in copied.items() if v[0] == "f"}, {k: v[2:] for k, v in original.items() if v[0] == "f"}
        )  # same names and sizes
        self.assertEqual({v[1] for v in copied.values() if v[0] == "d"}, {0o700})
        self.assertEqual({v[1] for v in copied.values() if v[0] == "f"}, {0o600})
        self.assertEqual(stat.S_IMODE(self.new_home.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.new_home / ".config" / "lifeos-whatsapp" / "hash-key").stat().st_mode), 0o600
        )
        self.assertEqual((self.new_home / ".config" / "lifeos-whatsapp" / "hash-key").read_text(), "NOT-A-REAL-KEY")
        # the old side: stopped, disabled, folder and unit file untouched
        self.assertFalse(self.on("old-active") or self.on("old-enabled"))
        self.assertEqual(tree_listing(self.old_state), self.before_old)
        self.assertEqual(self.old_unit.read_text(), OLD_UNIT)
        self.assertTrue((self.old_state / "attention.sock").exists())
        # the program tree: root-owned layout, the pinned tree copied with its link and exec bit, new app files from.
        app = self.prefix / "opt" / "lifeos-wa"
        self.assertTrue((app / "bin" / "node").exists() and (app / "bin" / "resume-old.sh").exists())
        self.assertEqual(
            (app / "app" / "attention.mjs").read_text(), (ROOT / "lifeos/whatsapp/oracle/attention.mjs").read_text()
        )
        self.assertTrue((app / "app" / "node_modules" / ".bin" / "baileys").is_symlink())
        self.assertEqual(stat.S_IMODE((app / "app" / "node_modules" / "baileys" / "cli.js").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((app / "app" / "attention.mjs").stat().st_mode), 0o644)
        # the consumer files are the repo's, still private
        self.assertEqual(self.endpoint.read_text(), (ROOT / "lifeos/imessage/alerts_endpoint.py").read_text())
        self.assertEqual(self.stats.read_text(), (ROOT / "lifeos/oraclecloud/oracle/stats.mjs").read_text())
        self.assertEqual(stat.S_IMODE(self.endpoint.stat().st_mode), 0o600)
        self.assertFalse(self.flag.exists())
        readback = " | ".join(result["readback"])
        for want in (
            "new_user=absent",
            "program_tree entries=",
            "consumer_files=installed",
            "old_stopped=yes",
            "copy_check same entries=",
            "proof new_pid_user_is_new_user=yes",
            "socket_mode=660",
            "endpoint_health=CURRENT",
            "consumer_read_session=denied",
            "other_users=refused",
            "switched=yes old_disabled=yes new_enabled=yes",
            "new_unit_active=active enabled=enabled",
            "main_pid_user=lifeos-wa uid_is_ubuntu=no",
            "ProtectSystem=strict",
            "socket path=/run/lifeos-wa/attention.sock kind=socket",
            "consumer_connect_bytes=",
            "ubuntu_lists_new_state=denied",
            "ubuntu_lists_new_home=denied",
            "nobody_connects=no",
            "old_unit_active=inactive old_unit_enabled=disabled old_unit_file_present=yes",
            "switch_flag_gone=yes",
            "user_shell=/usr/sbin/nologin",
        ):
            self.assertIn(want, readback)
        for place in (readback, json.dumps(result)):
            self.assertNotIn("NOT-A-REAL", place)

    def test_the_old_unit_is_stopped_before_the_session_is_copied_and_disabled_only_after_the_new_one_is_proven(self):
        self.run_pack()
        order = [
            c
            for c in self.calls()
            if c.startswith("--user ") and ("stop" in c or "disable" in c) or c.startswith(("start", "enable"))
        ]
        self.assertEqual(order[0], "--user stop lifeos-whatsapp-attention.service")
        self.assertLess(
            order.index("start lifeos-wa-attention.service"),
            order.index("--user disable lifeos-whatsapp-attention.service"),
        )
        self.assertLess(
            order.index("--user disable lifeos-whatsapp-attention.service"),
            order.index("enable lifeos-wa-attention.socket lifeos-wa-attention.service"),
        )

    def test_a_second_run_after_a_good_one_stops_in_the_preflight_and_changes_nothing(self):
        self.assertEqual(self.run_pack()["outcome"], "ok")
        again = self.run_pack()
        self.assertEqual((again["outcome"], again["code"]), ("fail", "E_STEP_1"))
        self.assertTrue(self.on("new-active"))


class InterruptTests(Fixture):
    """The runner kills a step's process group with SIGKILL on Ctrl-C or a timeout, so no trap in a step can run."""

    def drive(self, flag):
        self.flag_on(flag)
        driver = (
            "import subprocess, sys; from lifeos.edge.runner import runner; fake = sys.argv[3]\n"
            "def sc(*a):\n    d = subprocess.run([fake, *a], capture_output=True, text=True); return d.returncode, (d.stdout.split() or [''])[0]\n"
            "h = runner.Host(state=sys.argv[1], inbox=sys.argv[2], require_root=False, systemctl=sc)\n"
            "sys.exit(runner.main(sys.argv[4:], h))"
        )
        return subprocess.run(
            [sys.executable, "-c", driver, self.state, self.inbox, self.fake, "LIFE-643", self.sha8],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_a_kill_after_the_new_service_started_brings_the_old_one_back(self):
        done = self.drive("interrupt")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "RESULT fail E_INTERRUPTED\n", ""))
        self.assertTrue(self.on("old-active") and self.on("old-enabled"))
        self.assertFalse(self.on("new-active") or self.on("sock-active"))
        self.assertFalse(self.flag.exists())
        self.assertEqual(tree_listing(self.old_state), self.before_old)  # the old folder was never touched
        self.assertEqual(
            self.endpoint.read_text(), "# old endpoint\n"
        )  # the runner restored the declared consumer files
        self.assertEqual(self.stats.read_text(), "// old stats\n")
        self.assertTrue(self.new_state.exists())

    def test_a_kill_between_the_old_stop_and_the_new_start_leaves_the_old_service_stopped_but_intact_and_one_command_restarts_it(
        self,
    ):
        done = self.drive("interrupt-after-old-stop")
        self.assertEqual((done.returncode, done.stdout), (1, "RESULT fail E_INTERRUPTED\n"))
        self.assertFalse(self.on("old-active"))
        self.assertTrue(self.on("old-enabled"))  # still enabled: a reboot would also bring it back
        self.assertFalse(self.on("new-active"))
        self.assertEqual(tree_listing(self.old_state), self.before_old)
        self.assertTrue(self.flag.exists())  # the flag shows the state
        self.assertEqual(
            subprocess.run([self.fake, "--user", "start", "lifeos-whatsapp-attention.service"]).returncode, 0
        )
        self.assertTrue(self.on("old-active"))

    def test_resume_old_does_nothing_without_the_flag(self):
        (self.prefix / "fake" / "old-active").unlink()
        env = {**os.environ, "WA_ROOT": str(self.prefix)}
        subprocess.run(["sh", str(PACK_SRC / "files" / "resume-old.sh")], env=env, check=True)
        self.assertFalse(self.on("old-active"))
        self.flag.parent.mkdir(parents=True, exist_ok=True)
        self.flag.touch()
        subprocess.run(["sh", str(PACK_SRC / "files" / "resume-old.sh")], env=env, check=True)
        self.assertTrue(self.on("old-active") and self.on("old-enabled"))
        self.assertFalse(self.flag.exists())


class HelperTests(unittest.TestCase):
    """wa_row1.py checks that a pack-level test cannot stage (a second user needs root)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        (self.src / "auth").mkdir(parents=True)
        (self.src / "auth" / "a.json").write_text("NOT-A-REAL-SECRET")
        (self.src / "chats.json").write_text("{}")
        (self.src / "chats.json").chmod(0o664)
        self.server = Server(str(self.src / "attention.sock"), str(self.tmp / "silent"))
        self.server.start()
        self.addCleanup(self.server.sock.close)

    def wa(self, *args, no_chown=False):
        done = subprocess.run(
            [sys.executable, "-I", str(WA_ROW1), *(["--no-chown"] if no_chown else []), *args],
            capture_output=True,
            text=True,
        )
        return done.returncode, done.stdout.strip()

    def test_copy_session_skips_the_socket_sets_700_600_and_changes_the_names_and_sizes_of_nothing(self):
        dst = self.tmp / "home" / ".local" / "state" / "wa"
        code, out = self.wa("copy-session", str(self.src), str(dst), str(os.getuid()), str(os.getgid()))
        self.assertEqual((code, out), (0, "copied files=2 dirs=2 bytes=%d" % (len("NOT-A-REAL-SECRET") + 2)))
        self.assertFalse((dst / "attention.sock").exists())
        self.assertEqual(self.wa("same", str(self.src), str(dst)), (0, "same entries=4"))
        self.assertEqual({stat.S_IMODE(p.stat().st_mode) for p in dst.rglob("*") if p.is_file()}, {0o600})
        self.assertEqual(
            {stat.S_IMODE(p.stat().st_mode) for p in [dst, dst / "auth", dst.parent, dst.parent.parent]}, {0o700}
        )  # parents made on the way are private too
        self.assertEqual(
            self.wa("copy-session", str(self.src), str(dst), str(os.getuid()), str(os.getgid())), (1, "dst_exists")
        )
        (dst / "chats.json").write_text("{}{}")
        self.assertEqual(self.wa("same", str(self.src), str(dst)), (1, "differ"))

    def test_wipe_only_removes_folders_under_the_new_home(self):
        home, other = self.tmp / "home", self.tmp / "other"
        (home / "x").mkdir(parents=True)
        other.mkdir()
        self.assertEqual(self.wa("wipe", str(other), str(home)), (1, "refused"))
        self.assertEqual(self.wa("wipe", str(home), str(home)), (1, "refused"))
        self.assertEqual(self.wa("wipe", str(home / "x"), str(home)), (0, "wiped"))
        self.assertTrue(other.exists() and home.exists() and not (home / "x").exists())

    def test_install_tree_keeps_links_and_exec_bits_and_removes_group_and_other_write(self):
        tree = self.tmp / "nm"
        (tree / "p").mkdir(parents=True)
        (tree / "p" / "run").write_text("x")
        (tree / "p" / "run").chmod(0o775)
        (tree / "p" / "data").write_text("y")
        (tree / "p" / "data").chmod(0o666)
        (tree / "ln").symlink_to("p/run")
        out = self.tmp / "out"
        code, said = self.wa("install-tree", str(tree), str(out), no_chown=True)
        self.assertEqual((code, said), (0, "installed entries=5 bytes=2"))
        self.assertTrue((out / "ln").is_symlink())
        self.assertEqual(
            (
                stat.S_IMODE((out / "p" / "run").stat().st_mode),
                stat.S_IMODE((out / "p" / "data").stat().st_mode),
                stat.S_IMODE((out / "p").stat().st_mode),
            ),
            (0o755, 0o644, 0o755),
        )
        self.assertEqual(self.wa("tree-summary", str(tree)), self.wa("tree-summary", str(out)))

    def test_a_folder_not_owned_by_the_owner_or_with_a_link_is_refused_and_the_socket_is_counted_never_printed(self):
        self.assertEqual(self.wa("check", str(os.getuid()), str(self.src)), (0, ""))
        self.assertEqual(self.wa("check", str(os.getuid() + 1), str(self.src)), (1, "owner"))
        (self.src / "ln").symlink_to("chats.json")
        self.assertEqual(self.wa("check", str(os.getuid()), str(self.src)), (1, "symlink"))
        self.assertEqual(self.wa("connect", str(self.src / "attention.sock")), (0, str(len(json.dumps(SNAPSHOT)) + 1)))

    def test_canlist_reports_denied_for_a_folder_that_cannot_be_listed(self):
        self.assertEqual(self.wa("canlist", str(self.tmp / "nowhere")), (1, "denied"))
        self.assertEqual(self.wa("canlist", str(self.src)), (0, "readable"))


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        from lifeos.imessage import alerts_endpoint as ep

        self.ep = ep
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def serve(self, name, snapshot):
        path = str(self.tmp / name)
        server = Server(path, str(self.tmp / "silent"))
        server.start()
        self.addCleanup(server.sock.close)
        return path

    def test_only_the_lifeos_wa_socket_is_tried_and_a_missing_one_is_unavailable_never_zero(self):
        new = self.serve("n.sock", SNAPSHOT)
        with unittest_patch(self.ep, WHATSAPP_SOCKET_WA=new):
            self.assertEqual(self.ep.whatsapp("0" * 64)["health"], "CURRENT")
        with unittest_patch(self.ep, WHATSAPP_SOCKET_WA=str(self.tmp / "gone.sock")):
            self.assertEqual(self.ep.whatsapp("0" * 64)["health"], "UNAVAILABLE")
        self.assertFalse(hasattr(self.ep, "WHATSAPP_SOCKET"))

    def test_an_explicit_path_is_the_only_one_tried(self):
        old = self.serve("o.sock", SNAPSHOT)
        with unittest_patch(self.ep, WHATSAPP_SOCKET_WA=old):
            self.assertEqual(self.ep.whatsapp("0" * 64, str(self.tmp / "gone.sock"))["health"], "UNAVAILABLE")

    def test_the_oracle_card_script_counts_only_the_lifeos_wa_unit(self):  # cleanup: the old ubuntu user unit is gone
        text = (ROOT / "lifeos" / "oraclecloud" / "oracle" / "stats.mjs").read_text()
        self.assertIn('["is-active", "lifeos-wa-attention.service"]', text)
        self.assertNotIn("lifeos-whatsapp-attention", text)
        self.assertNotIn("UNITS.whatsapp", text)


if __name__ == "__main__":
    unittest.main()
