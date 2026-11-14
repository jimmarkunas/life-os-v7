"""LIFE-699: V7's settings from one Notion page ("V7 settings (machine read)") instead of GitHub values; counts and names only, never a value in a log.

The page holds `NAME value` lines in code blocks (a line starting with # is a comment). Only names in MOVABLE are read; a SAFETY name is refused, an unknown one counted.
Shadow rule (until a GitHub value is deleted): Notion only fills a name the step declares empty (`X: ${{ vars.X }}` with X deleted); held names are compared.
Deleting a GitHub value is therefore the switch that makes Notion the source. Never raises: an unreadable page leaves the environment as it was (`status`).
LIFE-701: a production run also copies the addresses its environment holds and the page lacks into the "# Notion addresses" block (`seeded`); never a switch or credential."""
import os
import re

TITLE = "V7 settings (machine read)"
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)\s+(.+?)\s*$")
ADDRESS = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
ADDRESS_SUFFIXES = ("_BLOCK_ID", "_PAGE_ID", "_DATA_SOURCE_ID", "_DB_ID")
NOTION_IDS, NOTION_PREFIX = "LIFEOS_NOTION_IDS", "LIFEOS_NOTION_"           # notion_ids.py: one id per name, as LIFEOS_NOTION_<NAME> or inside the LIFEOS_NOTION_IDS map
ADDRESS_HEADER = "# notion addresses"
TOKENS = ("NOTION_JIRA_TOKEN", "NOTION_API_TOKEN")

# Notion block, page and database addresses, and plain switches and lists (LIFE-699 inventory). Adding a name here is the only way Notion can set it.
MOVABLE = frozenset({
    "AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "CALENDAR_CARD_BLOCK_ID", "HIRING_CARD_BLOCK_ID", "HIRING_PIPELINE_PAGE_ID",
    "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "JIRA_CARD_BLOCK_ID", "MAIL_ALERTS_CARD_BLOCK_ID", "MEGIBOW_BLOCK_ID", "NETWORK_HANDOFF_PAGE_ID",
    "WEEKLY_REVIEW_PAGE_ID", "NOTION_AMAZON_DATA_SOURCE_ID", "NOTION_BILLS_DATA_SOURCE_ID", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "NOTION_RECRUITERS_DATA_SOURCE_ID",
    "NOTION_FIT_SYNC_PAGE_ID", "MEGIBOW_REVIEW_DB_ID",
    "FIT_PROFILE_SYNC", "GRANOLA_AUTO", "HIRING_AUTO_CLOSE", "INTERVIEW_LEADS", "INTERVIEW_OS_WRITER", "INTERVIEW_TEST_COMPANIES", "JOB_PAGE_OUTREACH",
    "RECRUITER_OUTREACH", "V7_AI_CAPACITY_CARD", "V7_PIPELINE", "V7_FIT_GATE", "V7_IMESSAGE_ALERTS", "MAIL_PORTAL_URL", "PHYSICAL_MAIL_TRACKING",
    "BROWSER_CACHE_BUST", "JIRA_BOARDS", "JIRA_GTV_EPIC", "JIRA_BASE_URL", "JIRA_SITE_URL", "GCAL_CALENDAR_ID", "BUZZ_STAGE_CHANNEL", "V7_CLOSE_BASE_URL",
    "V7_CLOUD_FIT_PROFILE_REF", "INTERVIEW_ACCEPTANCE_JSON", "INTERVIEW_ACCEPTANCE_NEGATIVE_JSON",
    "TICK_CAP_ATS", "TICK_CAP_DICE", "TICK_CAP_JOBRIGHT", "TICK_CAP_LINKEDIN", "TICK_CAP_TINYFISH", "TICK_MAX_PARALLEL", "NETWORK_SURFACE_SKIP", "INTERVIEW_CONVERT",
    "INTERVIEW_CONVERT_CAP", "PHYSICAL_MAIL_EXPECT", "MEGIBOW_EXCLUDED_DOMAINS", "JIRA_CARD_PROJECTS", "JIRA_GTV_CONTEXT_URL", "GATE_MIN_GAP_MINUTES",
    "V7_CLOUD_FIT_BACKLOG_PAGE",
})
# Never from Notion: the production write switch, the alarm, the SSH route and model spend (LIFE-699), and every credential.
SAFETY = frozenset({
    "V7_LIVE", "ALERTS_ENABLED", "ALERTS_MUTED_UNTIL", "ALERT_SETTINGS", "V7_STORE_JUMP_HOST", "V7_STORE_JUMP_KNOWN_HOSTS", "V7_CLOUD_FIT", "V7_CLOUD_FIT_SCHEDULED",
    "V7_CLOUD_FIT_BACKLOG_BUDGET", "V7_CLOUD_FIT_BACKLOG_MAX", "V7_CLOUD_FIT_PARALLEL", "LIFEOS_BWS_RUNTIME_TOKEN",
})


def is_address(name):
    return name.endswith(ADDRESS_SUFFIXES) or (name.startswith(NOTION_PREFIX) and name != NOTION_IDS)


def movable(name):
    return name in MOVABLE or (name.startswith(NOTION_PREFIX) and name != NOTION_IDS)


def _bare(value):
    return value.strip().replace("collection://", "").replace("-", "").lower()


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
    out = []
    for name in sorted(names - set(found) - set(invalid)):
        value = held(environ, name)
        if value and ADDRESS.match(value.replace("collection://", "")):
            out.append(f"{name} {value.replace('collection://', '')}")
    return out


def seed(client, lines):
    """Append address lines to the "# Notion addresses" code block, one per name (the first line for a name wins), then read it back. -> names written."""
    if not lines:
        return []
    from lifeos.platform.notion_client import rich_text                    # noqa: PLC0415
    from urllib.parse import quote                                         # noqa: PLC0415
    blocks = [(b, t) for b, t in _blocks(client) if t.strip().lower().startswith(ADDRESS_HEADER)]
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
    after = {m.group(1) for b, t in _blocks(client) if t.strip().lower().startswith(ADDRESS_HEADER) for m in (LINE.match(x.strip()) for x in t.splitlines()) if m}
    names = [x.split(" ", 1)[0] for x in new]
    if not set(names) <= after:
        raise SettingsError("SETTINGS_SEED_VERIFY_FAILED")
    return names


def apply(environ=os.environ, client=None, copy=None):
    """Fill the environment from the page under the shadow rule; `copy` (default: production runs) also copies missing addresses onto the page. -> counts (names only)."""
    out = {"status": "off", "filled": [], "matched": 0, "mismatched": []}
    switch = (environ.get("LIFEOS_SETTINGS") or "").strip().lower()
    copy = (client is None and environ.get("GITHUB_ACTIONS") == "true") if copy is None else copy
    if switch == "off" or (client is None and switch != "on" and environ.get("GITHUB_ACTIONS") != "true"):     # production runs and an explicit "on" only; tests and local runs stay as they are
        return out
    token = next((environ.get(t).strip() for t in TOKENS if (environ.get(t) or "").strip()), "")
    if client is None and not token:
        out["status"] = "no_token"
        return out
    try:
        if client is None:
            from lifeos.platform.notion_client import Client                # noqa: PLC0415
            client = Client({"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})
        found, counts = parse(read(client))
    except Exception as error:                                             # noqa: BLE001 - settings never fail a stage; the environment stays as it was
        out["status"] = "unreadable:" + (str(error) if re.fullmatch(r"[A-Z0-9_]{3,60}", str(error)) else type(error).__name__)
        return out
    out.update(counts, status="ok")
    for name, value in sorted(found.items()):
        have = held(environ, name)
        if not have and name not in environ:                              # a step that never declares a name never receives it
            out["undeclared"] = out.get("undeclared", 0) + 1
        elif not have:
            environ[name] = value
            out["filled"].append(name)
        elif _bare(have) == _bare(value) if is_address(name) else have == value:
            out["matched"] += 1
        else:
            out["mismatched"].append(name)
    if copy:
        try:
            out["seeded"] = seed(client, wanted(environ, found, counts["invalid"]))
        except Exception as error:                                         # noqa: BLE001 - copying is best effort; the next run tries again
            out["seed_status"] = str(error) if re.fullmatch(r"[A-Z0-9_]{3,60}", str(error)) else type(error).__name__
    return out
