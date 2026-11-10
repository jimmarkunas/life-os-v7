"""LIFE-526: the shared read-back wait and the shared Oracle socket exchange, with the retry-negative cases: a request that was sent is never sent again, and nothing is retried that may have had an effect."""
import socket
import unittest
from unittest import mock

from lifeos.platform import readback, socket_exchange


class Boom(RuntimeError):
    pass


class ReadBack(unittest.TestCase):
    def test_a_match_on_the_first_read_does_not_pause(self):
        pauses = []
        self.assertEqual(readback.confirmed(lambda: "ok", lambda v: v == "ok", sleep=pauses.append), (True, "ok"))
        self.assertEqual(pauses, [])

    def test_it_reads_again_with_a_pause_before_every_read_after_the_first_and_stops_at_the_match(self):
        seen, pauses = iter(["old", "old", "new", "unused"]), []
        got = readback.confirmed(lambda: next(seen), lambda v: v == "new", sleep=pauses.append)
        self.assertEqual(got, (True, "new"))
        self.assertEqual(pauses, [3, 3])

    def test_it_gives_up_after_the_attempts_and_hands_back_the_last_read(self):
        reads, pauses = [], []
        got = readback.confirmed(lambda: reads.append(1) or len(reads), lambda v: False, attempts=4, pause=3, sleep=pauses.append)
        self.assertEqual((got, len(reads), pauses), ((False, 4), 4, [3, 3, 3]))                    # exactly 4 reads, never more

    def test_it_only_calls_what_it_is_given_so_it_cannot_repeat_a_write(self):
        calls = {"read": 0}
        readback.confirmed(lambda: calls.__setitem__("read", calls["read"] + 1), lambda v: False, sleep=lambda s: None)
        self.assertEqual(calls, {"read": 4})

    def test_a_patched_time_sleep_is_honoured(self):
        with mock.patch("time.sleep") as patched:
            readback.confirmed(lambda: 0, lambda v: False, attempts=2, pause=5)
        patched.assert_called_once_with(5)


class FakeSock:
    def __init__(self, chunks=(), recv_error=None):
        self.chunks, self.sent, self.timeout, self.recv_error, self.sizes = list(chunks), [], None, recv_error, []

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def settimeout(self, t): self.timeout = t
    def sendall(self, data): self.sent.append(data)

    def recv(self, n):
        self.sizes.append(n)
        if self.recv_error:
            raise self.recv_error
        return self.chunks.pop(0) if self.chunks else b""


def run(sock_or_errors, **kw):
    pauses, calls = [], []
    items = list(sock_or_errors)

    def connect(addr, timeout):
        calls.append(addr)
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    args = dict(host="oracle.example", data=b"FRAME", port=47341, wait=5, max_reply=100,
                connect_failed=lambda: Boom("CONNECT_FAILED"), timed_out=lambda: Boom("TIMEOUT"), sleep=pauses.append)
    args.update(kw)
    with mock.patch("socket.create_connection", connect):
        try:
            return socket_exchange.exchange(**args), pauses, calls, None
        except Boom as error:
            return None, pauses, calls, error


class Exchange(unittest.TestCase):
    def test_a_failed_connect_is_tried_again_after_a_pause_and_the_request_is_sent_once(self):
        good = FakeSock([b"REPLY"])
        out, pauses, calls, error = run([OSError("down"), good])
        self.assertEqual((out, pauses, len(calls), good.sent, error), (b"REPLY", [3], 2, [b"FRAME"], None))

    def test_when_no_connect_opens_nothing_was_sent_and_the_callers_code_is_raised(self):
        out, pauses, calls, error = run([TimeoutError("t"), OSError("down")])
        self.assertEqual((out, str(error), len(calls), pauses), (None, "CONNECT_FAILED", 2, [3]))

    def test_a_timeout_after_the_send_is_not_retried_and_the_request_is_not_sent_again(self):
        sock = FakeSock(recv_error=TimeoutError("slow"))
        out, pauses, calls, error = run([sock, FakeSock([b"NEVER"])])
        self.assertEqual((out, str(error), len(calls), sock.sent, pauses), (None, "TIMEOUT", 1, [b"FRAME"], []))

    def test_another_socket_error_after_the_send_returns_nothing_and_does_not_resend(self):
        sock = FakeSock(recv_error=ConnectionResetError("reset"))
        out, pauses, calls, error = run([sock, FakeSock([b"NEVER"])])
        self.assertEqual((out, len(calls), sock.sent, error), (b"", 1, [b"FRAME"], None))

    def test_the_default_reads_at_most_what_is_left_and_a_fixed_chunk_can_be_asked_for(self):
        sock = FakeSock([b"abc", b"de"])
        self.assertEqual(run([sock])[0], b"abcde")
        self.assertEqual(sock.sizes, [101, 98, 96])                                    # max_reply + 1 - already read
        big = FakeSock([b"x"])
        run([big], chunk=1 << 20)
        self.assertEqual(big.sizes[0], 1 << 20)

    def test_the_reply_stops_growing_past_the_limit(self):
        sock = FakeSock([b"a" * 60, b"b" * 60, b"c" * 60])
        out = run([sock], max_reply=100)[0]
        self.assertGreater(len(out), 100)                                              # the caller sees it is over the limit
        self.assertEqual(len(sock.sizes), 2)                                            # and the loop stopped

    def test_the_raised_message_carries_no_host_port_or_request_text(self):
        _, _, _, error = run([OSError("down"), OSError("down")])
        for piece in ("oracle.example", "47341", "FRAME", "down"):
            self.assertNotIn(piece, str(error))

    def test_it_works_against_a_real_local_socket(self):
        server = socket.create_server(("127.0.0.1", 0))
        port = server.getsockname()[1]
        import threading

        def serve():
            conn, _ = server.accept()
            with conn:
                conn.recv(100)
                conn.sendall(b'{"ok":1}')
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        try:
            got = socket_exchange.exchange("127.0.0.1", b"hi", port, 5, 1000, lambda: Boom("C"), lambda: Boom("T"))
        finally:
            server.close()
        self.assertEqual(got, b'{"ok":1}')


if __name__ == "__main__":
    unittest.main()
