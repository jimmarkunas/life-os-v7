"""JFM-221 (Jim, Oct 29 2026): the Jim & Matt Dashboard V2, fed from Jira. Jira is the source of truth; Notion is a read-only output; this stage never writes to Jira.

Runs in the domains `jira` job after every hourly tick. Under the dashboard page (JM_DASHBOARD_PAGE_ID) it keeps three things, each found by its exact title:
  * Weekly Check-ins: one page per Wednesday ("Check-in — Wed Nov 4, 2026"), made on the first run at or after 7:00 AM CT that Wednesday, then frozen. A page left half
    written by a failed run (no closing mark) is archived and made again; a whole one is never touched again, so re-runs never duplicate.
  * Quarterly QBR Records: one synopsis page per quarter ("Q4 2026 — Quarterly Synopsis"): each person's Mary Morrissey vision from their Win tickets, must-wins, the QBR
    decisions and progress. Rewritten only when what it says changes (its closing mark carries a fingerprint).
  * Completed Archive: a database with one row per issue key (deduped), person, epic and quarter, appended as issues reach Done.
MegIBOW: Jim's week comes from the MegIBOW table V7 already writes (MEGIBOW_BLOCK_ID); Matt's from the manual table "Matt — MegIBOW weekly (manual)" (no mail access).
Nothing private reaches Notion (dash_scrub). Logs carry counts and fixed codes only. A failure pushes one phone alert (jm_dashboard_failed) and fails the step."""
import os
from datetime import datetime, timedelta

from lifeos.jira import dash_data as D, dash_render as R
from lifeos.jira.dash_scrub import scrub, terms as private_terms
from lifeos.platform import alerts
from lifeos.platform.jira import Jira, JiraError
from lifeos.platform.notion_client import Client, NotionError, rich_text

CHECKINS, QBRS, ARCHIVE, MATT_MEGIBOW = "Weekly Check-ins", "Quarterly QBR Records", "Completed Archive", "Matt — MegIBOW weekly (manual)"
GENERATE_AT_HOUR = 7                                      # Wednesday 7:00 AM CT
ARCHIVE_MAX = 150                                         # archive rows created per run (a first backfill catches up over a few runs)


class DashError(RuntimeError):
    """Fixed codes only."""


def _client(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _plain(rich):
    return "".join(r.get("plain_text") or (r.get("text") or {}).get("content") or "" for r in rich or [])


def children(client, block_id):
    out, cursor = [], None
    for _ in range(50):
        page = client.call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
        out += page.get("results") or []
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
    raise DashError("JM_DASH_TOO_MANY_BLOCKS")


def layout(client, root):
    """The dashboard's machine-owned children, by exact title -> {title: id}; a missing or doubled one is refused (never guessed)."""
    found = {}
    for b in children(client, root):
        name = (b.get("child_page") or {}).get("title") if b.get("type") == "child_page" else (b.get("child_database") or {}).get("title") if b.get("type") == "child_database" else None
        if name in (CHECKINS, QBRS, ARCHIVE, MATT_MEGIBOW):
            if name in found:
                raise DashError("JM_DASH_LAYOUT_AMBIGUOUS")
            found[name] = b["id"]
    if set(found) != {CHECKINS, QBRS, ARCHIVE, MATT_MEGIBOW}:
        raise DashError("JM_DASH_LAYOUT_MISSING")
    return found


def source_of(client, database_id):
    sources = client.call("GET", f"/databases/{database_id}").get("data_sources") or []
    if len(sources) != 1:
        raise DashError("JM_DASH_DATABASE_SHAPE")
    return sources[0]["id"]


def query_all(client, source):
    out, cursor = [], None
    for _ in range(100):
        page = client.query_data_source(source, {"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
        out += page.get("results") or []
        if not page.get("has_more"):
            return out
        cursor = page.get("next_cursor")
    raise DashError("JM_DASH_TOO_MANY_ROWS")


def append(client, parent, blocks):
    for start in range(0, len(blocks), 100):
        made = client.call_once("PATCH", f"/blocks/{parent}/children", {"children": blocks[start:start + 100]}).get("results") or []
        if len(made) != len(blocks[start:start + 100]):
            raise DashError("JM_DASH_APPEND_MISMATCH")


def child_pages(client, parent):
    return {(b.get("child_page") or {}).get("title"): b["id"] for b in children(client, parent) if b.get("type") == "child_page"}


def complete(client, page_id):
    last = children(client, page_id)[-1:] or [{}]
    return R.DONE_MARK in _plain(((last[0].get("paragraph") or {}).get("rich_text")))


def make_page(client, parent, title, blocks):
    page = client.call_once("POST", "/pages", {"parent": {"page_id": parent}, "properties": {"title": {"title": rich_text(title)}}, "children": blocks[:100]})
    append(client, page["id"], blocks[100:])
    return page["id"]


def wednesday(now):
    """The check-in this run owes: the latest Wednesday at 7:00 AM CT at or before now."""
    local = now.astimezone(D.CENTRAL)
    back = (local.weekday() - 2) % 7
    wed = (local - timedelta(days=back)).replace(hour=GENERATE_AT_HOUR, minute=0, second=0, microsecond=0)
    return wed if wed <= local else wed - timedelta(days=7)


def checkin_title(wed):
    return f"Check-in — Wed {wed:%b} {wed.day}, {wed.year}"


def _number(prop):
    return (prop or {}).get("number") if isinstance((prop or {}).get("number"), (int, float)) else None


def megibow_jim(client, block_id, week_start):
    """Jim's counts for the week from the MegIBOW table V7 writes (its header names each week's Monday, e.g. "Oct 19"); None when the week or table is not there."""
    if not block_id:
        return None
    tables = [b for b in children(client, block_id) if b.get("type") == "table"]
    if len(tables) != 1:
        return None
    rows = [[_plain(c) for c in (r.get("table_row") or {}).get("cells") or []] for r in children(client, tables[0]["id"])]
    if not rows:
        return None
    want = R.day(week_start)
    col = next((i for i, h in enumerate(rows[0]) if h.strip() == want or h.strip().startswith(want + " ")), None)
    if col is None:
        return None
    counts = {}
    for row in rows[1:]:
        if row and row[0].strip() in R.CATEGORIES and col < len(row):
            try:
                counts[row[0].strip()] = int(row[col].strip())
            except ValueError:
                return None
    return counts if set(counts) == set(R.CATEGORIES) else None


def megibow_matt(client, source, week_start):
    """Matt's counts from his manual table: the row whose Week Start is that Monday. None when he has not entered it."""
    rows = [r for r in query_all(client, source) if (((r.get("properties") or {}).get("Week Start") or {}).get("date") or {}).get("start", "")[:10] == week_start.isoformat()]
    if len(rows) != 1:
        return None
    props = rows[0].get("properties") or {}
    counts = {c: _number(props.get(c)) for c in R.CATEGORIES}
    return None if any(v is None for v in counts.values()) else {c: int(v) for c, v in counts.items()}


def build_people(jira, issues, now, label):
    by = {"Jim": [], "Matt": []}
    for i in issues:
        if i["person"] in by:
            by[i["person"]].append(i)
    people = {}
    for name, mine in by.items():
        must = sorted((i for i in mine if D.MUST_WIN in i["labels"] and (i["category"] != "done" or (i["changed"] and i["changed"] >= now - timedelta(days=7)))),
                      key=lambda i: i["key"])
        stuck = [i for i in mine if D.blocked(i) and i["category"] != "done"][:D.BLOCKED_MAX]
        people[name] = {"window": D.window(mine, now), "must": must, "blocked": [i for i in stuck if D.unexplained(jira, i)]}
    return people


def build_synopsis(jira, issues, now, label):
    q_start = now.date().replace(month=3 * (D.quarter_of(now.date()) - 1) + 1, day=1)
    wins_raw = {r.get("key"): (r.get("fields") or {}).get("description") for r in
                D.search(jira, f"project in ({','.join(D.PROJECTS)}) AND labels = {label} AND summary ~ \"Win\"", "summary,description")}
    people = {}
    for name in ("Jim", "Matt"):
        mine = [i for i in issues if i["person"] == name]
        wins = sorted((i for i in mine if i["key"] in wins_raw and "win" in i["summary"].lower() and label in i["labels"]), key=lambda i: i["key"])
        completed = {}
        for i in mine:
            if i["category"] == "done" and i["level"] != 1 and i["changed"] and i["changed"].date() >= q_start:
                completed[D.epic_name(i["epic"])] = completed.get(D.epic_name(i["epic"]), 0) + 1
        people[name] = {"wins": [(i, D.vision(wins_raw.get(i["key"]))) for i in wins],
                        "must": sorted((i for i in mine if D.MUST_WIN in i["labels"]), key=lambda i: i["key"]),
                        "decisions": sorted((i for i in mine if label in i["labels"] and i not in wins and D.MUST_WIN not in i["labels"] and i["level"] != 1), key=lambda i: i["key"]),
                        "completed": completed}
    return people


def write_checkin(client, folder, wed, blocks, live):
    title = checkin_title(wed)
    existing = child_pages(client, folder).get(title)
    if existing and complete(client, existing):
        return "kept"
    if not live:
        return "would_write"
    if existing:                                                                      # a half-written page from a failed run is set aside (renamed, never deleted) and made again
        client.call_once("PATCH", f"/pages/{existing}", {"properties": {"title": {"title": rich_text(title + " (incomplete)")}}})
    make_page(client, folder, title, blocks)
    return "written"


def write_synopsis(client, folder, title, blocks, mark, live):
    existing = child_pages(client, folder).get(title)
    if existing:
        old = children(client, existing)
        if old and mark in _plain(((old[-1].get("paragraph") or {}).get("rich_text"))):
            return "unchanged"
        if not live:
            return "would_update"
        append(client, existing, blocks)                                              # new content first: a failure here removes nothing
        for b in old:
            client.call_once("DELETE", f"/blocks/{b['id']}")
        return "updated"
    if not live:
        return "would_write"
    make_page(client, folder, title, blocks)
    return "written"


def archive(client, jira, database, site, terms, live):
    source = source_of(client, database)
    have = {_plain(((r.get("properties") or {}).get("Key") or {}).get("rich_text")) for r in query_all(client, source)}
    done = D.resolve_epics(jira, [D.normalize(r, site) for r in D.search(jira, f"project in ({','.join(D.PROJECTS)}) AND statusCategory = Done AND issuetype != Epic")])
    todo = sorted((i for i in done if i["key"] not in have and i["changed"]), key=lambda i: i["changed"])
    made = 0
    for i in todo[:ARCHIVE_MAX] if live else []:
        props = {"Name": {"title": rich_text(f"{i['key']} — {scrub(i['summary'], terms)[0]}")}, "Key": {"rich_text": rich_text(i["key"])},
                 "Person": {"select": {"name": i["person"]}}, "Epic": {"select": {"name": D.epic_name(i["epic"])[:90].replace(",", " ")}},
                 "Quarter": {"select": {"name": D.quarter_name(i["changed"].date())}}, "Completed": {"date": {"start": i["changed"].date().isoformat()}},
                 "Link": {"url": i["url"]}}
        client.call_once("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source}, "properties": props})
        made += 1
    return {"archived": made, "archive_pending": len(todo) - made, "archive_rows": len(have)}


def _site(environ):
    site = (environ.get("JIRA_SITE_URL") or environ.get("JIRA_BASE_URL") or "").strip().rstrip("/")
    if not site.startswith("https://"):
        raise DashError("JM_DASH_CONFIG_MISSING")
    return site


def build(limit, live, environ=os.environ, jira=None, client=None, now=None):
    root = (environ.get("JM_DASHBOARD_PAGE_ID") or "").strip().replace("-", "")
    if len(root) != 32:
        raise DashError("JM_DASH_CONFIG_MISSING")
    now = (now or datetime.now(D.CENTRAL)).astimezone(D.CENTRAL)
    jira = jira or Jira.from_env(environ)
    client = client or _client(environ)
    terms, site = private_terms(environ), _site(environ)
    found = layout(client, root)
    issues = D.read_all(jira, site, now)
    label = D.quarter_label(now.date())
    result = {"issues": len(issues)}

    wed = wednesday(now)
    week_start = (wed - timedelta(days=9)).date()                                     # the last whole Monday-Sunday week before that Wednesday
    if child_pages(client, found[CHECKINS]).get(checkin_title(wed)) and complete(client, child_pages(client, found[CHECKINS])[checkin_title(wed)]):
        result["checkin"] = "kept"
    else:
        people = build_people(jira, issues, now, label)
        megibow = {"Jim": megibow_jim(client, (environ.get("MEGIBOW_BLOCK_ID") or "").strip(), week_start),
                   "Matt": megibow_matt(client, source_of(client, found[MATT_MEGIBOW]), week_start)}
        blocks = R.checkin(people, now, week_start, megibow, terms)
        result["checkin"] = write_checkin(client, found[CHECKINS], wed, blocks, live)
        result.update({f"{n.lower()}_{k}": len(v) for n, p in people.items() for k, v in list(p["window"].items()) + [("must", p["must"]), ("blocked", p["blocked"])]})
        result["megibow_entered"] = sum(1 for v in megibow.values() if v is not None)

    blocks, mark = R.synopsis(D.quarter_name(now.date()), label, build_synopsis(jira, issues, now, label), now, terms)
    result["synopsis"] = write_synopsis(client, found[QBRS], f"{D.quarter_name(now.date())} — Quarterly Synopsis", blocks, mark, live)
    result.update(archive(client, jira, found[ARCHIVE], site, terms, live))
    result["notion_calls"] = client.calls
    return result


def run(limit, live, environ=os.environ, jira=None, client=None, now=None, push=alerts.push):
    """The stage. On any failure: one phone alert (fixed code only) while this week's check-in is still owed, or once a day at 9 AM CT otherwise; then the step fails."""
    try:
        return build(limit, live, environ, jira, client, now)
    except (DashError, JiraError, NotionError, KeyError, ValueError, TypeError) as error:
        code = str(error)[:60] if isinstance(error, (DashError, JiraError, NotionError)) else f"JM_DASH_{type(error).__name__.upper()}"
        local = (now or datetime.now(D.CENTRAL)).astimezone(D.CENTRAL)
        owed = local.weekday() == 2 and local.hour >= GENERATE_AT_HOUR
        if live and (owed or local.hour == 9):
            push("jm_dashboard_failed", "Jim & Matt dashboard failed", f"The Jira-to-Notion dashboard run failed ({code}). The Notion pages were not refreshed.")
        raise DashError(code if code.split("_")[0] in ("JM", "JIRA", "NOTION") else "JM_DASH_FAILED") from None
