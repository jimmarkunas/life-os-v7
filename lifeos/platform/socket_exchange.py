"""LIFE-526: one request to an Oracle service over a plain socket (the fit1 and gran1 frames), read to the server's close.

The ONLY thing retried is the connect: a connection that cannot be opened has sent nothing, so opening it again is safe. Once the request is sent it is never sent again, whatever happens
(a timeout is the caller's fixed code; any other socket error after the send returns b"" = closed with no output). No half-close (Tailscale Serve drops the connection on a FIN).
The caller supplies the exceptions to raise, so each protocol keeps its own fixed codes. Prints nothing; no host, port or content in any message."""
import socket
import time


def exchange(host, data, port, wait, max_reply, connect_failed, timed_out, sleep=None, attempts=2, pause=3, chunk=None, connect_timeout=15):
    """-> the reply bytes (b"" when the server closed with no output). `connect_failed()` and `timed_out()` return the exception to raise. `chunk` None reads at most what is left of max_reply + 1."""
    sleep = sleep or time.sleep
    s = None
    for attempt in range(attempts):
        try:
            s = socket.create_connection((host, port), timeout=connect_timeout)
            break
        except OSError:                                          # TimeoutError included: the request was never sent
            if attempt + 1 < attempts:
                sleep(pause)
    if s is None:
        raise connect_failed()
    try:
        with s:
            s.settimeout(wait)
            s.sendall(data)
            out = b""
            while len(out) <= max_reply:
                piece = s.recv(chunk if chunk else max_reply + 1 - len(out))
                if not piece:
                    break
                out += piece
            return out
    except TimeoutError:
        raise timed_out() from None
    except OSError:
        return b""
