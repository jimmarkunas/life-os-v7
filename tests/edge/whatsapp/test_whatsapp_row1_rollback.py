"""the pack rolls back to the old service on every failure (first half of the rollback cases)."""

import unittest

from tests.edge.whatsapp.row1_support import *  # noqa: F403


class RollbackTests(RollbackBase):
    def test_a_new_service_that_will_not_start(self):
        self.failure("fail-start")

    def test_no_socket(self):
        self.failure("no-socket")

    def test_a_socket_that_is_not_mode_660(self):
        self.failure("bad-socket-mode")

    def test_a_socket_that_does_not_answer_the_consumer(self):
        self.failure("silent")

    def test_a_service_that_dies(self):
        self.failure("dies")

    def test_a_crash_loop(self):
        self.failure("crashloop")

    def test_a_process_that_is_not_the_new_user(self):
        self.failure("runs-as-ubuntu")

    def test_no_connection_to_whatsapp_under_the_new_sandbox(self):
        self.failure("no-connection")

    def test_a_sandbox_property_that_is_not_set(self):
        self.failure("no-sandbox")


if __name__ == "__main__":
    unittest.main()
