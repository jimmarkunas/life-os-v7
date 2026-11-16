"""Idempotent plan for reconciling the Inbox view's Notion rows (LIFE-711): create, update and archive operations; the real write is an injected callable that refuses."""
# `plan(current, desired)`: `current` are the rows already in the view (dicts with the "Record ID" property and an optional "Archived" flag), `desired` are `view_rows.project_rows(...)`. Rows are keyed by the
# source-qualified Record ID; the same input always gives the same ordered plan, and applying a plan then planning again gives an empty plan (no churn).
#
# Safety rules: a row is archived only when its source read cleanly this run (health state ok), so a failed, degraded or not-provisioned source keeps its last accepted rows in place; health rows are never
# archived; rows that are not ours (a key we do not recognise) are left alone; an archived row that comes back is restored by an update. Nothing here calls Notion: `execute` hands each operation to a
# `write` callable that defaults to a refusal until Jim approves a test page, the integration and a writer scope (docs/platform/action-inbox-view.md).
from lifeos.inbox.model import InboxError

CREATE, UPDATE, ARCHIVE = "create", "update", "archive"
_ORDER = {CREATE: 0, UPDATE: 1, ARCHIVE: 2}


def pair_of(record_id):
    """(source, workspace) from a Record ID: item `jira:personal:acct:id`, health `health:jira:personal`, review `review:followup:personal:acct:id`; None when the key is not ours."""
    parts = record_id.split(":") if isinstance(record_id, str) else []
    if len(parts) >= 3 and parts[0] in ("health", "review"):
        return parts[1], parts[2]
    if len(parts) >= 4:
        return parts[0], parts[1]
    return None


def read_ok(desired):
    """The (source, workspace) pairs whose read was fully clean this run, from the desired health rows."""
    return frozenset(pair_of(r["Record ID"]) for r in desired if r.get("Kind") == "health" and r.get("Health state") == "ok")


def _clean(row):
    return {k: v for k, v in row.items() if k != "Archived"}


def plan(current, desired):
    """-> a tuple of (op, Record ID, payload): create (full row), update (only the changed properties, plus Archived=False when restoring), archive (None). Sorted by operation then key."""
    have = {r["Record ID"]: r for r in current}
    want = {r["Record ID"]: r for r in desired}
    ok = read_ok(desired)
    ops = []
    for key, row in want.items():
        old = have.get(key)
        if old is None:
            ops.append((CREATE, key, _clean(row)))
            continue
        changed = {k: v for k, v in _clean(row).items() if old.get(k) != v}
        if old.get("Archived"):
            changed["Archived"] = False
        if changed:
            ops.append((UPDATE, key, changed))
    for key, old in have.items():
        if key in want or old.get("Archived") or old.get("Kind") == "health":
            continue
        pair = pair_of(key)
        if pair is not None and pair in ok:                                         # the source read cleanly and no longer has this record
            ops.append((ARCHIVE, key, None))
    return tuple(sorted(ops, key=lambda op: (_ORDER[op[0]], op[1])))


def apply_plan(current, ops):
    """The rows after the plan, as a pure function (tests, dry runs): used to prove that planning again gives nothing."""
    rows = {r["Record ID"]: dict(r) for r in current}
    for op, key, payload in ops:
        if op == CREATE:
            rows[key] = {**payload, "Archived": False}
        elif op == UPDATE:
            rows[key] = {**rows[key], **payload}
        else:
            rows[key] = {**rows[key], "Archived": True}
    return tuple(rows[k] for k in sorted(rows))


def refuse_write(operation):
    """The default writer: nothing is written to Notion until the integration, the test page and the writer scope are approved."""
    raise InboxError("notion_writes_not_authorized")


def execute(ops, write=refuse_write):
    """Hand each operation to `write` in plan order; the default refuses. -> the number of operations written."""
    for op in ops:
        write(op)
    return len(ops)


__all__ = ["ARCHIVE", "CREATE", "UPDATE", "apply_plan", "execute", "pair_of", "plan", "read_ok", "refuse_write"]
