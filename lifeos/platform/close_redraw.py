"""LIFE-972: ask the Close card redraw worker for a redraw. Standard library only, so the Close gate and the Close app can both import it.

The worker is a socket-activated unit (lifeos/sources/apps/redraw_worker.py); `poke` connects to its socket and sends one byte. A poke queues in the socket even while a redraw runs, and a burst becomes one redraw."""
import os
import socket

SOCKET = "/run/lifeos-close-redraw/redraw.sock"


def available(path=SOCKET):
    return os.path.exists(path)


def poke(path=SOCKET):
    """Ask for one redraw. -> True when the worker's socket took it, False when there is no worker."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(2)
            conn.connect(path)
            conn.sendall(b"1")
        return True
    except OSError:
        return False
