"""Hostinger MySQL connection through an SSH tunnel (platform: no job knowledge). Every error is a fixed code.
One tunnel per process (LIFE-76 PR 4a, D207): it is opened on the first connect(), checked before reuse, reopened after a failure and closed
at exit; every connect() is its own short MySQL session through it, so no stage holds a database session across network I/O."""
import atexit
from contextlib import contextmanager, suppress
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

# All six are GitHub *Secrets* (masked in logs). Variables are NOT masked and this repo is public.
# The database listens on the server's loopback only, reached through the tunnel: host/port are constants.
FIELDS = ("SSH_PRIVATE_KEY", "DB_PASSWORD", "SSH_HOST", "SSH_PORT", "SSH_USER",
          "SSH_KNOWN_HOSTS", "DB_NAME", "DB_USER")
DB_REMOTE = "127.0.0.1:3306"
TRANSIENT = ("STORE_SSH_NETWORK", "STORE_SSH_TIMEOUT", "STORE_SSH_FAILED", "STORE_SSH_REFUSED", "STORE_SSH_BANNER", "STORE_SSH_TCP_TIMEOUT", "STORE_DB_CONNECT_FAILED")
RETRY_WAITS = (0, 4, 12, 30)     # seconds before attempts 1-4: a Hostinger network blip (D92; on 2026-10-17 a 36 s window was too short and lost a whole lane) must not fail a stage; auth and host-key errors are never retried
# LIFE-41 (Oct 19 2026, 43 runs): an unanswered connection (STORE_SSH_TCP_TIMEOUT) is the host silently dropping ONE runner for minutes while concurrent runners
# connect; none of 9 such series recovered on the short schedule, and every quick retry is another dropped packet. After one, the next wait is long and quiet.
QUIET_WAITS = (0, 30, 60, 90)
# LIFE-85: the quiet waits did not recover either (run 39352200579: 3.7 minutes). When the optional Oracle route is configured, the attempt after an unanswered
# connection goes through Oracle's fixed address instead: ssh -W to the store's one IP and port as the forward-only user, whose key can do nothing else
# (restrict,port-forwarding,permitopen). The store key, known_hosts and DB password stay here; Oracle relays the end-to-end SSH bytes and holds no store secret.
JUMP_FIELDS = ("SSH_JUMP_KEY", "SSH_JUMP_HOST", "SSH_JUMP_KNOWN_HOSTS")
JUMP_USER = "v7-store-fwd"
NEVER_RETRIED = ("STORE_SSH_JUMP_AUTH", "STORE_SSH_JUMP_HOSTKEY", "STORE_SSH_JUMP_KEY_INVALID", "STORE_SSH_JUMP_DNS")
JITTER_SECONDS = 5                 # LIFE-76: up to this much extra before a process's FIRST connect and before every retry, so lanes that start in the same second do not open their tunnels together (D206); later connects in the same stage add nothing (publish opens hundreds)


class StoreError(RuntimeError):
    """Message is always a fixed code - never host names, users, or driver text."""


def _cfg():
    missing = [f for f in FIELDS if not os.environ.get("LIFEOS_ACQ_" + f)]
    if missing:
        raise StoreError("STORE_CONFIG_MISSING:" + ",".join(missing))  # names of settings only, no values
    return {f: os.environ["LIFEOS_ACQ_" + f] for f in FIELDS}


def _jump_cfg():
    """The optional Oracle route, or None (any of the three settings missing): without it every connection is direct, as before."""
    values = {f: (os.environ.get("LIFEOS_ACQ_" + f) or "").strip() for f in JUMP_FIELDS}
    return values if all(values.values()) else None


_route = {"jump": False}           # once this process saw its direct connection go unanswered, every later tunnel (re)open uses Oracle too


def _forced():
    """V7_STORE_VIA=oracle (Jim's repository variable): start on the Oracle route, for a live proof or if the direct route ever fails outright."""
    return (os.environ.get("LIFEOS_ACQ_SSH_VIA") or "").strip().lower() == "oracle"


def _ssh_code(error):
    if isinstance(error, subprocess.TimeoutExpired):
        return "STORE_SSH_TIMEOUT"
    detail = (error.stderr or "") if isinstance(error, subprocess.CalledProcessError) else ""
    detail = (detail.decode("utf-8", "replace") if isinstance(detail, bytes) else detail).casefold()
    for code, needles in (("HOSTKEY", ("host key verification failed", "host identification")),
                          ("AUTH", ("permission denied", "no more authentication")),
                          ("REFUSED", ("connection refused",)),
                          # The server answered the TCP connection and then dropped it (sshd connection limit, rate limit) ...
                          ("BANNER", ("banner exchange", "kex_exchange_identification", "connection reset by", "connection closed by")),
                          # ... or never answered it at all (a firewall or route that drops the packets): different causes, so different codes (MON-1.5).
                          ("TCP_TIMEOUT", ("timed out",)),
                          ("NETWORK", ("unreachable", "no route")),
                          ("KEY_INVALID", ("load key", "libcrypto"))):
        if any(n in detail for n in needles):
            return "STORE_SSH_" + code
    return "STORE_SSH_FAILED"


def _jitter():
    return random.uniform(0, JITTER_SECONDS)


_spread = [True]                   # cleared by the first attempt of the process's first connect


_tunnel = {"directory": None, "port": None, "exit": None, "check": None}      # one SSH tunnel per process (LIFE-76 PR 4a); each connect() is a fresh MySQL session through it
_lock = threading.Lock()                                                      # stages that connect from threads open the tunnel once


def _close_tunnel():
    """Close the process's tunnel (at exit, or after a failed attempt so the next one starts clean)."""
    exit_cmd, directory = _tunnel["exit"], _tunnel["directory"]
    _tunnel.update(directory=None, port=None, exit=None, check=None)
    with suppress(Exception):
        if exit_cmd is not None:
            subprocess.run(exit_cmd, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with suppress(Exception):
        if directory is not None:
            shutil.rmtree(directory)


atexit.register(_close_tunnel)


def _alive():
    """True when the open tunnel's master still answers (a local control-socket check, no network round trip)."""
    if _tunnel["check"] is None:
        return False
    try:
        subprocess.run(_tunnel["check"], check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:                                                         # noqa: BLE001 - any failure means reopen
        return False


def _proxy(jump, directory, host, port):
    """-> the ProxyCommand option for the Oracle route: the store's own IPv4 (the forward-only key may open exactly that address and port) through Oracle."""
    try:
        ip = next(a[4][0] for a in socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM))
    except (OSError, StopIteration, ValueError):
        raise StoreError("STORE_SSH_JUMP_DNS") from None
    key, hosts = Path(directory, "jump_key"), Path(directory, "jump_known_hosts")
    for path, content in ((key, jump["SSH_JUMP_KEY"]), (hosts, jump["SSH_JUMP_KNOWN_HOSTS"])):
        path.touch(mode=0o600)
        path.write_text(content.rstrip() + "\n")
    return ["-o", f"ProxyCommand=ssh -F /dev/null -i {key} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={hosts}"
                  f" -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=10 -W {ip}:{int(port)} {JUMP_USER}@{jump['SSH_JUMP_HOST']}"]


def _open_tunnel(cfg, jump=None):
    directory = tempfile.mkdtemp(prefix="v7-store-")
    key, hosts = Path(directory, "key"), Path(directory, "known_hosts")
    for path, content in ((key, cfg["SSH_PRIVATE_KEY"]), (hosts, cfg["SSH_KNOWN_HOSTS"])):
        path.touch(mode=0o600)
        path.write_text(content.rstrip() + "\n")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        local_port = probe.getsockname()[1]
    control = str(Path(directory, "control"))
    opts = ["-F", "/dev/null", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={hosts}",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10"]
    _tunnel.update(directory=directory, port=local_port,
                   exit=["ssh", "-F", "/dev/null", "-S", control, "-O", "exit", "--", cfg["SSH_HOST"]],
                   check=["ssh", "-F", "/dev/null", "-S", control, "-O", "check", "--", cfg["SSH_HOST"]])
    via = _proxy(jump, directory, cfg["SSH_HOST"], cfg["SSH_PORT"]) if jump else []     # after the tunnel owns the directory, so a refusal here still cleans it up
    try:
        subprocess.run(["ssh", *opts, *via, "-M", "-S", control, "-fNT", "-o", "ExitOnForwardFailure=yes",
                        "-o", "ServerAliveInterval=15", "-i", str(key), "-p", cfg["SSH_PORT"],
                        "-L", f"127.0.0.1:{local_port}:{DB_REMOTE}", "-l", cfg["SSH_USER"], "--", cfg["SSH_HOST"]],
                       check=True, timeout=30 if jump else 20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except Exception as error:
        code = _ssh_code(error)
        raise StoreError(code.replace("STORE_SSH_", "STORE_SSH_JUMP_", 1) if jump else code) from None


@contextmanager
def connect():
    cfg, jump = _cfg(), _jump_cfg()
    connection, last = None, None
    for attempt in range(len(RETRY_WAITS)):
        if (last == "STORE_SSH_TCP_TIMEOUT" or _forced()) and jump:
            _route["jump"] = True                                       # this runner is being dropped: go through Oracle now, and for the rest of the process
        via = bool(jump) and _route["jump"]
        wait = (QUIET_WAITS if last == "STORE_SSH_TCP_TIMEOUT" and not via else RETRY_WAITS)[attempt]
        extra = _jitter() if attempt or _spread[0] else 0
        _spread[0] = False
        time.sleep(wait + extra)
        try:
            with _lock:
                if not _alive():
                    _close_tunnel()
                    _open_tunnel(cfg, jump if via else None)
                port = _tunnel["port"]
            try:
                import pymysql
                connection = pymysql.connect(host="127.0.0.1", port=port, user=cfg["DB_USER"],
                                             password=cfg["DB_PASSWORD"], database=cfg["DB_NAME"], charset="utf8mb4",
                                             autocommit=True, connect_timeout=10, read_timeout=20, write_timeout=20)
            except Exception:
                raise StoreError("STORE_DB_CONNECT_FAILED") from None
            if last:
                print(f"store: connected on attempt {attempt + 1} of {len(RETRY_WAITS)}{' via oracle' if via else ''} after {last}", file=sys.stderr)   # a recovery is counted, so the route can be judged
            break
        except StoreError as error:
            last = str(error)
            print(f"store: attempt {attempt + 1} of {len(RETRY_WAITS)} {error}", file=sys.stderr)          # the fixed code of every failed attempt, so a lane that gives up shows whether all three failed alike
            with _lock:
                _close_tunnel()                                                                         # a half-open tunnel must not block the next attempt
            transient = str(error) in TRANSIENT or (str(error).startswith("STORE_SSH_JUMP_") and str(error) not in NEVER_RETRIED)
            if not transient or attempt == len(RETRY_WAITS) - 1:
                raise
    try:
        yield connection
    finally:
        with suppress(Exception):
            connection.close()                                                                          # the session ends; the tunnel stays for the next connect
