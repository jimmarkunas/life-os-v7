"""LIFE-844: the Runtime Configuration data source reader, addressed by a stable ID, never by title.

`shadow` (compare-only) is called by lifeos.run next to settings.apply; the environment is still filled only from the old page.

The V7 settings page (settings.py) is REQ-52 in Product Requirements and is found by its exact title; moving or renaming it breaks every stage that reads it. This reader takes its source from
V7_RUNTIME_CONFIG_SOURCE_ID, so a rename or a move cannot hide it. Rows: Name, Value, Kind (address | switch), Revision, Active. It reuses settings.py's allowlist (MOVABLE), refusals (SAFETY and
credential-shaped names), shadow rule (`_fill`) and last-accepted fallback; two active rows for a name apply neither. Counts and names only, never a value; never raises (`status` says why).
Cutover is protected and not done here: docs/platform/runtime-config-cutover.md."""
import os
import re
import threading
import time

from lifeos.platform import settings

SOURCE_NAME = "V7_RUNTIME_CONFIG_SOURCE_ID"
KINDS = ("address", "switch")
REQUIRED = {"Name": "title", "Value": "rich_text", "Kind": "select", "Revision": "number", "Active": "checkbox"}
MAX_PAGES = 20                                                           # a settings source is a few dozen rows; more than 20 pages is a mistake, not data
CODE = re.compile(r"[A-Z0-9_]{3,60}")
NAME = re.compile(r"[A-Z][A-Z0-9_]*")
PROOF_TOKENS = ("NOTION_JIRA_TOKEN", "NOTION_API_TOKEN", "NOTION_INTERVIEW_TOKEN", "NOTION_RECRUITERS_TOKEN", "NOTION_AMAZON_TOKEN", "NOTION_BILLS_TOKEN", "NOTION_FIT_SYNC_TOKEN")   # LIFE-858: the seven identities that must read the source


class RuntimeConfigError(RuntimeError):
    """Fixed codes only: RUNTIME_CONFIG_SOURCE_ID_MISSING, RUNTIME_CONFIG_SOURCE_ID_INVALID, RUNTIME_CONFIG_UNREADABLE, RUNTIME_CONFIG_EMPTY."""


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
    """One Notion page -> (name, value, kind, revision, active), or None when a required property is missing or has the wrong type."""
    props = (page or {}).get("properties") or {}
    if any((props.get(key) or {}).get("type") != kind for key, kind in REQUIRED.items()):
        return None
    kind = ((props["Kind"].get("select")) or {}).get("name") or ""
    revision = props["Revision"].get("number")
    return _text(props["Name"]), _text(props["Value"]), kind, revision, bool(props["Active"].get("checkbox"))


def fetch(client, source):
    """-> every row of the source, by ID. Any client failure becomes RUNTIME_CONFIG_UNREADABLE; the source is never searched for by title."""
    rows, cursor = [], None
    try:
        for _ in range(MAX_PAGES):
            body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            reply = client.call("POST", f"/data_sources/{source}/query", body) or {}
            rows += [r for r in reply.get("results") or [] if isinstance(r, dict) and not r.get("in_trash") and not r.get("archived")]
            cursor = reply.get("next_cursor")
            if not (reply.get("has_more") and cursor):
                return rows
    except Exception:                                                    # noqa: BLE001 - the reader never raises its cause (it may hold a value); the fixed code is the receipt
        raise RuntimeConfigError("RUNTIME_CONFIG_UNREADABLE") from None
    raise RuntimeConfigError("RUNTIME_CONFIG_UNREADABLE")                # the source kept paging: not a settings source


def parse(pages):
    """Notion pages -> ({name: value}, counts). The counts match settings.parse (`refused`, `unknown`, `invalid`, `repeated`) plus `inactive`; none of them is applied."""
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
        if not NAME.fullmatch(name):                                         # a Name that is not NAME_LIKE_THIS is not a setting
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
    """-> ({name: value}, counts) from the configured source; the source is addressed only by its bootstrap ID."""
    return parse(fetch(client, source_id(environ)))


def compare(old, new):
    """The dual-read gate before any cutover: the old page's accepted values against the candidate source's. -> counts (names only) and `blocking`, true on any difference."""
    out = {"matched": 0, "mismatched": [], "only_old": sorted(set(old) - set(new)), "only_new": sorted(set(new) - set(old))}
    for name in sorted(set(old) & set(new)):
        if settings._bare(old[name]) == settings._bare(new[name]) if settings.is_address(name) else old[name] == new[name]:
            out["matched"] += 1
        else:
            out["mismatched"].append(name)
    out["blocking"] = bool(out["mismatched"] or out["only_old"] or out["only_new"])
    return out


def export_rows(found):
    """The rows to create in the new source from an accepted settings map (a migration helper for the protected cutover; it writes nothing)."""
    return [{"Name": n, "Value": v, "Kind": "address" if settings.is_address(n) else "switch", "Revision": 1, "Active": True} for n, v in sorted(found.items())
            if settings.movable(n) and n not in settings.SAFETY]


def apply(environ=os.environ, client=None, connect=None, remember=False):
    """Fill the environment from the configured source under settings.py's shadow rule. -> counts (names only). Same fallbacks as settings.apply:
    no bootstrap ID: nothing changes (`off`); an unreadable, malformed or empty source: the last accepted map when `connect` is given (`unreadable:<CODE>`, `last_accepted`);
    `remember` (cutover only) saves a good read as the last accepted map."""
    out = {"status": "off", "filled": [], "matched": 0, "mismatched": []}
    try:
        source = source_id(environ)
    except RuntimeConfigError as error:
        if str(error) != "RUNTIME_CONFIG_SOURCE_ID_MISSING":
            out["status"] = "unreadable:" + str(error)
            settings._fallback(environ, connect, out)
        return out
    try:
        if client is None:
            raise RuntimeConfigError("RUNTIME_CONFIG_UNREADABLE")
        found, counts = parse(fetch(client, source))
        if not found:
            out.update(counts)
            raise RuntimeConfigError("RUNTIME_CONFIG_EMPTY")             # nothing usable: never apply an empty override
    except RuntimeConfigError as error:
        out["status"] = "unreadable:" + str(error)
        settings._fallback(environ, connect, out)
        return out
    out.update(counts, status="ok")
    settings._fill(environ, found, out)
    if remember and connect is not None:
        try:
            out["remembered"] = settings.remember(connect, found)
        except Exception as error:                                       # noqa: BLE001 - remembering is best effort, as in settings.apply
            out["remember_status"] = str(error) if CODE.fullmatch(str(error)) else type(error).__name__
    return out


_ONCE = {}                                                               # LIFE-858: the process-wide receipt of `shadow(once=True)`, so the many stages of one tick process make one read, not one each
_ONCE_LOCK = threading.Lock()


def reset_once():
    with _ONCE_LOCK:
        _ONCE.clear()


def _client_for(token):
    from lifeos.platform.notion_client import Client                    # noqa: PLC0415
    return Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def probe(environ=os.environ, make_client=_client_for):
    """LIFE-858 (Phase 1, read only): can each Notion token identity THIS step holds read the source? One read of the source per token present, by ID, through the same reader.
    -> {TOKEN_NAME: "PASS" | "FAIL:<CODE>"}. A token the step does not hold is not listed (UNVERIFIED for that run); a name outside PROOF_TOKENS is never probed. Names and codes only: never a token, a value or a message."""
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
        except Exception as error:                                       # noqa: BLE001 - a probe never fails a stage and never repeats a message
            out[name] = "FAIL:" + type(error).__name__
    return out


def shadow(environ=os.environ, client=None, make_client=None, once=False):
    """With `once=True` (the `lifeos.run` hook and the tick) the first call in a process makes the read and every later call with the same bootstrap ID and token names returns a compact
    `{"status", "matched", "blocking", "cached": True}` without touching Notion: the hourly tick runs about 23 stages in one process and each used to repeat the whole read (about 2.6 s each)."""
    if not once:
        return _shadow(environ, client, make_client)
    try:
        key = (source_id(environ), tuple(name for name in PROOF_TOKENS if (environ.get(name) or "").strip()))
    except RuntimeConfigError:
        return _shadow(environ, client, make_client)                          # off or invalid: nothing is read, nothing to cache
    with _ONCE_LOCK:                                                         # a concurrent caller waits for the first read instead of repeating it
        if key in _ONCE:
            first = _ONCE[key]
            return {"status": "ok", "matched": first["matched"], "blocking": first["blocking"], "cached": True}
        receipt = _shadow(environ, client, make_client)
        if receipt.get("status") == "ok":                                     # only a good read is remembered: a transient failure never poisons the later stages
            _ONCE[key] = receipt
        return receipt


def _shadow(environ, client, make_client):
    """Compare-only dual read (cutover step 4): the new source against the old V7 settings page. Changes nothing in the environment, writes nothing, never raises.
    -> {"status": "off"} with no bootstrap ID or no token (nothing was read), else `compare` counts (names only) plus `status` ok, or `unreadable:<CODE>`.
    LIFE-858 adds, to the ok receipt only: `rows` and `kinds` (counts), `new_counts` (names of rows the reader refused or found invalid or repeated, only when there are any), `seconds` (the read's own cost),
    `tokens` (`probe`: one PASS/FAIL per Notion token this step holds, only in production or with `make_client`), and `names` (both sides' setting names, only on a manual dispatch)."""
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
        old, _ = settings.parse(settings.read(client))
    except Exception as error:                                           # noqa: BLE001 - a shadow read never fails a stage
        return {"status": "unreadable:" + (str(error) if CODE.fullmatch(str(error)) else type(error).__name__)}
    out = {"status": "ok", **compare(old, new), "rows": {"old": len(old), "new": len(new)},
           "kinds": {"address": sum(1 for n in new if settings.is_address(n)), "switch": sum(1 for n in new if not settings.is_address(n))}}
    flagged = {key: value for key, value in new_counts.items() if value}
    if flagged:
        out["new_counts"] = flagged
    if make_client is not None:
        out["tokens"] = probe(environ, make_client)
    if environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        out["names"] = {"old": sorted(old), "new": sorted(new)}
    out["seconds"] = round(time.monotonic() - began, 2)
    return out
