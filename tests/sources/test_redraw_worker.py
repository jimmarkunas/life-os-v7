"""LIFE-972 (Jim, Nov 24 2026): the background card redraw. Real unix sockets, a fake redraw; no network, no token."""
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from lifeos.platform import close_redraw as P
from lifeos.sources.apps import redraw_worker as R


def listener(path):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(16)
    return srv


class Worker(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "r.sock")

    def test_poke_is_false_without_a_worker_and_true_with_one(self):
        self.assertFalse(P.poke(self.path))
        self.assertFalse(P.available(self.path))
        srv = listener(self.path)
        self.addCleanup(srv.close)
        self.assertTrue(P.available(self.path))
        self.assertTrue(P.poke(self.path))

    def test_a_burst_of_pokes_waiting_during_a_redraw_gets_exactly_one_more_redraw(self):
        srv = listener(self.path)
        self.addCleanup(srv.close)
        runs, gate, started = [], threading.Event(), threading.Event()

        def run():
            runs.append(time.monotonic())
            started.set()
            gate.wait(5)                                                                      # the first redraw is still running while the clicks come in

        done = []
        worker = threading.Thread(target=lambda: done.append(R.serve(srv, run, idle=0.6)))
        worker.start()
        self.assertTrue(P.poke(self.path))
        self.assertTrue(started.wait(5))
        for _ in range(5):                                                                    # five quick clicks during the running redraw
            self.assertTrue(P.poke(self.path))
        gate.set()
        worker.join(10)
        self.assertEqual((len(runs), done), (2, [0]))                                         # the first, then ONE for all five

    def test_pokes_that_arrive_together_before_the_worker_takes_them_make_one_redraw(self):
        srv = listener(self.path)
        self.addCleanup(srv.close)
        for _ in range(4):
            self.assertTrue(P.poke(self.path))
        runs = []
        self.assertEqual(R.serve(srv, lambda: runs.append(1), idle=0.3), 0)
        self.assertEqual(len(runs), 1)

    def test_an_idle_worker_exits_by_itself(self):
        srv = listener(self.path)
        self.addCleanup(srv.close)
        self.assertEqual(R.serve(srv, lambda: self.fail("no poke, no redraw"), idle=0.1), 0)

    def test_redraw_is_true_only_when_the_writers_own_readback_verified_and_never_raises(self):
        with mock.patch("lifeos.recruiters.outreach_card.run", return_value={"verified": True}) as run:
            self.assertTrue(R.redraw("tok", "src", "a" * 32, "https://close.example.test"))
        env = run.call_args.kwargs["environ"]
        self.assertEqual((env["RECRUITERS_STAGE_OUTCOME"], env["HIRING_TABLE_BLOCK_ID"], env["V7_CLOSE_BASE_URL"]), ("skipped", "a" * 32, "https://close.example.test"))
        self.assertTrue(run.call_args.args[1])                                                # live
        with mock.patch("lifeos.recruiters.outreach_card.run", return_value={"verified": False}):
            self.assertFalse(R.redraw("tok", "src", "a" * 32, "https://close.example.test"))
        with mock.patch("lifeos.recruiters.outreach_card.run", side_effect=RuntimeError("boom")):
            self.assertFalse(R.redraw("tok", "src", "a" * 32, "https://close.example.test"))


if __name__ == "__main__":
    unittest.main()
