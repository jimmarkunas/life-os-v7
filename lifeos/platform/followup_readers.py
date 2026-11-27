"""Read-only delta readers for the Followup stage's six source families. Pure: no network, no store, no cache."""
# One call reads ONE source for ONE tenant and account since the last ACCEPTED checkpoint: validate, check the grant,
# write the receipt, then read with the grant's credential NAME (the injected `fetch` is the host-broker side; the
# caller never holds a credential). Evidence is minimal: source, native id, https link, kind, observed time, a title
# cut to 120 characters. A failed, denied, incomplete or partly malformed read is DEGRADED with the last accepted time
# and no next checkpoint. Asana is never read, TGP is excluded, and grants never share an identity, credential or
# cache.
import re
from dataclasses import dataclass
from datetime import datetime

SOURCES = ("gmail", "outlook", "calendar", "jira", "todoist", "notion", "interview_os")
TENANTS = ("personal", "bytalos")
KINDS = {"gmail": "mail", "outlook": "mail", "calendar": "event", "jira": "issue", "todoist": "task", "notion": "page", "interview_os": "interview"}
TITLE_MAX = 120
MAX_ROWS = 500  # a larger answer is treated as truncated, never silently cut
SECRET = re.compile(r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,}|-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:ghp_|sk-|xox[abprs]-)[A-Za-z0-9_-]{10,}")
LINK = re.compile(r"^https://[^\s]{4,500}$")
DUE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?$")  # a date the SOURCE itself holds (a due date): metadata, never read from a body


@dataclass(frozen=True)
class Grant:
    source: str
    tenant: str
    account: str
    credential_ref: str  # a NAME, never a value
    expires: datetime
    revoked: bool = False


@dataclass(frozen=True)
class Delta:
    source: str
    tenant: str
    account: str
    status: str
    reason: str
    items: tuple = ()
    last_accepted: datetime | None = None
    next_checkpoint: datetime | None = None  # a candidate only: the store advances it after an accepted write, never here


def _when(text):
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00")) if isinstance(text, str) else None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("time")
    return parsed


def _item(source, native_id, link, observed, title, due=None):
    if not isinstance(native_id, str) or not native_id or not isinstance(link, str) or not LINK.match(link):
        raise ValueError("row")
    item = {"source": source, "native_id": native_id[:80], "link": link, "kind": KINDS[source], "observed_at": _when(observed).isoformat(), "title": str(title or "")[:TITLE_MAX]}
    if isinstance(due, str) and DUE.match(due.strip()):
        item["due"] = due.strip()  # only when the source has one; an item without a due date has no `due` key
    return item


def _jira(row, base):
    return _item("jira", row["key"], f"{base.rstrip('/')}/browse/{row['key']}", row["fields"]["updated"], row["fields"].get("summary"), row["fields"].get("duedate"))


def _calendar(row):
    return _item("calendar", row["id"], row.get("htmlLink"), row["updated"], row.get("summary"))


def _mail(source):
    return lambda row: _item(source, row["id"], row.get("link"), row["at"], row.get("subject"))


def _todoist(row):
    return _item("todoist", row["id"], row.get("url"), row["updated_at"], row.get("content"), row.get("due"))


def _notion(row):
    return _item("notion", row["id"], row.get("url"), row["last_edited_time"], row.get("title"))


def _interview(row):
    return _item("interview_os", row["id"], row.get("url"), row["at"], row.get("title"))


NORMALIZE = {"gmail": _mail("gmail"), "outlook": _mail("outlook"), "calendar": _calendar, "todoist": _todoist, "notion": _notion, "interview_os": _interview}


def revoke(grants, source=None, tenant=None):
    return tuple(Grant(**{**g.__dict__, "revoked": True}) if (source in (None, g.source) and tenant in (None, g.tenant)) else g for g in grants)


def _receipt(source, tenant, account, request_id, outcome, reason, now):
    return {"t": now.isoformat(), "source": source, "tenant": tenant, "account": account, "request_id": request_id, "outcome": outcome, "reason": reason}


def read_delta(source, tenant, account, since, grants, fetch, audit, now, request_id, jira_base=None):
    """`since`: the last accepted checkpoint (aware datetime) or None for a first read. `fetch(credential_ref, since)`
    returns a list of raw rows; `audit(receipt)` raises when it cannot write. A refused or failed read never raises;
    an audit failure raises before any read.
    """
    def refuse(reason):
        audit(_receipt(str(source)[:20], str(tenant)[:20], account, request_id, "deny", reason, now))
        return Delta(str(source)[:20], str(tenant)[:20], account, "refused", reason, last_accepted=since)

    if str(source).lower() == "asana":
        return refuse("asana_not_read")
    if source not in SOURCES:
        return refuse("unknown_source")
    if str(tenant).lower() == "tgp":
        return refuse("tenant_excluded")
    if tenant not in TENANTS:
        return refuse("unknown_tenant")
    mine = [g for g in grants if g.source == source and g.tenant == tenant and g.account == account]
    if not mine:
        return refuse("no_grant")
    grant = mine[0]
    if grant.revoked:
        return refuse("grant_revoked")
    if grant.expires <= now:
        return refuse("grant_expired")
    audit(_receipt(source, tenant, account, request_id, "allow", "ok", now))  # the receipt first: no receipt, no read

    def degraded(reason):
        audit(_receipt(source, tenant, account, request_id, "degraded", reason, now))
        return Delta(source, tenant, account, "degraded", reason, items, since)

    items = ()
    try:
        rows = fetch(grant.credential_ref, since)
    except Exception:                                                                    # noqa: BLE001 - a connector failure is recorded by outcome only, never its message
        return degraded("fetch_failed")
    if not isinstance(rows, list):
        return degraded("listing_incomplete")
    if len(rows) > MAX_ROWS:
        return degraded("truncated")
    good, bad = [], 0
    for row in rows:
        try:
            item = _jira(row, jira_base or "") if source == "jira" else NORMALIZE[source](row)
            if SECRET.search(repr(item)):
                raise ValueError("secret")
            when = _when(item["observed_at"])
        except (KeyError, TypeError, AttributeError, ValueError):
            bad += 1
            continue
        if since is None or when > since:
            good.append((when, item))
    good.sort(key=lambda pair: (pair[0], pair[1]["native_id"]))
    items = tuple(item for _, item in good)
    if bad:
        return degraded("row_invalid")
    nxt = max((w for w, _ in good), default=since)
    audit(_receipt(source, tenant, account, request_id, "ok", "ok", now))
    return Delta(source, tenant, account, "ok", "ok", items, since, nxt)


def read_all(requests, grants, fetchers, audit, now, since_by_key=None, jira_base=None):
    """`requests`: (source, tenant, account) triples; `fetchers`: {source: fetch}. Each source fails alone. Returns
    {(source, tenant, account): Delta}.
    """
    since_by_key, out = since_by_key or {}, {}
    for n, (source, tenant, account) in enumerate(requests):
        fetch = fetchers.get(source)
        if fetch is None and source in SOURCES:
            out[(source, tenant, account)] = Delta(source, tenant, account, "degraded", "no_reader", last_accepted=since_by_key.get((source, tenant, account)))
            continue
        out[(source, tenant, account)] = read_delta(source, tenant, account, since_by_key.get((source, tenant, account)), grants, fetch, audit, now, f"batch-{n}", jira_base)
    return out


__all__ = ["SOURCES", "TENANTS", "Grant", "Delta", "revoke", "read_delta", "read_all"]
