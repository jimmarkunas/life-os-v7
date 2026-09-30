"""Hostinger MySQL store reached through a pinned SSH tunnel. Private job data lives here, never in the repo.

Only `v7_`-prefixed tables are ever created or touched. Errors surface fixed codes only (public logs).
Usage: python -m pipeline.store --check     (connect, create tables if missing, print counts only)
"""
from contextlib import contextmanager, suppress
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS v7_jobs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        dedupe_key CHAR(64) NOT NULL,
        status VARCHAR(24) NOT NULL,
        title VARCHAR(300), company VARCHAR(200), location_text VARCHAR(200),
        source_url TEXT, final_apply_url TEXT, apply_kind VARCHAR(24),
        posted_age_days SMALLINT NULL, unresolved_reason VARCHAR(100),
        notion_page_id VARCHAR(64) NULL,
        first_seen DATETIME NOT NULL, last_seen DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        UNIQUE KEY uq_v7_jobs_dedupe (dedupe_key),
        KEY ix_v7_jobs_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_job_sources (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        job_id BIGINT NULL,
        source VARCHAR(40) NOT NULL,
        source_url_hash CHAR(64) NOT NULL,
        gmail_message_id VARCHAR(40) NOT NULL,
        seen_at DATETIME NOT NULL,
        UNIQUE KEY uq_v7_sources_url (source_url_hash),
        KEY ix_v7_sources_job (job_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_runs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stage VARCHAR(24) NOT NULL,
        started_at DATETIME NOT NULL, finished_at DATETIME NULL,
        status VARCHAR(16) NOT NULL, counts VARCHAR(500) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)
TABLES = ("v7_jobs", "v7_job_sources", "v7_runs")
# Columns added after the first release (checked via information_schema; portable across MySQL/MariaDB).
COLUMNS = (
    ("v7_jobs", "fuzzy_key", "CHAR(64) NULL", "ADD KEY ix_v7_jobs_fuzzy (fuzzy_key)"),
    ("v7_jobs", "salary_text", "VARCHAR(80) NULL", None),
    ("v7_jobs", "source", "VARCHAR(40) NULL", None),
)
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


def existing_v7_tables(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.tables "
                       "WHERE table_schema = DATABASE() AND table_name LIKE 'v7\\_%%'")
        return {row[0] for row in cursor.fetchall()}


def ensure_schema(connection):
    before = existing_v7_tables(connection)
    with connection.cursor() as cursor:
        for statement in SCHEMA:
            cursor.execute(statement)
    with connection.cursor() as cursor:
        for table, column, ddl, index in COLUMNS:
            cursor.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() "
                           "AND table_name = %s AND column_name = %s", (table, column))
            if not cursor.fetchone()[0]:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                if index:
                    cursor.execute(f"ALTER TABLE {table} {index}")
    after = existing_v7_tables(connection)
    return {"created": len(after - before), "present": len(after & set(TABLES)), "expected": len(TABLES)}


def check():
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()")
            total = cursor.fetchone()[0]
        result = ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM v7_jobs")
            rows = cursor.fetchone()[0]
    return {"connected": True, "tables_in_db": total, "v7_jobs_rows": rows, **result}


if __name__ == "__main__":
    try:
        line = "store-check OK: " + str(check())
    except StoreError as error:
        print(f"STORE FAILED: {error}", file=sys.stderr)
        sys.exit(1)
    print(line)
