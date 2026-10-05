"""SAFE-1.1: two fixed weekly checks that need the real database, plus the saved representative Fit set. Counts and fixed codes only.

`sql-smoke`: every SQL statement written as a plain string in `lifeos/` is run through `EXPLAIN` on the real Hostinger database with the real PyMySQL driver (the
unit tests use SQLite and fake cursors, which cannot catch MySQL-only syntax, a renamed column or a stray `%`). EXPLAIN plans and never executes, so nothing is
read or written. A statement is reported as module:line:MySQL error number; a missing table is counted apart (a table the first live run has not created yet).

`fit-capture` / `fit-golden`: `fit_golden.json` is a saved set of real jobs (store ids only) with the Fit and lane decision each had on the capture date. `fit-golden`
re-scores exactly those jobs with today's rules and the private profile and reports how many decisions changed: run it after any rule change (a flip is either the
intended effect of the change, to be re-captured in the same PR, or a regression). `fit-capture` prints a fresh set to save."""
import ast
import json
import os
import re
from datetime import date
from pathlib import Path

GOLDEN = Path(__file__).with_name("fit_golden.json")
ROOT = Path(__file__).resolve().parents[1]
VERBS = ("SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE")
PER_DECISION = {"Go": 25, "No-Go": 25, "Unscorable": 5}
SKIP_TABLE_ERRORS = {1146}                      # table does not exist
SKIP_FILES = {"probe.py", "safety.py"}


class SafetyError(RuntimeError):
    """Fixed codes only."""


def _text(node):
    """The string a node spells, or None: plain constants and `+` joins of constants (the way statements are written here)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left), _text(node.right)
        return left + right if left is not None and right is not None else None
    return None


def statements(root=ROOT):
    """[(path, line, sql)] for every resolvable SQL string constant under lifeos/ that touches a v7_ table."""
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.name in SKIP_FILES or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        inner = {id(child) for node in ast.walk(tree) if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                 for child in (node.left, node.right)}                               # a join is reported once, as a whole
        for node in ast.walk(tree):
            if id(node) in inner:
                continue
            text = _text(node)
            if text and text.lstrip().upper().startswith(VERBS) and "v7_" in text:
                found.append((str(path.relative_to(root.parent)), node.lineno, " ".join(text.split())))
    return found


def _placeholders(sql):
    return sql.replace("%%", "").count("%s")


def _params(sql):
    """One value per placeholder; a placeholder written `IN %s` takes a tuple, which the driver expands to `(1)` as the code relies on."""
    return tuple((1,) if m.group(1) else 1 for m in re.finditer(r"(\bIN\s+)?%s", sql.replace("%%", ""), re.I))


def _fragment(sql):
    """A statement the code finishes at run time (`... IN (` plus a joined list of placeholders) cannot be planned as written."""
    return sql.count("(") != sql.count(")")


def sql_smoke(connection, items=None):
    items = statements() if items is None else items
    counts = {"statements": len(items), "ok": 0, "fragments": 0, "missing_table": 0, "failed": 0, "failures": []}
    with connection.cursor() as cursor:
        for path, line, sql in items:
            if _fragment(sql):
                counts["fragments"] += 1
                continue
            try:
                cursor.execute("EXPLAIN " + sql, _params(sql))
                cursor.fetchall()
                counts["ok"] += 1
            except Exception as error:                                              # noqa: BLE001 - any driver error is a finding, reported by code only
                code = error.args[0] if error.args and isinstance(error.args[0], int) else type(error).__name__
                if code in SKIP_TABLE_ERRORS:
                    counts["missing_table"] += 1
                else:
                    counts["failed"] += 1
                    counts["failures"].append(f"{path}:{line}:{code}")
    return counts


def run_sql_smoke(limit, live, environ=os.environ):
    from lifeos.jobs import store                                                    # noqa: PLC0415
    with store.connect() as connection:
        counts = sql_smoke(connection)
    if counts["failed"]:
        raise SafetyError(f"SQL_SMOKE_FAILED:{counts['failed']}of{counts['statements']}:" + ",".join(counts["failures"][:8]))
    return counts


def _rows(connection, ids):
    marks = ",".join(["%s"] * len(ids))
    with connection.cursor() as cursor:
        cursor.execute("SELECT j.id, j.title, j.company, d.full_text, d.fingerprint, j.lane, j.location_text, j.salary_text, j.posted_date, j.first_seen, j.route_evidence, j.source "
                       "FROM v7_jobs j JOIN v7_job_descriptions d ON d.job_id = j.id WHERE j.id IN (" + marks + ")", tuple(ids))
        return [tuple(r) for r in cursor.fetchall()]


def _decisions(rows, environ, today):
    from lifeos.jobs.fit import profile as fit_profile                               # noqa: PLC0415
    from lifeos.jobs.fit.stage import score_rows                                      # noqa: PLC0415
    profile = fit_profile.load(environ)
    return {str(job_id): [result.decision, decision.status]
            for job_id, _, result, decision, _, _, _ in score_rows(rows, profile, today)}


def run_fit_capture(limit, live, environ=os.environ, today=None):
    from lifeos.jobs import store                                                    # noqa: PLC0415
    today = today or date.today()
    ids = []
    with store.connect() as connection, connection.cursor() as cursor:
        for decision, n in PER_DECISION.items():
            cursor.execute("SELECT j.id FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id JOIN v7_job_descriptions d ON d.job_id = j.id WHERE f.decision = %s "
                           "ORDER BY CRC32(CONCAT(j.id, 'fit-golden')) LIMIT %s", (decision, n))
            ids += [r[0] for r in cursor.fetchall()]
        rows = _rows(connection, ids)
    scored = _decisions(rows, environ, today)
    return {"captured": len(scored), "today": today.isoformat(), "golden": scored}


def run_fit_golden(limit, live, environ=os.environ, rows=None, golden=None, today=None):
    saved = golden if golden is not None else json.loads(GOLDEN.read_text())
    expected, today = saved["jobs"], today or date.fromisoformat(saved["today"])
    if not expected:
        raise SafetyError("FIT_GOLDEN_EMPTY")
    if rows is None:
        from lifeos.jobs import store                                                # noqa: PLC0415
        with store.connect() as connection:
            rows = _rows(connection, [int(i) for i in expected])
    now = _decisions(rows, environ, today)
    changed = sorted(i for i in now if now[i] != expected[i])
    counts = {"saved": len(expected), "checked": len(now), "missing": len(expected) - len(now), "same": len(now) - len(changed), "changed": len(changed),
              "changed_ids": changed[:20], "flips": sorted({f"{expected[i][0]}/{expected[i][1]}->{now[i][0]}/{now[i][1]}" for i in changed})}
    if changed:
        raise SafetyError(f"FIT_GOLDEN_CHANGED:{len(changed)}of{len(now)}:" + ",".join(counts["flips"][:6]))
    return counts
