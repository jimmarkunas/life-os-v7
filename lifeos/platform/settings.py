"""LIFE-699: V7's settings from one Notion page ("V7 settings (machine read)") instead of GitHub values; counts and names only, never a value in a log.

The page holds `NAME value` lines in code blocks (a line starting with # is a comment). Only names in MOVABLE are read; a SAFETY name is refused, an unknown one counted.
Shadow rule (until a GitHub value is deleted): Notion only fills a name the step declares empty (`X: ${{ vars.X }}` with X deleted); held names are compared.
Deleting a GitHub value is therefore the switch that makes Notion the source. Never raises: an unreadable page leaves the environment as it was (`status`).
LIFE-702: with the private store, a good read is remembered and an unreadable page falls back to it (`last_accepted`, never a SAFETY name or credential).
LIFE-701: a production run also copies the addresses its environment holds and the page lacks into the "# Notion addresses" block (`seeded`); never a switch or credential."""
import os
import re

TITLE = "V7 settings (machine read)"
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)\s+(.+?)\s*$")
ADDRESS = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
ADDRESS_SUFFIXES = ("_BLOCK_ID", "_PAGE_ID", "_DATA_SOURCE_ID", "_DB_ID")
PRIVATE_PLAIN = frozenset({"JIRA_BASE_URL", "JIRA_SITE_URL", "JIRA_BOARDS", "JIRA_GTV_EPIC", "GCAL_CALENDAR_ID", "V7_CLOSE_BASE_URL", "V7_CLOUD_FIT_PROFILE_REF", "BUZZ_STAGE_CHANNEL"})   # LIFE-699: plain settings that were masked secrets in a public repository; masked when exported, never a switch
NOTION_IDS, NOTION_PREFIX = "LIFEOS_NOTION_IDS", "LIFEOS_NOTION_"           # notion_ids.py: one id per name, as LIFEOS_NOTION_<NAME> or inside the LIFEOS_NOTION_IDS map
ADDRESS_HEADER = "# notion addresses"
SWITCH_HEADER = "# switches and lists"
SNAPSHOT_KEY = "settings:last-accepted"                                  # one row in the existing v7_private_reports table (stored_pages.STORE); keys there are prefixed
TOKENS = ("NOTION_JIRA_TOKEN", "NOTION_API_TOKEN")
ANY_NOTION_TOKEN = re.compile(r"NOTION_[A-Z0-9_]*TOKEN")                 # LIFE-699: a step without the two above reads the page with its own Notion token (the root page is shared with every connection), read-only: it never seeds

# Notion block, page and database addresses, and plain switches and lists (LIFE-699 inventory). Adding a name here is the only way Notion can set it.
MOVABLE = frozenset({
    "AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "HIRING_PIPELINE_PAGE_ID",
    "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "MEGIBOW_BLOCK_ID", "NETWORK_HANDOFF_PAGE_ID",
    "WEEKLY_REVIEW_PAGE_ID", "NOTION_AMAZON_DATA_SOURCE_ID", "NOTION_BILLS_DATA_SOURCE_ID", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "NOTION_RECRUITERS_DATA_SOURCE_ID",
    "NOTION_FIT_SYNC_PAGE_ID", "MEGIBOW_REVIEW_DB_ID",
    "FIT_PROFILE_SYNC", "GRANOLA_AUTO", "HIRING_AUTO_CLOSE", "INTERVIEW_LEADS", "INTERVIEW_OS_WRITER", "INTERVIEW_TEST_COMPANIES", "JOB_PAGE_OUTREACH",
    "RECRUITER_OUTREACH", "V7_AI_CAPACITY_CARD", "V7_FIT_GATE", "V7_IMESSAGE_ALERTS", "MAIL_PORTAL_URL", "PHYSICAL_MAIL_TRACKING",
    "BROWSER_CACHE_BUST", "JIRA_BOARDS", "JIRA_GTV_EPIC", "JIRA_BASE_URL", "JIRA_SITE_URL", "GCAL_CALENDAR_ID", "BUZZ_STAGE_CHANNEL", "V7_CLOSE_BASE_URL",
    "V7_CLOUD_FIT_PROFILE_REF", 
    "TICK_CAP_ATS", "TICK_CAP_DICE", "TICK_CAP_JOBRIGHT", "TICK_CAP_LINKEDIN", "TICK_CAP_TINYFISH", "TICK_MAX_PARALLEL", "NETWORK_SURFACE_SKIP", "INTERVIEW_CONVERT",
    "INTERVIEW_CONVERT_CAP", "PHYSICAL_MAIL_EXPECT", "MEGIBOW_EXCLUDED_DOMAINS", "JIRA_CARD_PROJECTS", "JIRA_GTV_CONTEXT_URL", 
    
})
# LIFE-699 phase 7: non-address settings a production run may copy onto the page from the step's environment (never a credential; a value with a line break is skipped).
SEEDABLE = frozenset({
    "BUZZ_STAGE_CHANNEL", "GCAL_CALENDAR_ID", "JIRA_BASE_URL", "JIRA_BOARDS", "JIRA_GTV_EPIC",
    "JIRA_SITE_URL", "V7_CLOSE_BASE_URL", "V7_CLOUD_FIT_PROFILE_REF",
})
# Never from Notion: the production write switch, the alarm, the SSH route and model spend (LIFE-699), and every credential.
SAFETY = frozenset({
    "V7_LIVE", "ALERTS_ENABLED", "ALERTS_MUTED_UNTIL", "ALERT_SETTINGS", "V7_STORE_JUMP_HOST", "V7_STORE_JUMP_KNOWN_HOSTS", "V7_CLOUD_FIT", "V7_CLOUD_FIT_SCHEDULED",
    "V7_CLOUD_FIT_BACKLOG_BUDGET", "V7_CLOUD_FIT_BACKLOG_MAX", "V7_CLOUD_FIT_PARALLEL", "LIFEOS_BWS_RUNTIME_TOKEN",
    "V7_PIPELINE", "GATE_MIN_GAP_MINUTES", "V7_STORE_VIA", "V7_CLOUD_FIT_BACKLOG_PAGE", "INTERVIEW_ACCEPTANCE_JSON", "INTERVIEW_ACCEPTANCE_NEGATIVE_JSON",      # LIFE-699: Bitwarden, never the page
})


def is_address(name):
    return name.endswith(ADDRESS_SUFFIXES) or (name.startswith(NOTION_PREFIX) and name != NOTION_IDS)


def movable(name):
    return name in MOVABLE or (name.startswith(NOTION_PREFIX) and name != NOTION_IDS)


def _bare(value):
    from lifeos.platform.report_region import clean_id                     # noqa: PLC0415 - a copied Notion link counts as its block id
    return clean_id(value).strip().replace("collection://", "").replace("-", "").lower()


def _plain(block):
    inner = block.get(block.get("type")) or {}
    return "".join(p.get("plain_text") or (p.get("text") or {}).get("content") or "" for p in inner.get("rich_text") or [] if isinstance(p, dict))


def parse(lines):
    """`NAME value` lines -> ({name: value}, counts). Refused, unknown, invalid and repeated names are counted, never applied."""
    found, counts = {}, {"refused": [], "unknown": 0, "invalid": [], "repeated": []}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.match(line)
        if not match:
            counts["unknown"] += 1
            continue
        name, value = match.groups()
        if name in SAFETY or name.endswith(("_TOKEN", "_SECRET", "_KEY", "_PASSWORD")):
            counts["refused"].append(name)
        elif not movable(name):
            counts["unknown"] += 1
        elif is_address(name) and not ADDRESS.match(value.replace("collection://", "")):
            counts["invalid"].append(name)
        elif name in found:
            counts["repeated"].append(name)
        else:
            found[name] = value
    return found, counts


class SettingsError(RuntimeError):
    """Fixed codes only."""


def _title(obj):
    for prop in (obj.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(p.get("plain_text") or "" for p in prop.get("title") or [])
    return ""


def read(client):
    """-> the page's `NAME value` lines. The page is found by its exact title; none or several raises SETTINGS_PAGE_NOT_FOUND."""
    return [line for _, text in _blocks(client) for line in text.splitlines()]


def _blocks(client):
    """-> [(block, text)] for every code block on the page, in order."""
    from lifeos.platform import report_region                             # noqa: PLC0415
    found = client.call("POST", "/search", {"query": TITLE, "filter": {"property": "object", "value": "page"}, "page_size": 100})
    hits = [r["id"] for r in (found or {}).get("results") or [] if isinstance(r, dict) and _title(r).strip() == TITLE and not r.get("in_trash") and not r.get("archived")]
    if len(hits) != 1:
        raise SettingsError("SETTINGS_PAGE_NOT_FOUND")
    return [(block, _plain(block)) for block in report_region.children(client, hits[0], SettingsError) if block.get("type") == "code"]


def held(environ, name):
    """What the environment holds for a name: the variable itself, or (LIFEOS_NOTION_<NAME>) its entry in the LIFEOS_NOTION_IDS map."""
    value = (environ.get(name) or "").strip()
    if value or not name.startswith(NOTION_PREFIX) or name == NOTION_IDS:
        return value
    import json                                                            # noqa: PLC0415
    try:
        mapped = json.loads(environ.get(NOTION_IDS) or "{}").get(name[len(NOTION_PREFIX):])
    except (ValueError, AttributeError):
        return ""
    return str(mapped).strip() if mapped else ""


def wanted(environ, found, invalid=()):
    """The `NAME value` address lines the environment holds and the page lacks (valid addresses only; never a switch or credential)."""
    names = {n for n in MOVABLE if is_address(n)}
    try:
        import json                                                        # noqa: PLC0415
        names |= {NOTION_PREFIX + k for k in json.loads(environ.get(NOTION_IDS) or "{}") if str(k).replace("_", "").isalnum() and str(k).isupper()}
    except (ValueError, AttributeError, TypeError):
        pass
    from lifeos.platform.report_region import clean_id                     # noqa: PLC0415 - a copied Notion link is written as its bare block id
    out = []
    for name in sorted(names - set(found) - set(invalid)):
        value = clean_id(held(environ, name))
        if value and ADDRESS.match(value.replace("collection://", "")):
            out.append(f"{name} {value.replace('collection://', '')}")
    return out


def wanted_settings(environ, found):
    """The SEEDABLE `NAME value` lines the environment holds and the page lacks (one line each; never a credential)."""
    return [f"{name} {value}" for name in sorted(SEEDABLE - set(found)) if (value := (environ.get(name) or "").strip()) and "\n" not in value and "\r" not in value]


def seed(client, lines, header=ADDRESS_HEADER):
    """Append lines to the code block that starts with `header` ("# Notion addresses" by default), one per name (the first line for a name wins), then read it back. -> names written."""
    if not lines:
        return []
    from lifeos.platform.notion_client import rich_text                    # noqa: PLC0415
    from urllib.parse import quote                                         # noqa: PLC0415
    blocks = [(b, t) for b, t in _blocks(client) if t.strip().lower().startswith(header)]
    if len(blocks) != 1:
        raise SettingsError("SETTINGS_SEED_TARGET_MISSING")
    block, text = blocks[0]
    have = {m.group(1) for m in (LINE.match(x.strip()) for x in text.splitlines()) if m}
    new = [x for x in lines if x.split(" ", 1)[0] not in have]
    if not new:
        return []
    body = text.rstrip("\n") + "\n" + "\n".join(new)
    language = (block.get("code") or {}).get("language") or "plain text"
    client.call_once("PATCH", f"/blocks/{quote(block['id'], safe='')}", {"code": {"rich_text": rich_text(body), "language": language}})
    after = {m.group(1) for b, t in _blocks(client) if t.strip().lower().startswith(header) for m in (LINE.match(x.strip()) for x in t.splitlines()) if m}
    names = [x.split(" ", 1)[0] for x in new]
    if not set(names) <= after:
        raise SettingsError("SETTINGS_SEED_VERIFY_FAILED")
    return names


def _fill(environ, values, out):
    """The shadow rule: fill a name the step declares empty, compare a held one, count an undeclared one."""
    out.setdefault("matched_names", [])                                    # LIFE-703: the NAMES whose GitHub value equalled the page (never a value), for a per-name 24 hour match list
    for name, value in sorted(values.items()):
        have = held(environ, name)
        if not have and name not in environ and not (name.startswith(NOTION_PREFIX) and NOTION_IDS in environ):      # a step that never declares a name never receives it (a step that declares the LIFEOS_NOTION_IDS map receives its names, LIFE-699)
            out["undeclared"] = out.get("undeclared", 0) + 1
        elif not have:
            environ[name] = value
            out["filled"].append(name)
        elif _bare(have) == _bare(value) if is_address(name) else have == value:
            out["matched"] += 1
            out["matched_names"].append(name)
        else:
            out["mismatched"].append(name)


def _store_connect(environ):
    """The private store's connect(), or None when this step has no store access."""
    if not (environ.get("LIFEOS_ACQ_DB_NAME") or "").strip():
        return None
    from lifeos.platform import db                                        # noqa: PLC0415
    return db.connect


def remember(connect, values, now=None):
    """Save the accepted map when it differs from the saved one, with read-back. -> True when written."""
    from datetime import datetime, timezone                               # noqa: PLC0415
    from lifeos.platform.stored_pages import STORE                       # noqa: PLC0415
    values = {n: v for n, v in values.items() if movable(n) and n not in SAFETY}
    with connect() as connection:
        STORE.ensure(connection)
        saved = STORE.load(connection, SNAPSHOT_KEY)
        if (saved or {}).get("values") == values:
            return False
        snapshot = {"taken_at": (now or datetime.now(timezone.utc)).isoformat(), "schema": 1, "values": values}
        STORE.save(connection, SNAPSHOT_KEY, snapshot)
        if STORE.load(connection, SNAPSHOT_KEY) != snapshot:
            raise SettingsError("SETTINGS_REMEMBER_READBACK")
    return True


def recall(connect):
    """-> (values, taken_at) of the last accepted map, or ({}, None)."""
    from lifeos.platform.stored_pages import STORE                       # noqa: PLC0415
    with connect() as connection:
        saved = STORE.load(connection, SNAPSHOT_KEY)
    values = (saved or {}).get("values") or {}
    return {n: v for n, v in values.items() if isinstance(v, str) and movable(n) and n not in SAFETY}, (saved or {}).get("taken_at")


def _fallback(environ, connect, out):
    """The page could not be read: fill from the last accepted map, if this step has the store."""
    if connect is None:
        return
    try:
        values, taken = recall(connect)
    except Exception as error:                                             # noqa: BLE001 - the store is a fallback; never fail a stage for it
        out["recall_status"] = str(error) if re.fullmatch(r"[A-Z0-9_]{3,60}", str(error)) else type(error).__name__
        return
    if values:
        from datetime import datetime                                     # noqa: PLC0415
        from lifeos.platform import ct_stamp                              # noqa: PLC0415
        out["last_accepted"] = ct_stamp.when(datetime.fromisoformat(taken)) if taken else "unknown"
        _fill(environ, values, out)


def apply(environ=os.environ, client=None, copy=None, connect=None):
    """Fill the environment from the page under the shadow rule; `copy` (default: production runs) also copies missing addresses onto the page. -> counts (names only)."""
    out = {"status": "off", "filled": [], "matched": 0, "mismatched": []}
    switch = (environ.get("LIFEOS_SETTINGS") or "").strip().lower()
    copy = (client is None and environ.get("GITHUB_ACTIONS") == "true") if copy is None else copy
    if switch == "off" or (client is None and switch != "on" and environ.get("GITHUB_ACTIONS") != "true"):     # production runs and an explicit "on" only; tests and local runs stay as they are
        return out
    token = next((environ.get(t).strip() for t in TOKENS if (environ.get(t) or "").strip()), "")
    read_only = ""
    if not token and client is None:
        read_only = next((k for k in sorted(environ) if k not in TOKENS and ANY_NOTION_TOKEN.fullmatch(k) and (environ.get(k) or "").strip()), "")
        token = environ.get(read_only, "").strip() if read_only else ""
        copy = copy and not read_only                                      # a step's own token only reads the page; seeding stays with the two tokens above
        if read_only:
            out["read_only_token"] = read_only                             # the name only, never the value
    connect = connect if connect is not None else (_store_connect(environ) if copy else None)
    if client is None and not token:
        out["status"] = "no_token"
        _fallback(environ, connect, out)
        return out
    try:
        if client is None:
            from lifeos.platform.notion_client import Client                # noqa: PLC0415
            client = Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})
        found, counts = parse(read(client))
    except Exception as error:                                             # noqa: BLE001 - settings never fail a stage; the environment stays as it was
        out["status"] = "unreadable:" + (str(error) if re.fullmatch(r"[A-Z0-9_]{3,60}", str(error)) else type(error).__name__)
        _fallback(environ, connect, out)
        return out
    out.update(counts, status="ok")
    _fill(environ, found, out)
    if connect is not None:
        try:
            out["remembered"] = remember(connect, found)
        except Exception as error:                                         # noqa: BLE001 - remembering is best effort; the next good read tries again
            out["remember_status"] = str(error) if re.fullmatch(r"[A-Z0-9_]{3,60}", str(error)) else type(error).__name__
    if copy:
        try:
            out["seeded"] = seed(client, wanted(environ, found, counts["invalid"])) + seed(client, wanted_settings(environ, found), SWITCH_HEADER)
        except Exception as error:                                         # noqa: BLE001 - copying is best effort; the next run tries again
            out["seed_status"] = str(error) if re.fullmatch(r"[A-Z0-9_]{3,60}", str(error)) else type(error).__name__
    return out


def main(argv=None, environ=None, client=None):
    """`python -m lifeos.platform.settings [--output NAME ...]` (LIFE-703): the pre-step for a workflow step that does not go through lifeos.run, and the gate job's reader.

    Reads the page once and applies the shadow rule to a copy of the environment, then writes each name Notion filled to $GITHUB_ENV (so later steps see it) and each --output name's
    effective value to $GITHUB_OUTPUT (so a job condition can use `needs.<job>.outputs.NAME`). Only movable, non-safety names are ever written; a value with a line break is refused.
    Prints counts and names only, masks every written address (never a plain switch such as on or true), and always exits 0: an unreadable page leaves everything as it was."""
    import json                                                            # noqa: PLC0415
    import sys                                                             # noqa: PLC0415
    args = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ
    outputs = [args[i + 1] for i, a in enumerate(args) if a == "--output" and i + 1 < len(args)]
    env = dict(environ)
    counts = apply(env, client=client)                                     # LIFE-699: a production run seeds here too (the branches job declares WEEKLY_REVIEW_PAGE_ID); tests pass a client, which never copies

    def writable(name, value):
        return movable(name) and name not in SAFETY and isinstance(value, str) and "\n" not in value and "\r" not in value

    refused = []

    def write(path, name, value):
        if not (path and writable(name, value)):
            if path:
                refused.append(name)
            return
        if is_address(name) or name in PRIVATE_PLAIN:                                             # an address is masked; a switch is not: GitHub drops a job output that contains any masked text, so masking "on" or "true" silently emptied every gate output (Nov 15 2026)
            print(f"::add-mask::{value}")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")

    for name in counts["filled"]:
        write(environ.get("GITHUB_ENV"), name, env.get(name))
    for name in outputs:
        write(environ.get("GITHUB_OUTPUT"), name, held(env, name))
    if refused:
        counts["not_written"] = sorted(set(refused))
    print("settings:", json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
