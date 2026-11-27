"""Hostinger MySQL connection through an SSH tunnel; every error is a fixed code.

One tunnel per process: opened on the first connect(), reused after a check, reopened after a failure, closed at exit.
Each connect() is its own short MySQL session through it.
"""

import atexit
from collections.abc import Iterator
from contextlib import contextmanager, suppress
import os
from pathlib import Path
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

# All six are GitHub Secrets. The database listens on the server's loopback only, reached through the tunnel.
FIELDS = ("SSH_PRIVATE_KEY", "DB_PASSWORD", "SSH_HOST", "SSH_PORT", "SSH_USER", "SSH_KNOWN_HOSTS", "DB_NAME", "DB_USER")
DB_REMOTE = "127.0.0.1:3306"
TRANSIENT = (
    "STORE_SSH_NETWORK",
    "STORE_SSH_TIMEOUT",
    "STORE_SSH_FAILED",
    "STORE_SSH_REFUSED",
    "STORE_SSH_BANNER",
    "STORE_SSH_TCP_TIMEOUT",
    "STORE_DB_CONNECT_FAILED",
)
RETRY_WAITS = (0, 4, 12, 30)  # seconds before attempts 1-4; auth and host-key errors are never retried
# An unanswered connection (STORE_SSH_TCP_TIMEOUT) means the host silently drops this runner, so the next wait is long;
# with the Oracle route configured the next attempt goes through Oracle's fixed address (ssh -W to the store's IP and port).
QUIET_WAITS = (0, 30, 60, 90)
JUMP_FIELDS = ("SSH_JUMP_KEY", "SSH_JUMP_HOST", "SSH_JUMP_KNOWN_HOSTS")
JUMP_USER = "v7-store-fwd"
NEVER_RETRIED = ("STORE_SSH_JUMP_AUTH", "STORE_SSH_JUMP_HOSTKEY", "STORE_SSH_JUMP_KEY_INVALID", "STORE_SSH_JUMP_DNS")
JITTER_SECONDS = (
    5  # extra wait before a process's FIRST connect and every retry, so parallel lanes do not open tunnels together
)


# A session lost mid-stage (MySQL 2013, 2006) is reconnected and a read is rerun once; a write or anything inside a
# transaction is never repeated.
LOST_CODES = (2006, 2013)
IN_TRANSACTION = 1
_READ = re.compile(
    r"\s*\(?\s*(?:SELECT|SHOW|DESCRIBE|EXPLAIN(?!\s+ANALYZE))\b", re.IGNORECASE
)  # EXPLAIN ANALYZE runs its statement: never retried


class StoreError(RuntimeError):
    """Message is always a fixed code, never host names, users or driver text."""


def _cfg() -> dict[str, str]:
    missing = [f for f in FIELDS if not os.environ.get("LIFEOS_ACQ_" + f)]
    if missing:
        raise StoreError("STORE_CONFIG_MISSING:" + ",".join(missing))
    return {f: os.environ["LIFEOS_ACQ_" + f] for f in FIELDS}


def _jump_cfg() -> dict[str, str] | None:
    values = {f: (os.environ.get("LIFEOS_ACQ_" + f) or "").strip() for f in JUMP_FIELDS}
    return values if all(values.values()) else None


_route: dict[str, bool] = {"jump": False}  # after a direct timeout, later tunnels in this process use Oracle


def _forced() -> bool:
    return (os.environ.get("LIFEOS_ACQ_SSH_VIA") or "").strip().lower() == "oracle"


def _ssh_code(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return "STORE_SSH_TIMEOUT"
    detail = (error.stderr or "") if isinstance(error, subprocess.CalledProcessError) else ""
    detail = (detail.decode("utf-8", "replace") if isinstance(detail, bytes) else detail).casefold()
    for code, needles in (
        ("HOSTKEY", ("host key verification failed", "host identification")),
        ("AUTH", ("permission denied", "no more authentication")),
        ("REFUSED", ("connection refused",)),
        ("BANNER", ("banner exchange", "kex_exchange_identification", "connection reset by", "connection closed by")),
        ("TCP_TIMEOUT", ("timed out",)),
        ("NETWORK", ("unreachable", "no route")),
        ("KEY_INVALID", ("load key", "libcrypto")),
    ):
        if any(n in detail for n in needles):
            return "STORE_SSH_" + code
    return "STORE_SSH_FAILED"


def _jitter() -> float:
    return random.uniform(0, JITTER_SECONDS)


_spread: list[bool] = [True]


_tunnel: dict[str, Any] = {"directory": None, "port": None, "exit": None, "check": None}
_lock = threading.Lock()


def _close_tunnel() -> None:
    exit_cmd, directory = _tunnel["exit"], _tunnel["directory"]
    _tunnel.update(directory=None, port=None, exit=None, check=None)
    with suppress(Exception):
        if exit_cmd is not None:
            subprocess.run(exit_cmd, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with suppress(Exception):
        if directory is not None:
            shutil.rmtree(directory)


atexit.register(_close_tunnel)


def _alive() -> bool:
    if _tunnel["check"] is None:
        return False
    try:
        subprocess.run(_tunnel["check"], check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001
        return False


def _proxy(jump: dict[str, str], directory: str, host: str, port: str) -> list[str]:
    try:
        ip = next(a[4][0] for a in socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM))
    except (OSError, StopIteration, ValueError):
        raise StoreError("STORE_SSH_JUMP_DNS") from None
    key, hosts = Path(directory, "jump_key"), Path(directory, "jump_known_hosts")
    for path, content in ((key, jump["SSH_JUMP_KEY"]), (hosts, jump["SSH_JUMP_KNOWN_HOSTS"])):
        path.touch(mode=0o600)
        path.write_text(content.rstrip() + "\n")
    # The relay's stderr goes to a private file, never the pipe this process reads (an inherited pipe never closes).
    return [
        "-o",
        f"ProxyCommand=ssh -F /dev/null -i {key} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={hosts}"
        f" -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=10 -W {ip}:{int(port)} {JUMP_USER}@{jump['SSH_JUMP_HOST']} 2>>{Path(directory, 'jump.err')}",
    ]


def _open_tunnel(cfg: dict[str, str], jump: dict[str, str] | None = None) -> None:
    directory = tempfile.mkdtemp(prefix="v7-store-")
    key, hosts = Path(directory, "key"), Path(directory, "known_hosts")
    for path, content in ((key, cfg["SSH_PRIVATE_KEY"]), (hosts, cfg["SSH_KNOWN_HOSTS"])):
        path.touch(mode=0o600)
        path.write_text(content.rstrip() + "\n")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        local_port = probe.getsockname()[1]
    control = str(Path(directory, "control"))
    opts = [
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={hosts}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
    ]
    _tunnel.update(
        directory=directory,
        port=local_port,
        exit=["ssh", "-F", "/dev/null", "-S", control, "-O", "exit", "--", cfg["SSH_HOST"]],
        check=["ssh", "-F", "/dev/null", "-S", control, "-O", "check", "--", cfg["SSH_HOST"]],
    )
    via = _proxy(jump, directory, cfg["SSH_HOST"], cfg["SSH_PORT"]) if jump else []
    try:
        subprocess.run(
            [
                "ssh",
                *opts,
                *via,
                "-M",
                "-S",
                control,
                "-fNT",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=15",
                "-i",
                str(key),
                "-p",
                cfg["SSH_PORT"],
                "-L",
                f"127.0.0.1:{local_port}:{DB_REMOTE}",
                "-l",
                cfg["SSH_USER"],
                "--",
                cfg["SSH_HOST"],
            ],
            check=True,
            timeout=30 if jump else 20,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as error:
        if jump and isinstance(error, subprocess.CalledProcessError):
            with suppress(OSError):
                error.stderr = (error.stderr or "") + "\n" + Path(directory, "jump.err").read_text(errors="replace")
        code = _ssh_code(error)
        raise StoreError(code.replace("STORE_SSH_", "STORE_SSH_JUMP_", 1) if jump else code) from None


def _lost(error: BaseException) -> bool:
    args: tuple[Any, ...] = getattr(error, "args", ())
    return (
        type(error).__name__ == "OperationalError"
        and bool(args)
        and args[0] in LOST_CODES
        and "timed out" not in str(args[1] if len(args) > 1 else "").casefold()
    )


class _Cursor:
    def __init__(self, connection: "_Connection", cursor: Any, args: tuple[tuple[Any, ...], dict[str, Any]]) -> None:
        self._connection, self._cursor, self._args = connection, cursor, args

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._cursor)

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._cursor.close()

    def execute(self, query: Any, args: Any = None) -> Any:
        try:
            return self._cursor.execute(query, args)
        except Exception as error:  # noqa: BLE001
            if not (
                _lost(error)
                and isinstance(query, str)
                and _READ.match(query)
                and not self._connection._in_transaction()
            ):
                raise
            self._connection._reconnect(error)
            self._cursor = self._connection._raw.cursor(*self._args[0], **self._args[1])
            print(f"store: reconnected after a lost session ({error.args[0]}); the read ran once more", file=sys.stderr)
            return self._cursor.execute(query, args)


class _Connection:
    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._raw.__exit__(*exc)

    def cursor(self, *args: Any, **kwargs: Any) -> _Cursor:
        return _Cursor(self, self._raw.cursor(*args, **kwargs), (args, kwargs))

    def _in_transaction(self) -> bool:
        status = getattr(self._raw, "server_status", None)
        return not isinstance(status, int) or bool(status & IN_TRANSACTION)

    def _reconnect(self, error: BaseException) -> None:
        """A fresh MySQL session through the process's tunnel (reopened if it died). Any failure re-raises the lost-session error."""
        try:
            with _lock:
                if not _alive():
                    _close_tunnel()
                    jump = _jump_cfg()
                    _open_tunnel(_cfg(), jump if jump and _route["jump"] else None)
                port = _tunnel["port"]
            self._raw.port = port
            self._raw.ping(reconnect=True)
        except Exception:  # noqa: BLE001
            raise error from None


@contextmanager
def connect() -> Iterator[_Connection]:
    cfg, jump = _cfg(), _jump_cfg()
    connection: Any
    last: str | None
    connection, last = None, None
    for attempt in range(len(RETRY_WAITS)):
        if (last == "STORE_SSH_TCP_TIMEOUT" or _forced()) and jump:
            _route["jump"] = True
        via = bool(jump) and _route["jump"]
        wait = (QUIET_WAITS if last == "STORE_SSH_TCP_TIMEOUT" and not via else RETRY_WAITS)[attempt]
        extra = _jitter() if attempt or _spread[0] else 0
        _spread[0] = False
        time.sleep(wait + extra)
        opened = False
        try:
            with _lock:
                if not _alive():
                    _close_tunnel()
                    _open_tunnel(cfg, jump if via else None)
                    opened = True
                port = _tunnel["port"]
            try:
                import pymysql

                connection = pymysql.connect(
                    host="127.0.0.1",
                    port=port,
                    user=cfg["DB_USER"],
                    password=cfg["DB_PASSWORD"],
                    database=cfg["DB_NAME"],
                    charset="utf8mb4",
                    autocommit=True,
                    connect_timeout=10,
                    read_timeout=20,
                    write_timeout=20,
                )
            except Exception:
                raise StoreError("STORE_DB_CONNECT_FAILED") from None
            if last or (via and opened):
                print(
                    f"store: connected on attempt {attempt + 1} of {len(RETRY_WAITS)}{' via oracle' if via else ''}{f' after {last}' if last else ''}",
                    file=sys.stderr,
                )
            break
        except StoreError as error:
            last = str(error)
            print(f"store: attempt {attempt + 1} of {len(RETRY_WAITS)} {error}", file=sys.stderr)
            with _lock:
                _close_tunnel()
            transient = str(error) in TRANSIENT or (
                str(error).startswith("STORE_SSH_JUMP_") and str(error) not in NEVER_RETRIED
            )
            if not transient or attempt == len(RETRY_WAITS) - 1:
                raise
    try:
        yield _Connection(connection)
    finally:
        with suppress(Exception):
            connection.close()
