"""The Runtime Configuration data source reader, addressed by V7_RUNTIME_CONFIG_SOURCE_ID, never by title: V7's one
settings source. Rows: Name, Value, Kind (address | switch), Revision, Active; two active rows for a name apply
neither. Reuses settings.py's allowlist, refusals and shadow rule. Counts and names only; never raises.
"""
import os
import re
import threading
import time

from lifeos.platform import settings

SOURCE_NAME = "V7_RUNTIME_CONFIG_SOURCE_ID"
KINDS = ("address", "switch")
REQUIRED = {"Name": "title", "Value": "rich_text", "Kind": "select", "Revision": "number", "Active": "checkbox"}
MAX_PAGES = 20  # more than 20 pages is a mistake, not a settings source
CODE = re.compile(r"[A-Z0-9_]{3,60}")
NAME = re.compile(r"[A-Z][A-Z0-9_]*")
PROOF_TOKENS = ("NOTION_JIRA_TOKEN", "NOTION_API_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_RECRUITERS_TOKEN", "NOTION_AMAZON_TOKEN", "NOTION_BILLS_TOKEN", "NOTION_FIT_SYNC_TOKEN")


class RuntimeConfigError(RuntimeError):
    """Fixed codes only: RUNTIME_CONFIG_SOURCE_ID_MISSING, _SOURCE_ID_INVALID, _UNREADABLE, _EMPTY."""


def source_id(environ):
    """The bootstrap ID as bare hex-and-dashes (a `collection://` prefix is dropped), or a fixed error code."""
    raw = (environ.get("V7_RUNTIME_CONFIG_SOURCE_ID") or "").strip().replace("collection://", "")
    if not raw:
        raise RuntimeConfigError("RUNTIME_CONFIG_SOURCE_ID_MISSING")
    if not settings.ADDRESS.match(raw):
        raise RuntimeConfigError("RUNTIME_CONFIG_SOURCE_ID_INVALID")
    return raw


def _text(prop):
    return "".join(p.get("plain_text") or "" for p in (prop or {}).get("rich_text") or (prop or {}).get("title") or [] if isinstance(p, dict)).strip()


def _row(page):
    """One Notion page -> (name, value, kind, revision, active), or None when a property is missing or mistyped."""
    props = (page or {}).get("properties") or {}
    if any((props.get(key) or {}).get("type") != kind for key, kind in REQUIRED.items()):
        return None
    kind = ((props["Kind"].get("select")) or {}).get("name") or ""
    revision = props["Revision"].get("number")
    return _text(props["Name"]), _text(props["Value"]), kind, revision, bool(props["Active"].get("checkbox"))


def fetch(client, source):
    """-> every row of the source, by ID. Any client failure becomes RUNTIME_CONFIG_UNREADABLE."""
    rows, cursor = [], None
    try:
        for _ in range(MAX_PAGES):
            body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            reply = client.call("POST", f"/data_sources/{source}/query", body) or {}
            rows += [r for r in reply.get("results") or [] if isinstance(r, dict) and not r.get("in_trash") and not r.get("archived")]
            cursor = reply.get("next_cursor")
            if not (reply.get("has_more") and cursor):
                return rows
    except Exception:  # noqa: BLE001
        raise RuntimeConfigError("RUNTIME_CONFIG_UNREADABLE") from None
    raise RuntimeConfigError("RUNTIME_CONFIG_UNREADABLE")


def parse(pages):
    """Notion pages -> ({name: value}, counts of refused, unknown, invalid, repeated and inactive rows, which are not
    applied).
    """
    counts = {"refused": [], "unknown": 0, "invalid": [], "repeated": [], "inactive": 0}
    seen, rows = {}, []
    for page in pages:
        row = _row(page)
        if row is None:
            counts["unknown"] += 1
            continue
        name, value, kind, revision, active = row
        if not active:
            counts["inactive"] += 1
            continue
        if not NAME.fullmatch(name):
            counts["unknown"] += 1
            continue
        seen[name] = seen.get(name, 0) + 1
        rows.append((name, value, kind, revision))
    found = {}
    for name, value, kind, revision in rows:
        if name in settings.SAFETY or name.endswith(("_TOKEN", "_SECRET", "_KEY", "_PASSWORD")):
            counts["refused"].append(name)
        elif not settings.movable(name):
            counts["unknown"] += 1
        elif seen[name] > 1:
            if name not in counts["repeated"]:
                counts["repeated"].append(name)
        elif kind != ("address" if settings.is_address(name) else "switch") or not isinstance(revision, (int, float)) or revision < 0 \
                or not value or "\n" in value or "\r" in value \
                or (settings.is_address(name) and not settings.ADDRESS.match(value.replace("collection://", ""))):
            counts["invalid"].append(name)
        else:
            found[name] = value
    return found, counts


def read(client, environ=os.environ):
    return parse(fetch(client, source_id(environ)))


def export_rows(found):
    return [{"Name": n, "Value": v, "Kind": "address" if settings.is_address(n) else "switch", "Revision": 1, "Active": True} for n, v in sorted(found.items())
            if settings.movable(n) and n not in settings.SAFETY]


_ONCE = {}
_ONCE_LOCK = threading.Lock()


def reset_once():
    with _ONCE_LOCK:
        _ONCE.clear()


def _client_for(token):
    from lifeos.platform.notion_client import Client                    # noqa: PLC0415
    return Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def probe(environ=os.environ, make_client=_client_for):
    """Can each Notion token this step holds read the source? -> {TOKEN_NAME: "PASS" | "FAIL:<CODE>"}."""
    out = {}
    for name in PROOF_TOKENS:
        token = (environ.get(name) or "").strip()
        if not token:
            continue
        try:
            found, _ = read(make_client(token), environ)
            out[name] = "PASS" if found else "FAIL:RUNTIME_CONFIG_EMPTY"
        except RuntimeConfigError as error:
            out[name] = "FAIL:" + str(error)
        except Exception as error:  # noqa: BLE001
            out[name] = "FAIL:" + type(error).__name__
    return out


def shadow(environ=os.environ, client=None, make_client=None, once=False):
    """With `once=True`, later calls with the same bootstrap ID and token names return the first call's receipt."""
    if not once:
        return _shadow(environ, client, make_client)
    try:
        key = (source_id(environ), tuple(name for name in PROOF_TOKENS if (environ.get(name) or "").strip()))
    except RuntimeConfigError:
        return _shadow(environ, client, make_client)
    with _ONCE_LOCK:
        if key in _ONCE:
            first = _ONCE[key]
            return {"status": "ok", "rows": first["rows"], "cached": True}
        receipt = _shadow(environ, client, make_client)
        if receipt.get("status") == "ok":
            _ONCE[key] = receipt
        return receipt


def _shadow(environ, client, make_client):
    """Read-only health check of the source; never raises. -> {"status": "off"} with no bootstrap ID or token, else
    `status` ok or `unreadable:<CODE>` plus `rows`, `kinds`, `seconds` and optional `new_counts`, `tokens`, `names`.
    """
    try:
        source_id(environ)
    except RuntimeConfigError as error:
        return {"status": "off"} if str(error) == "RUNTIME_CONFIG_SOURCE_ID_MISSING" else {"status": "unreadable:" + str(error)}
    began = time.monotonic()
    try:
        if client is None:
            token = next((environ.get(t).strip() for t in settings.TOKENS if (environ.get(t) or "").strip()), "") \
                or next((environ.get(k).strip() for k in sorted(environ) if settings.ANY_NOTION_TOKEN.fullmatch(k) and (environ.get(k) or "").strip()), "")
            if not token:
                return {"status": "off"}
            client = _client_for(token)
            make_client = make_client or _client_for
        new, new_counts = read(client, environ)
    except Exception as error:  # noqa: BLE001
        return {"status": "unreadable:" + (str(error) if CODE.fullmatch(str(error)) else type(error).__name__)}
    out = {"status": "ok", "rows": len(new), "kinds": {"address": sum(1 for n in new if settings.is_address(n)), "switch": sum(1 for n in new if not settings.is_address(n))}}
    flagged = {key: value for key, value in new_counts.items() if value}
    if flagged:
        out["new_counts"] = flagged
    if make_client is not None:
        out["tokens"] = probe(environ, make_client)
    if environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        out["names"] = sorted(new)
    out["seconds"] = round(time.monotonic() - began, 2)
    return out
