"""Jobs OS tables and persistence (Hostinger). The connection itself lives in lifeos.platform.db."""
import sys

from lifeos.platform.db import StoreError, connect          # noqa: F401 - the jobs facade for the database

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
    """CREATE TABLE IF NOT EXISTS v7_job_descriptions (
        job_id BIGINT NOT NULL PRIMARY KEY,
        source_kind VARCHAR(24) NOT NULL,
        full_text MEDIUMTEXT NOT NULL,
        summary MEDIUMTEXT NULL, responsibilities MEDIUMTEXT NULL,
        requirements MEDIUMTEXT NULL, qualifications MEDIUMTEXT NULL,
        fingerprint CHAR(64) NOT NULL, fetched_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_job_fit (
        job_id BIGINT NOT NULL PRIMARY KEY,
        score SMALLINT NULL, decision VARCHAR(8) NOT NULL, line VARCHAR(600) NOT NULL, why VARCHAR(300) NULL,
        exclusion VARCHAR(40) NULL, confidence VARCHAR(8) NOT NULL,
        buckets VARCHAR(400) NULL, trace MEDIUMTEXT NULL,
        model_version VARCHAR(8) NOT NULL, profile_hash CHAR(16) NOT NULL, jd_fingerprint CHAR(64) NOT NULL,
        scored_at DATETIME NOT NULL,
        KEY ix_v7_fit_decision (decision)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_sources (
        source_id VARCHAR(40) NOT NULL PRIMARY KEY,
        kind VARCHAR(24) NOT NULL,
        due_at DATETIME NOT NULL,
        last_status VARCHAR(10) NULL, last_reason VARCHAR(24) NULL,
        last_complete_at DATETIME NULL, frontier_hash CHAR(64) NULL,
        failures SMALLINT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_source_items (
        source_id VARCHAR(40) NOT NULL, provider_job_id VARCHAR(120) NOT NULL,
        material_hash CHAR(64) NOT NULL,
        state VARCHAR(8) NOT NULL DEFAULT 'CURRENT',
        ingest VARCHAR(10) NOT NULL DEFAULT 'PENDING',
        suppress_reason VARCHAR(24) NULL,
        first_seen DATETIME NOT NULL, last_seen DATETIME NOT NULL,
        job_id BIGINT NULL,
        PRIMARY KEY (source_id, provider_job_id),
        KEY ix_v7_items_ingest (ingest)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_source_runs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        source_id VARCHAR(40) NOT NULL, ran_at DATETIME NOT NULL,
        status VARCHAR(10) NOT NULL, reason VARCHAR(24) NULL,
        added INT NOT NULL DEFAULT 0, changed INT NOT NULL DEFAULT 0, unchanged INT NOT NULL DEFAULT 0,
        removed INT NOT NULL DEFAULT 0, suppressed INT NOT NULL DEFAULT 0, ingested INT NOT NULL DEFAULT 0,
        KEY ix_v7_runs_source (source_id, ran_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_ledger_urls (
        url_hash CHAR(64) NOT NULL PRIMARY KEY,
        source VARCHAR(12) NOT NULL,
        seen_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_spend (
        day DATE NOT NULL PRIMARY KEY,
        fetch_urls INT NOT NULL DEFAULT 0,
        browser_seconds INT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_runs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        stage VARCHAR(24) NOT NULL,
        started_at DATETIME NOT NULL, finished_at DATETIME NULL,
        status VARCHAR(16) NOT NULL, counts VARCHAR(500) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)
TABLES = ("v7_jobs", "v7_job_sources", "v7_job_descriptions", "v7_job_fit", "v7_sources", "v7_source_items", "v7_source_runs", "v7_ledger_urls", "v7_spend", "v7_runs")
# Columns added after the first release (checked via information_schema; portable across MySQL/MariaDB).
COLUMNS = (
    ("v7_jobs", "fuzzy_key", "CHAR(64) NULL", "ADD KEY ix_v7_jobs_fuzzy (fuzzy_key)"),
    ("v7_jobs", "salary_text", "VARCHAR(80) NULL", None),
    ("v7_jobs", "source", "VARCHAR(40) NULL", None),
    ("v7_jobs", "mail_received_at", "DATETIME NULL", None),
    ("v7_jobs", "provider_score", "SMALLINT NULL", None),
    ("v7_jobs", "posted_date", "DATE NULL", None),
    ("v7_jobs", "posted_source", "VARCHAR(16) NULL", None),
    ("v7_jobs", "resolve_attempts", "SMALLINT NOT NULL DEFAULT 0", None),
    ("v7_jobs", "seen_count", "INT NOT NULL DEFAULT 1", None),
    ("v7_jobs", "repost_of", "BIGINT NULL", None),
    ("v7_jobs", "enrich_attempts", "SMALLINT NOT NULL DEFAULT 0", None),
    ("v7_jobs", "lane", "VARCHAR(24) NOT NULL DEFAULT 'Newsletter'", None),
    ("v7_jobs", "provider", "VARCHAR(40) NULL", None),
    ("v7_jobs", "ghost_flag", "TINYINT NOT NULL DEFAULT 0", None),
    ("v7_jobs", "notion_synced_at", "DATETIME NULL", None),
    ("v7_jobs", "notion_expired_at", "DATETIME NULL", None),
    ("v7_job_fit", "shadow_score", "SMALLINT NULL", None),
    ("v7_job_fit", "shadow_changes", "SMALLINT NULL", None),
    ("v7_job_fit", "admission", "VARCHAR(8) NULL", None),
    ("v7_job_fit", "admission_reason", "VARCHAR(80) NULL", None),
    ("v7_job_fit", "work_mode", "VARCHAR(8) NULL", None),
    ("v7_job_fit", "lane", "VARCHAR(24) NULL", None),
    ("v7_spend", "browser_seconds", "INT NOT NULL DEFAULT 0", None),
)
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
            cursor.execute("SELECT status, COUNT(*) FROM v7_jobs GROUP BY status")
            by_status = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.execute("SELECT source, COUNT(*) FROM v7_jobs WHERE status = 'NEW' GROUP BY source")
            new_by_source = {row[0]: row[1] for row in cursor.fetchall()}
            cursor.execute("SELECT COUNT(DISTINCT fuzzy_key) FROM v7_jobs WHERE status = 'NEW'")
            distinct_new = cursor.fetchone()[0]
    return {"connected": True, "tables_in_db": total, "jobs_by_status": by_status, "new_by_source": new_by_source,
            "distinct_new_by_fuzzy_key": distinct_new, **result}


if __name__ == "__main__":
    try:
        line = "store-check OK: " + str(check())
    except StoreError as error:
        print(f"STORE FAILED: {error}", file=sys.stderr)
        sys.exit(1)
    print(line)
