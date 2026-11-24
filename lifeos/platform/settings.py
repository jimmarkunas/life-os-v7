"""LIFE-699 / LIFE-859: V7's settings come from the Notion Runtime Configuration source; counts and names only, never a value in a log.

One row per setting. LIFE-892 switched to it and LIFE-859 retired the old settings page reader.

Only names in MOVABLE are read; a SAFETY name is refused. Shadow rule (until a GitHub value is deleted): Notion only fills a name the step declares empty (`X: ${{ vars.X }}` with X deleted); held names are compared.
Deleting a GitHub value is therefore the switch that makes Notion the source. Never raises: an unreadable source leaves the environment as it was (`status`).
LIFE-702: with the private store, a good read is remembered and an unreadable, empty or unset source falls back to it (`last_accepted`, never a SAFETY name or credential). That is the recovery route: it never reads the retired page.
LIFE-859: the old page reader, its seeding and the `V7_RUNTIME_CONFIG_SOURCE_ID`-empty fallback to that page are gone (REQ-52 stays as history, Superseded); nothing writes a settings source."""
import os
import re

ADDRESS = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
ADDRESS_SUFFIXES = ("_BLOCK_ID", "_PAGE_ID", "_DATA_SOURCE_ID", "_DB_ID")
PRIVATE_PLAIN = frozenset({"JIRA_BASE_URL", "JIRA_SITE_URL", "JIRA_BOARDS", "JIRA_GTV_EPIC", "GCAL_CALENDAR_ID", "V7_CLOSE_BASE_URL", "V7_CLOUD_FIT_PROFILE_REF", "BUZZ_STAGE_CHANNEL"})   # LIFE-699: plain settings that were masked secrets in a public repository; masked when exported, never a switch
NOTION_IDS, NOTION_PREFIX = "LIFEOS_NOTION_IDS", "LIFEOS_NOTION_"           # notion_ids.py: one id per name, as LIFEOS_NOTION_<NAME> or inside the LIFEOS_NOTION_IDS map
SNAPSHOT_KEY = "settings:last-accepted"                                  # one row in the existing v7_private_reports table (stored_pages.STORE); keys there are prefixed
TOKENS = ("NOTION_JIRA_TOKEN", "NOTION_API_TOKEN")
ANY_NOTION_TOKEN = re.compile(r"NOTION_[A-Z0-9_]*TOKEN")                 # LIFE-699: a step without the two above reads the source with its own Notion token, read-only

# Notion block, page and database addresses, and plain switches and lists (LIFE-699 inventory). Adding a name here is the only way Notion can set it.
MOVABLE = frozenset({
    "AMAZON_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "HIRING_PIPELINE_PAGE_ID",
    "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "MEGIBOW_BLOCK_ID", "NETWORK_HANDOFF_PAGE_ID",
    "WEEKLY_REVIEW_PAGE_ID", "WEEKLY_ARCHIVE_PAGE_ID", "WEEKLY_REVIEW_POINTER_PAGE_ID", "NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID", "WEEKLY_REVIEW", "NOTION_AMAZON_DATA_SOURCE_ID", "NOTION_BILLS_DATA_SOURCE_ID", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "NOTION_RECRUITERS_DATA_SOURCE_ID",
    "NOTION_FIT_SYNC_PAGE_ID", "MEGIBOW_REVIEW_DB_ID",
    "FIT_PROFILE_SYNC", "GRANOLA_AUTO", "HIRING_AUTO_CLOSE", "INTERVIEW_LEADS", "INTERVIEW_OS_WRITER", "INTERVIEW_TEST_COMPANIES", "JOB_PAGE_OUTREACH",
    "RECRUITER_OUTREACH", "V7_AI_CAPACITY_CARD", "V7_FIT_GATE", "V7_IMESSAGE_ALERTS", "MAIL_PORTAL_URL", "PHYSICAL_MAIL_TRACKING",
    "BROWSER_CACHE_BUST", "JIRA_BOARDS", "JIRA_GTV_EPIC", "JIRA_BASE_URL", "JIRA_SITE_URL", "GCAL_CALENDAR_ID", "BUZZ_STAGE_CHANNEL", "V7_CLOSE_BASE_URL",
    "V7_CLOUD_FIT_PROFILE_REF", 
    "TICK_CAP_ATS", "TICK_CAP_DICE", "TICK_CAP_JOBRIGHT", "TICK_CAP_LINKEDIN", "TICK_CAP_TINYFISH", "TICK_MAX_PARALLEL", "NETWORK_SURFACE_SKIP", "INTERVIEW_CONVERT",
    "INTERVIEW_CONVERT_CAP", "PHYSICAL_MAIL_EXPECT", "MEGIBOW_EXCLUDED_DOMAINS", "JIRA_CARD_PROJECTS", "JIRA_GTV_CONTEXT_URL", 
    
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


def _code(error):
    return str(error) if re.fullmatch(r"[A-Z0-9_]{3,60}", str(error)) else type(error).__name__


def _from_source(environ, client, connect, out):
    """LIFE-892 / LIFE-859: read the Runtime Configuration source by `V7_RUNTIME_CONFIG_SOURCE_ID`, fill under the shadow rule, remember the map as last accepted and say `source: new`. -> True when it was applied.
    No ID, or an unreadable, empty or malformed source: False with `status` `no_source_id` or `unreadable:<CODE>`; the caller then uses the last accepted map, never any other source."""
    if not (environ.get("V7_RUNTIME_CONFIG_SOURCE_ID") or "").strip():
        out["status"] = "no_source_id"
        return False
    from lifeos.platform import runtime_config                             # noqa: PLC0415 - runtime_config imports this module
    try:
        found, counts = runtime_config.parse(runtime_config.fetch(client, runtime_config.source_id(environ)))
        if not found:
            raise runtime_config.RuntimeConfigError("RUNTIME_CONFIG_EMPTY")  # never an empty override
    except Exception as error:                                             # noqa: BLE001 - the cause may hold a value; only a fixed code is kept
        out["status"] = "unreadable:" + _code(error)
        return False
    out.update(counts, status="ok", source="new")
    _fill(environ, found, out)
    if connect is not None:
        try:
            out["remembered"] = remember(connect, found)
        except Exception as error:                                         # noqa: BLE001 - remembering is best effort; the next good read tries again
            out["remember_status"] = _code(error)
    return True


def apply(environ=os.environ, client=None, connect=None):
    """Fill the environment from the Runtime Configuration source under the shadow rule. -> counts (names only). A source that cannot be read leaves the environment as it was, or fills it from the last accepted map."""
    out = {"status": "off", "filled": [], "matched": 0, "mismatched": []}
    switch = (environ.get("LIFEOS_SETTINGS") or "").strip().lower()
    if switch == "off" or (client is None and switch != "on" and environ.get("GITHUB_ACTIONS") != "true"):     # production runs and an explicit "on" only; tests and local runs stay as they are
        return out
    token = next((environ.get(t).strip() for t in TOKENS if (environ.get(t) or "").strip()), "")
    store = client is None and environ.get("GITHUB_ACTIONS") == "true"      # only a production run reaches the private store
    if not token and client is None:
        read_only = next((k for k in sorted(environ) if k not in TOKENS and ANY_NOTION_TOKEN.fullmatch(k) and (environ.get(k) or "").strip()), "")
        token = environ.get(read_only, "").strip() if read_only else ""
        store = store and not read_only                                    # a step's own read-only token reads the source and never touches the store
        if read_only:
            out["read_only_token"] = read_only                             # the name only, never the value
    connect = connect if connect is not None else (_store_connect(environ) if store else None)
    if client is None and not token:
        out["status"] = "no_token"
        _fallback(environ, connect, out)
        return out
    try:
        if client is None:
            from lifeos.platform.notion_client import Client                # noqa: PLC0415
            client = Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})
        if _from_source(environ, client, connect, out):
            return out
    except Exception as error:                                             # noqa: BLE001 - settings never fail a stage; the environment stays as it was
        out["status"] = "unreadable:" + _code(error)
    _fallback(environ, connect, out)
    return out


def main(argv=None, environ=None, client=None):
    """`python -m lifeos.platform.settings [--output NAME ...]` (LIFE-703): the pre-step for a workflow step that does not go through lifeos.run, and the gate job's reader.

    Reads the source once and applies the shadow rule to a copy of the environment, then writes each name Notion filled to $GITHUB_ENV (so later steps see it) and each --output name's
    effective value to $GITHUB_OUTPUT (so a job condition can use `needs.<job>.outputs.NAME`). Only movable, non-safety names are ever written; a value with a line break is refused.
    Prints counts and names only, masks every written address (never a plain switch such as on or true), and always exits 0: an unreadable source leaves everything as it was."""
    import json                                                            # noqa: PLC0415
    import sys                                                             # noqa: PLC0415
    args = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ
    outputs = [args[i + 1] for i, a in enumerate(args) if a == "--output" and i + 1 < len(args)]
    env = dict(environ)
    counts = apply(env, client=client)

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
