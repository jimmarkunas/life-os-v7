"""One request to an Oracle service over a plain socket (the fit1 and gran1 frames), read to the server's close. Only the
connect is retried; once sent, the request is never resent (a timeout is the caller's fixed code; any other socket
error after the send returns b""). No half-close: Tailscale Serve drops the connection on a FIN.
"""
import socket
import time


def exchange(host, data, port, wait, max_reply, connect_failed, timed_out, sleep=None, attempts=2, pause=3, chunk=None, connect_timeout=15):
    """-> the reply bytes (b"" if the server closed with no output). `connect_failed()` and `timed_out()` return the
    exceptions to raise.
    """
    sleep = sleep or time.sleep
    s = None
    for attempt in range(attempts):
        try:
            s = socket.create_connection((host, port), timeout=connect_timeout)
            break
        except OSError:
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
