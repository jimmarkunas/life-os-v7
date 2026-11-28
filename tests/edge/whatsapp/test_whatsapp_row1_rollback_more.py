"""the pack rolls back to the old service on every failure (second half of the rollback cases, preflight refusals)."""

import unittest

from tests.edge.whatsapp.row1_support import *  # noqa: F403


class RollbackMoreTests(RollbackBase):
    def test_a_consumer_that_could_read_the_session(self):
        result = self.failure("ubuntu-reads")
        self.assertIn("failed=consumer_can_read_session", " ".join(result["readback"]))

    def test_a_socket_any_other_user_could_open(self):
        result = self.failure("others-connect")
        self.assertIn("failed=socket_open_to_others", " ".join(result["readback"]))

    def test_an_old_unit_that_will_not_stop_leaves_it_running_and_the_new_side_down(self):
        result = self.failure("old-wont-stop")
        self.assertIn("failed=old_still_active", " ".join(result["readback"]))

    def test_the_rollback_stops_the_new_units_before_it_starts_the_old_one(self):
        self.flag_on("no-socket")
        self.run_pack()
        calls = self.calls()
        self.assertLess(
            calls.index("stop lifeos-wa-attention.service"),
            max(i for i, c in enumerate(calls) if c.startswith("--user start")),
        )

    def test_the_new_user_not_able_to_run_node_stops_in_prepare_and_the_old_service_is_never_touched(self):
        self.flag_on("new-user-cannot-run")
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_2"), result)
        self.assertTrue(self.on("old-active") and self.on("old-enabled"))
        self.assertFalse([c for c in self.calls() if c.startswith("--user stop")])
        self.assertEqual(self.endpoint.read_text(), "# old endpoint\n")  # nothing of the consumer was changed yet

    def test_preflight_refusals_change_nothing(self):
        cases = {
            "gateway-down": "not_active",
            "user-has-shell": "new_user_has_shell",
            "user-in-groups": "new_user_in_other_groups",
        }
        for flag, word in cases.items():
            with self.subTest(flag):
                if flag.startswith("user-"):
                    (self.prefix / "fake" / "user").touch()
                (self.prefix / flag).touch()
                result = runner.execute("LIFE-643", self.sha8, self.host)
                self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"), result)
                self.assertIn(word, " ".join(result["readback"]))
                (self.prefix / flag).unlink()
                self.assertTrue(self.on("old-active"))
                self.assertFalse(self.new_state.exists())

    def test_an_unexpected_installed_file_or_a_changed_lock_stops_in_preflight(self):
        for target, word in (
            (self.endpoint, "endpoint_unexpected"),
            (self.stats, "stats_unexpected"),
            (self.old_app / "package-lock.json", "lock_unexpected"),
            (self.old_app / "attention.mjs", "attention_unexpected"),
        ):
            with self.subTest(word):
                original = target.read_text()
                target.write_text(original + "\n// edited by hand\n")
                result = runner.execute("LIFE-643", self.sha8, self.host)
                self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"), result)
                self.assertIn("failed=" + word, " ".join(result["readback"]))
                target.write_text(original)

    def test_a_link_in_the_old_session_folder_stops_in_preflight(self):
        (self.old_state / "link").symlink_to("chats.json")
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"))
        self.assertIn("failed=old_tree_symlink", " ".join(result["readback"]))
        self.assertTrue(self.on("old-active"))

    def test_an_old_unit_that_is_not_running_stops_in_preflight(self):
        (self.prefix / "fake" / "old-active").unlink()
        result = self.run_pack()
        self.assertEqual((result["outcome"], result["code"]), ("fail", "E_STEP_1"))
        self.assertIn("failed=old_unit_not_active", " ".join(result["readback"]))


if __name__ == "__main__":
    unittest.main()
