"""LIFE-810 (F01): the capability catalog resolver. Pure functions, no network, no secret, no endpoint.

It extends the existing access layer (access_policy.py decides allow/deny; agent_directory.py is a catalog, not a token) with one question: for a registered principal, tool and action in a
workspace, which provider, account alias, host, source and freshness apply, and is it working, synthetic or missing? A catalog row grants nothing and is never proof that a provider is online.
Fail closed: an unreadable catalog, an unknown principal or capability, the wrong workspace, a missing or duplicate alias or a stale source refuses with a fixed code and never invents a route.
"""
import json
import re
from datetime import datetime, timedelta, timezone

STATUSES = ("working", "synthetic", "missing")
KINDS = ("human", "agent", "session", "service")
FORBIDDEN_KEYS = re.compile(r"(token|secret|password|passwd|credential|api_?key|private_?key|endpoint|url)", re.I)
SECRET_VALUE = re.compile(r"(ghp_|gho_|github_pat_|xox[abp]-|sk-[A-Za-z0-9]|AKIA[0-9A-Z]{12}|-----BEGIN|[A-Fa-f0-9]{40,})")
NAME = re.compile(r"[a-z][a-z0-9_.:-]{1,63}")
TOP = {"version", "principals", "accounts", "capabilities"}
PRINCIPAL = {"id", "kind", "workspace"}
ACCOUNT = {"alias", "provider", "tenant"}
CAPABILITY = {"principal", "tool", "action", "provider", "account_alias", "host", "source", "as_of", "max_age_days", "status"}


class CatalogError(ValueError):
    """A fixed code only: not_json, bad_version, bad_shape, bad_row, bad_status, credential_in_catalog."""


def _scan(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if FORBIDDEN_KEYS.search(str(key)):
                raise CatalogError("credential_in_catalog")
            _scan(value)
    elif isinstance(node, list):
        for value in node:
            _scan(value)
    elif isinstance(node, str) and SECRET_VALUE.search(node):
        raise CatalogError("credential_in_catalog")


def _rows(doc, key, allowed):
    rows = doc.get(key)
    if not isinstance(rows, list) or any(not isinstance(r, dict) or set(r) != allowed for r in rows):
        raise CatalogError("bad_row")
    return rows


def load(text):
    """Parse and validate a catalog. Raises CatalogError (the caller then refuses every resolution)."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        raise CatalogError("not_json") from None
    if not isinstance(doc, dict):
        raise CatalogError("bad_shape")
    if doc.get("version") != 1:
        raise CatalogError("bad_version")
    if set(doc) != TOP:
        raise CatalogError("bad_shape")
    _scan(doc)
    principals, accounts, caps = _rows(doc, "principals", PRINCIPAL), _rows(doc, "accounts", ACCOUNT), _rows(doc, "capabilities", CAPABILITY)
    for row in principals:
        if row["kind"] not in KINDS or not all(isinstance(row[k], str) and NAME.fullmatch(row[k]) for k in ("id", "workspace")):
            raise CatalogError("bad_row")
    for row in accounts:
        if not all(isinstance(row[k], str) and NAME.fullmatch(row[k]) for k in ACCOUNT):
            raise CatalogError("bad_row")
    for row in caps:
        if row["status"] not in STATUSES:
            raise CatalogError("bad_status")
        if not all(isinstance(row[k], str) and row[k] for k in ("principal", "tool", "action", "provider", "host", "source", "as_of")) \
                or type(row["max_age_days"]) is not int or not 1 <= row["max_age_days"] <= 365 \
                or not (row["account_alias"] is None or (isinstance(row["account_alias"], str) and NAME.fullmatch(row["account_alias"]))):
            raise CatalogError("bad_row")
        try:
            datetime.fromisoformat(row["as_of"])
        except ValueError:
            raise CatalogError("bad_row") from None
    return {"principals": {r["id"]: r for r in principals}, "accounts": accounts, "capabilities": caps}


def _refuse(code, **extra):
    return {"ok": False, "code": code, **extra}


def resolve(catalog, principal, tool, action, workspace, now=None):
    """-> {"ok": True, ...the exact action, provider, tenant, account alias, host, allowed source, provenance and status...} or {"ok": False, "code": ...}. Never raises, never returns a credential or an endpoint.
    `catalog` is the result of load(), or None when it could not be read. A `synthetic` row resolves with synthetic=True: it proves a contract, not a live provider."""
    if catalog is None:
        return _refuse("catalog_unreadable")
    who = catalog["principals"].get(principal) if isinstance(principal, str) else None
    if who is None:
        return _refuse("unknown_principal")
    if who["workspace"] != workspace:
        return _refuse("wrong_workspace")
    row = next((c for c in catalog["capabilities"] if (c["principal"], c["tool"], c["action"]) == (principal, tool, action)), None)
    if row is None:
        return _refuse("unknown_capability")
    if row["status"] == "missing":
        return _refuse("unavailable", provider=row["provider"])
    tenant = None
    if row["account_alias"] is not None:
        found = [a for a in catalog["accounts"] if a["alias"] == row["account_alias"] and a["provider"] == row["provider"]]
        if not found:
            return _refuse("alias_missing")
        if len(found) > 1:
            return _refuse("alias_duplicate")
        tenant = found[0]["tenant"]
    now = now or datetime.now(timezone.utc)
    seen = datetime.fromisoformat(row["as_of"])
    seen = seen if seen.tzinfo else seen.replace(tzinfo=timezone.utc)
    if now - seen > timedelta(days=row["max_age_days"]):
        return _refuse("source_stale", as_of=row["as_of"])
    return {"ok": True, "principal": principal, "workspace": workspace, "tool": tool, "action": action, "provider": row["provider"], "account_alias": row["account_alias"], "tenant": tenant,
            "host": row["host"], "allowed_source": row["source"], "provenance": {"source": row["source"], "as_of": row["as_of"]}, "status": row["status"], "synthetic": row["status"] == "synthetic"}
