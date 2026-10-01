"""Hostinger MySQL connection through an SSH tunnel (platform: no job knowledge). Every error is a fixed code."""
from contextlib import contextmanager, suppress
import os
from pathlib import Path
import socket
import subprocess
import tempfile

# All six are GitHub *Secrets* (masked in logs). Variables are NOT masked and this repo is public.
# The database listens on the server's loopback only, reached through the tunnel: host/port are constants.
FIELDS = ("SSH_PRIVATE_KEY", "DB_PASSWORD", "SSH_HOST", "SSH_PORT", "SSH_USER",
          "SSH_KNOWN_HOSTS", "DB_NAME", "DB_USER")
DB_REMOTE = "127.0.0.1:3306"


class StoreError(RuntimeError):
    """Message is always a fixed code - never host names, users, or driver text."""


def _cfg():
    missing = [f for f in FIELDS if not os.environ.get("LIFEOS_ACQ_" + f)]
    if missing:
        raise StoreError("STORE_CONFIG_MISSING:" + ",".join(missing))  # names of settings only, no values
    return {f: os.environ["LIFEOS_ACQ_" + f] for f in FIELDS}


def _ssh_code(error):
    if isinstance(error, subprocess.TimeoutExpired):
        return "STORE_SSH_TIMEOUT"
    detail = (error.stderr or "") if isinstance(error, subprocess.CalledProcessError) else ""
    detail = (detail.decode("utf-8", "replace") if isinstance(detail, bytes) else detail).casefold()
    for code, needles in (("HOSTKEY", ("host key verification failed", "host identification")),
                          ("AUTH", ("permission denied", "no more authentication")),
                          ("REFUSED", ("connection refused",)),
                          ("NETWORK", ("timed out", "unreachable", "no route")),
                          ("KEY_INVALID", ("load key", "libcrypto"))):
        if any(n in detail for n in needles):
            return "STORE_SSH_" + code
    return "STORE_SSH_FAILED"


@contextmanager
def connect():
    cfg = _cfg()
    connection = exit_cmd = None
    with tempfile.TemporaryDirectory(prefix="v7-store-") as directory:
        try:
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
            exit_cmd = ["ssh", "-F", "/dev/null", "-S", control, "-O", "exit", "--", cfg["SSH_HOST"]]
            try:
                subprocess.run(["ssh", *opts, "-M", "-S", control, "-fNT", "-o", "ExitOnForwardFailure=yes",
                                "-o", "ServerAliveInterval=15", "-i", str(key), "-p", cfg["SSH_PORT"],
                                "-L", f"127.0.0.1:{local_port}:{DB_REMOTE}", "-l", cfg["SSH_USER"], "--", cfg["SSH_HOST"]],
                               check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            except Exception as error:
                raise StoreError(_ssh_code(error)) from None
            try:
                import pymysql
                connection = pymysql.connect(host="127.0.0.1", port=local_port, user=cfg["DB_USER"],
                                             password=cfg["DB_PASSWORD"], database=cfg["DB_NAME"], charset="utf8mb4",
                                             autocommit=True, connect_timeout=10, read_timeout=20, write_timeout=20)
            except Exception:
                raise StoreError("STORE_DB_CONNECT_FAILED") from None
            yield connection
        finally:
            with suppress(Exception):
                if connection is not None:
                    connection.close()
            with suppress(Exception):
                if exit_cmd is not None:
                    subprocess.run(exit_cmd, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
