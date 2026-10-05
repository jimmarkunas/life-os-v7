"""NET-1 Hostinger state (approved tables v7_network_people, v7_network_positions, v7_network_batches): the sole writer of them.

Derived, machine-maintained state for matching and history; the LinkedIn Connections Sheet stays canonical for who is connected, and nothing here is hand-edited.
Tables are created only on a live run. A batch is applied in chunks, each chunk one transaction that also moves the batch's cursor, so a failure leaves earlier chunks
accepted and the next run resumes at the cursor. Every write is idempotent (unique person and position keys), so replaying a batch changes nothing.
Fixed NETWORK_* codes only; never a driver message."""
from datetime import datetime, timezone

from lifeos.network import identity
from lifeos.network.errors import NetworkError

SOURCE_KIND = "SHEET_VIA_CHATGPT"
CHUNK = 500
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS v7_network_people (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        person_key CHAR(64) NOT NULL, url_key VARCHAR(200) NOT NULL,
        display_name VARCHAR(300) NOT NULL, connected_on DATE NULL,
        first_seen DATETIME NOT NULL, last_observed DATE NOT NULL,
        status VARCHAR(12) NOT NULL, history_coverage VARCHAR(12) NOT NULL,
        UNIQUE KEY uq_net_person (person_key), UNIQUE KEY uq_net_url (url_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_network_positions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        person_id BIGINT NOT NULL, company_key VARCHAR(300) NOT NULL, company_name VARCHAR(400) NOT NULL, title VARCHAR(400) NULL,
        position_state VARCHAR(12) NOT NULL, first_observed DATE NOT NULL, last_observed DATE NOT NULL, last_verified DATE NOT NULL,
        source_kind VARCHAR(32) NOT NULL, source_ref VARCHAR(64) NOT NULL, source_observed_at DATE NOT NULL, material_hash CHAR(64) NOT NULL,
        UNIQUE KEY uq_net_position (material_hash), KEY ix_net_company (company_key, position_state), KEY ix_net_person (person_id, position_state)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS v7_network_batches (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        batch_key CHAR(64) NOT NULL, batch_kind VARCHAR(12) NOT NULL, observed_from DATE NOT NULL, observed_to DATE NOT NULL,
        source_rows INT NOT NULL, people_accepted INT NOT NULL, positions_accepted INT NOT NULL, ambiguous_held INT NOT NULL, rejected INT NOT NULL,
        cursor_row INT NOT NULL, status VARCHAR(12) NOT NULL, imported_at DATETIME NULL,
        UNIQUE KEY uq_net_batch (batch_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)
EXPECTED = {"v7_network_people": "id,person_key,url_key,display_name,connected_on,first_seen,last_observed,status,history_coverage",
            "v7_network_positions": "id,person_id,company_key,company_name,title,position_state,first_observed,last_observed,last_verified,source_kind,source_ref,source_observed_at,material_hash",
            "v7_network_batches": "id,batch_key,batch_kind,observed_from,observed_to,source_rows,people_accepted,positions_accepted,ambiguous_held,rejected,cursor_row,status,imported_at"}


def _marks(n):
    return ",".join(["%s"] * n)


def ensure_schema(connection):
    """Create the tables if missing and prove the columns are the ones this code writes; a different table fails closed."""
    try:
        with connection.cursor() as cursor:
            for statement in SCHEMA:
                cursor.execute(statement)
            for table, columns in EXPECTED.items():
                cursor.execute(f"SELECT {columns} FROM {table} LIMIT 0")
    except Exception:                                                  # noqa: BLE001 - a driver message may carry names; only the fixed code leaves
        raise NetworkError("NETWORK_SCHEMA_MISMATCH") from None


def totals(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v7_network_people")
        people = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM v7_network_positions")
        return {"people_total": people, "positions_total": cursor.fetchone()[0]}


def open_batch(connection, key, plan, exported):
    """-> (batch id, status, cursor). A batch already COMPLETE is a replay; APPLYING or FAILED resumes at its cursor; a new one is recorded as APPLYING."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, status, cursor_row, source_rows, people_accepted, positions_accepted, ambiguous_held, rejected FROM v7_network_batches WHERE batch_key=%s", (key,))
        row = cursor.fetchone()
        if row:
            if tuple(row[3:]) != (plan["source_rows"], len(plan["people"]), plan["positions"], plan["ambiguous_held"], plan["rejected"]):
                raise NetworkError("NETWORK_IDENTITY_CONFLICT")
            return row[0], row[1], row[2]
        cursor.execute("INSERT INTO v7_network_batches (batch_key, batch_kind, observed_from, observed_to, source_rows, people_accepted, positions_accepted, ambiguous_held, rejected,"
                       " cursor_row, status, imported_at) VALUES (%s,'ROSTER',%s,%s,%s,%s,%s,%s,%s,0,'APPLYING',NULL)",
                       (key, exported, exported, plan["source_rows"], len(plan["people"]), plan["positions"], plan["ambiguous_held"], plan["rejected"]))
        cursor.execute("SELECT id FROM v7_network_batches WHERE batch_key=%s", (key,))
        return cursor.fetchone()[0], "APPLYING", 0


def _apply(cursor, chunk, exported, ref, now):
    keys = [p["url_key"] for p in chunk]
    cursor.executemany("INSERT IGNORE INTO v7_network_people (person_key, url_key, display_name, connected_on, first_seen, last_observed, status, history_coverage)"
                       " VALUES (%s,%s,%s,%s,%s,%s,'ACTIVE','SEED_ONLY')",
                       [(identity.person_key(p["url_key"]), p["url_key"], p["name"], p["connected_on"].isoformat() if p["connected_on"] else None, now.isoformat(sep=" ", timespec="seconds"), exported) for p in chunk])
    cursor.execute(f"SELECT id, url_key FROM v7_network_people WHERE url_key IN ({_marks(len(keys))})", keys)
    ids = {url: pid for pid, url in cursor.fetchall()}
    if len(ids) != len(keys):
        raise NetworkError("NETWORK_READBACK_MISMATCH")
    cursor.execute(f"SELECT person_id, id, company_key, title, source_observed_at FROM v7_network_positions WHERE position_state='CURRENT' AND person_id IN ({_marks(len(ids))})", list(ids.values()))
    current = {row[0]: row[1:] for row in cursor.fetchall()}
    new, confirm, supersede = [], [], []
    for p in chunk:
        if not p["company_key"]:
            continue                                                     # no usable employer: the person exists, no position is created or replaced
        pid, cur = ids[p["url_key"]], current.get(ids[p["url_key"]])
        title = p["title"]
        if cur:
            pos_id, ckey, ctitle, seen = cur
            if str(seen) > exported:
                continue                                                 # an older observation never replaces a newer one
            if ckey == p["company_key"] and (title is None or title == ctitle):
                confirm.append((exported, exported, pos_id))
                continue
            supersede.append((pos_id,))
        new.append((pid, p["company_key"], p["company_name"], title, exported, exported, exported, SOURCE_KIND, ref, exported, identity.material_hash(p["url_key"], p["company_key"], title, SOURCE_KIND, exported)))
    if supersede:
        cursor.executemany("UPDATE v7_network_positions SET position_state='SUPERSEDED' WHERE id=%s", supersede)
    if confirm:
        cursor.executemany("UPDATE v7_network_positions SET last_observed=%s, last_verified=%s WHERE id=%s", confirm)
    if new:
        cursor.executemany("INSERT IGNORE INTO v7_network_positions (person_id, company_key, company_name, title, position_state, first_observed, last_observed, last_verified,"
                           " source_kind, source_ref, source_observed_at, material_hash) VALUES (%s,%s,%s,%s,'CURRENT',%s,%s,%s,%s,%s,%s,%s)", new)
    cursor.executemany("UPDATE v7_network_people SET last_observed=%s WHERE url_key=%s AND last_observed < %s", [(exported, k, exported) for k in keys])


def _readback(cursor, chunk):
    keys = [p["url_key"] for p in chunk]
    cursor.execute(f"SELECT COUNT(*) FROM v7_network_people WHERE url_key IN ({_marks(len(keys))})", keys)
    if cursor.fetchone()[0] != len(keys):
        raise NetworkError("NETWORK_READBACK_MISMATCH")
    wanted = sum(1 for p in chunk if p["company_key"])
    cursor.execute(f"SELECT COUNT(DISTINCT s.person_id) FROM v7_network_positions s JOIN v7_network_people p ON p.id = s.person_id WHERE p.url_key IN ({_marks(len(keys))})", keys)
    if cursor.fetchone()[0] < wanted:
        raise NetworkError("NETWORK_READBACK_MISMATCH")


def apply_batch(connection, batch_id, start, plan, exported, ref, chunk=CHUNK, clock=lambda: datetime.now(timezone.utc).replace(tzinfo=None)):
    """Apply people[start:] in chunks; each chunk is one transaction (people, positions and the cursor together) and is read back after it commits."""
    people = plan["people"]
    for at in range(start, len(people), chunk):
        part = people[at:at + chunk]
        try:
            connection.begin()
            with connection.cursor() as cursor:
                _apply(cursor, part, exported, ref, clock())
                cursor.execute("UPDATE v7_network_batches SET cursor_row=%s WHERE id=%s", (at + len(part), batch_id))
            connection.commit()
        except NetworkError:
            connection.rollback()
            raise
        except Exception:                                              # noqa: BLE001
            connection.rollback()
            raise NetworkError("NETWORK_STORE_FAILED") from None
        with connection.cursor() as cursor:
            _readback(cursor, part)


def complete(connection, batch_id, plan, clock=lambda: datetime.now(timezone.utc).replace(tzinfo=None)):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE v7_network_batches SET status='COMPLETE', cursor_row=%s, imported_at=%s WHERE id=%s", (len(plan["people"]), clock().isoformat(sep=" ", timespec="seconds"), batch_id))
        cursor.execute("SELECT status, cursor_row FROM v7_network_batches WHERE id=%s", (batch_id,))
        if tuple(cursor.fetchone()) != ("COMPLETE", len(plan["people"])):
            raise NetworkError("NETWORK_READBACK_MISMATCH")


def audit(connection):
    """Read-only, counts only: row totals, batch states, and how many text values contain an @ (email-shaped). Nothing is written and no value is returned."""
    out = dict(totals(connection))
    with connection.cursor() as cursor:
        cursor.execute("SELECT status, COUNT(*) FROM v7_network_batches GROUP BY status")
        out["batches"] = {str(status): n for status, n in cursor.fetchall()}
        for table, columns in (("v7_network_people", ("url_key", "display_name")), ("v7_network_positions", ("company_key", "company_name", "title", "source_ref")),
                               ("v7_network_batches", ("batch_key",))):
            for column in columns:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE %s", ("%@%",))
                out[f"at_sign_{table[11:]}_{column}"] = cursor.fetchone()[0]
    out["at_sign_total"] = sum(v for k, v in out.items() if k.startswith("at_sign_"))
    return out
