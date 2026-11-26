"""V7's settings come from the Notion Runtime Configuration source; counts and names only, never a value. Only MOVABLE
names are read; SAFETY names are refused. Shadow rule: Notion fills only a name the step declares empty (`X: ${{
vars.X }}` with X deleted); held names are compared. Never raises: an unreadable source leaves the environment as it
was, or uses the last accepted map from the private store.
"""
import os
import re

ADDRESS = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
ADDRESS_SUFFIXES = ("_BLOCK_ID", "_PAGE_ID", "_DATA_SOURCE_ID", "_DB_ID")
# masked when exported, never a switch
PRIVATE_PLAIN = frozenset({"JIRA_BASE_URL", "JIRA_SITE_URL", "JIRA_BOARDS", "JIRA_GTV_EPIC", "GCAL_CALENDAR_ID", "V7_CLOSE_BASE_URL", "V7_CLOUD_FIT_PROFILE_REF", "BUZZ_STAGE_CHANNEL"})
NOTION_IDS, NOTION_PREFIX = "LIFEOS_NOTION_IDS", "LIFEOS_NOTION_"
SNAPSHOT_KEY = "settings:last-accepted"
TOKENS = ("NOTION_JIRA_TOKEN", "NOTION_API_TOKEN")
ANY_NOTION_TOKEN = re.compile(r"NOTION_[A-Z0-9_]*TOKEN")

# Notion addresses, plain switches and lists; adding a name here is the only way Notion can set it
MOVABLE = frozenset({
    "AMAZON_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "HIRING_PIPELINE_PAGE_ID",
    "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "GTV_CARD_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "MEGIBOW_BLOCK_ID", "NETWORK_HANDOFF_PAGE_ID",
    "WEEKLY_REVIEW_PAGE_ID", "WEEKLY_ARCHIVE_PAGE_ID", "NOTION_WEEKLY_REVIEWS_DATA_SOURCE_ID", "WEEKLY_REVIEW", "NOTION_AMAZON_DATA_SOURCE_ID", "NOTION_BILLS_DATA_SOURCE_ID", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "NOTION_RECRUITERS_DATA_SOURCE_ID",
    "NOTION_FIT_SYNC_PAGE_ID", "MEGIBOW_REVIEW_DB_ID",
    "FIT_PROFILE_SYNC", "GRANOLA_AUTO", "HIRING_AUTO_CLOSE", "INTERVIEW_LEADS", "INTERVIEW_OS_WRITER", "INTERVIEW_TEST_COMPANIES", "JOB_PAGE_OUTREACH",
    "RECRUITER_OUTREACH", "V7_AI_CAPACITY_CARD", "V7_FIT_GATE", "V7_IMESSAGE_ALERTS", "MAIL_PORTAL_URL", "PHYSICAL_MAIL_TRACKING",
    "BROWSER_CACHE_BUST", "JIRA_BOARDS", "JIRA_GTV_EPIC", "JIRA_BASE_URL", "JIRA_SITE_URL", "GCAL_CALENDAR_ID", "BUZZ_STAGE_CHANNEL", "V7_CLOSE_BASE_URL",
    "V7_CLOUD_FIT_PROFILE_REF", 
    "TICK_CAP_ATS", "TICK_CAP_DICE", "TICK_CAP_JOBRIGHT", "TICK_CAP_LINKEDIN", "TICK_CAP_TINYFISH", "TICK_MAX_PARALLEL", "NETWORK_SURFACE_SKIP", "INTERVIEW_CONVERT",
    "INTERVIEW_CONVERT_CAP", "PHYSICAL_MAIL_EXPECT", "MEGIBOW_EXCLUDED_DOMAINS", "JIRA_CARD_PROJECTS", "JIRA_GTV_CONTEXT_URL", 
    "RECRUITER_COUNT_BLOCK_ID",
    "MEJ_SPRINT_AUTOMATION", "V3_WEEK_VIEW", "V2_QUARTER_SHADOW", "V2_PROMOTION", "V2_OFFICIAL", "JIRA_WRITERS_VERIFIED", "MEJ_AUTOMATION_SINCE", "JIRA_FUTURE_SPRINTS",
    # on: an orchestrator dispatch starts the released ticket's agent session
    "ORCHESTRATOR_CAN_START",
    
})
# Never from Notion: the production write switch, the alarm, the SSH route, model spend, credentials.
SAFETY = frozenset({
    "V7_LIVE", "ALERTS_ENABLED", "ALERTS_MUTED_UNTIL", "ALERT_SETTINGS", "V7_STORE_JUMP_HOST", "V7_STORE_JUMP_KNOWN_HOSTS", "V7_CLOUD_FIT", "V7_CLOUD_FIT_SCHEDULED",
    "V7_CLOUD_FIT_BACKLOG_BUDGET", "V7_CLOUD_FIT_BACKLOG_MAX", "V7_CLOUD_FIT_PARALLEL", "LIFEOS_BWS_RUNTIME_TOKEN",
    "V7_PIPELINE", "GATE_MIN_GAP_MINUTES", "V7_STORE_VIA", "V7_CLOUD_FIT_BACKLOG_PAGE", "INTERVIEW_ACCEPTANCE_JSON", "INTERVIEW_ACCEPTANCE_NEGATIVE_JSON",
})


def is_address(name):
    return name.endswith(ADDRESS_SUFFIXES) or (name.startswith(NOTION_PREFIX) and name != NOTION_IDS)


def movable(name):
    return name in MOVABLE or (name.startswith(NOTION_PREFIX) and name != NOTION_IDS)


def _bare(value):
    from lifeos.platform.report_region import clean_id  # noqa: PLC0415
    return clean_id(value).strip().replace("collection://", "").replace("-", "").lower()


def held(environ, name):
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
    """Fill a name the step declares empty, compare a held one, count an undeclared one."""
    out.setdefault("matched_names", [])
    for name, value in sorted(values.items()):
        have = held(environ, name)
        if not have and name not in environ and not (name.startswith(NOTION_PREFIX) and NOTION_IDS in environ):
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
    if not (environ.get("LIFEOS_ACQ_DB_NAME") or "").strip():
        return None
    from lifeos.platform import db                                        # noqa: PLC0415
    return db.connect


def remember(connect, values, now=None):
    """Save the accepted map when it changed, with read-back -> True when written."""
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
    from lifeos.platform.stored_pages import STORE                       # noqa: PLC0415
    with connect() as connection:
        saved = STORE.load(connection, SNAPSHOT_KEY)
    values = (saved or {}).get("values") or {}
    return {n: v for n, v in values.items() if isinstance(v, str) and movable(n) and n not in SAFETY}, (saved or {}).get("taken_at")


def _fallback(environ, connect, out):
    if connect is None:
        return
    try:
        values, taken = recall(connect)
    except Exception as error:  # noqa: BLE001
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
    """Read the source by V7_RUNTIME_CONFIG_SOURCE_ID, fill under the shadow rule, remember the map -> True when applied.
    """
    if not (environ.get("V7_RUNTIME_CONFIG_SOURCE_ID") or "").strip():
        out["status"] = "no_source_id"
        return False
    from lifeos.platform import runtime_config  # noqa: PLC0415 - circular import
    try:
        found, counts = runtime_config.parse(runtime_config.fetch(client, runtime_config.source_id(environ)))
        if not found:
            raise runtime_config.RuntimeConfigError("RUNTIME_CONFIG_EMPTY")
    except Exception as error:  # noqa: BLE001
        out["status"] = "unreadable:" + _code(error)
        return False
    out.update(counts, status="ok", source="new")
    _fill(environ, found, out)
    if connect is not None:
        try:
            out["remembered"] = remember(connect, found)
        except Exception as error:  # noqa: BLE001
            out["remember_status"] = _code(error)
    return True


def apply(environ=os.environ, client=None, connect=None):
    out = {"status": "off", "filled": [], "matched": 0, "mismatched": []}
    switch = (environ.get("LIFEOS_SETTINGS") or "").strip().lower()
    # production runs and an explicit "on" only
    if switch == "off" or (client is None and switch != "on" and environ.get("GITHUB_ACTIONS") != "true"):
        return out
    token = next((environ.get(t).strip() for t in TOKENS if (environ.get(t) or "").strip()), "")
    store = client is None and environ.get("GITHUB_ACTIONS") == "true"
    if not token and client is None:
        read_only = next((k for k in sorted(environ) if k not in TOKENS and ANY_NOTION_TOKEN.fullmatch(k) and (environ.get(k) or "").strip()), "")
        token = environ.get(read_only, "").strip() if read_only else ""
        store = store and not read_only
        if read_only:
            out["read_only_token"] = read_only
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
    except Exception as error:  # noqa: BLE001
        out["status"] = "unreadable:" + _code(error)
    _fallback(environ, connect, out)
    return out


def main(argv=None, environ=None, client=None):
    """`python -m lifeos.platform.settings [--output NAME ...]`: pre-step for workflow steps that skip lifeos.run, and
    the gate job's reader. Writes names Notion filled to $GITHUB_ENV and each --output name to $GITHUB_OUTPUT; prints
    counts and names; always exits 0.
    """
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
        # masked text empties a job output, so only addresses are masked, never on or true
        if is_address(name) or name in PRIVATE_PLAIN:
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
