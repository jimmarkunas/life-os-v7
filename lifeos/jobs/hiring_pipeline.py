"""INT-7.1A, the bounded progression handoff: which jobs already have a human hiring process behind them.

Source: the Notion Hiring Pipeline page (HIRING_PIPELINE_PAGE_ID), a page not a database: opportunity pages under Active / Retired Opportunities, one child page per interview round.
Output: for one job, a Handoff with its evidence: protected human progression is TRUE only on a deterministic match (company AND role), never on a company name alone, never inferred by a model.
Absence of a match proves nothing (a recruiter thread may exist only in Gmail), so this source can protect a pursuit but never unprotect one. Legacy Lifecycle / Liveness are never read.
LIFE-352 (REQ-5, LOS-88): `from_ledger` reads the pursuits of every Ledger row whose Stage is set; `merge` puts them first and keeps a page-tree opportunity only when no Ledger pursuit matches (company AND role).
Failure: read only; an incomplete Ledger read raises (the caller fails closed), never a partial list."""
# The Job Ledger row is the job record (LIFE-352); a row with Stage empty is still read from the page tree exactly as before.
from dataclasses import dataclass, field
import re

from lifeos.platform.names import LEGAL, norm, same_company, same_role, split_title          # noqa: F401 - shared with Interview OS
from lifeos.platform.notion_client import NotionError
from lifeos.platform.notion_pages import query

ACTIVE, RETIRED = "ACTIVE", "RETIRED"
INTERVIEW_ACTIVE, HIRING_ACTIVE = "INTERVIEW_ACTIVE", "HIRING_ACTIVE"
MAX_DEPTH, MAX_BLOCKS = 4, 2000
STAGE, DECISION, ACTIVE_STAGES = "Stage", "Hiring Decision", ("Applied", "Interviewing", "Offer")      # REQ-5 Ledger contract: an active pursuit is Stage in (Applied, Interviewing, Offer) and Hiring Decision empty
LEDGER_MAX_PAGES = 40


@dataclass
class Opportunity:
    page_id: str
    title: str
    section: str                  # ACTIVE | RETIRED
    rounds: int = 0               # interview child pages
    company: str = ""
    role: str = ""
    lead: bool = False            # E01b: a round-less page whose machine render line names a minted recruiter key
    replied: str = ""             # E05: the day Jim marked the lead's reply action done on the page ("I replied — mark it done"), else ""
    holds: tuple = ()             # LIFE-111: the hand-set hold lines (Paused, Waiting on employer, Active) Manage wrote in the page's fold, as written
    from_row: bool = False        # LIFE-352: read from a Ledger row (page_id is the row page id)
    tree_id: str = ""             # LIFE-352: for a Ledger pursuit, the matching page-tree page id (the Close and Reopen writers still move that page until LIFE-353), else ""


@dataclass
class Handoff:
    protected: bool
    progression_state: str | None = None
    interview_progression: bool = False
    evidence: list = field(default_factory=list)     # [{"type", "source_id"}]  (page ids only, never titles or text)
    confidence: str = "authoritative"


LEAD_LINE = re.compile(r"v7-render:1;job=recruiter-")
LEAD_ACTION = re.compile(r"v7-action:1;key=([0-9a-f]{16});text=Reply to the recruiter's message, or close this lead.*")   # lifeos.interview.template's line formats (contract-tested)
DONE_LINE = re.compile(r"v7-done:1;key=([0-9a-f]{16});on=(\d{4}-\d{2}-\d{2})")
HOLD_PREFIX = "v7-hold:1;"                    # lifeos.hiring.models.HOLD_LINE (contract-tested); parsed by the Hiring Pipeline stage, not here


def replied_on(lines):
    """The latest day the lead's reply action was marked done, else "": a done line counts only for that action's own key."""
    keys = {m.group(1) for m in map(LEAD_ACTION.fullmatch, lines) if m}
    return max((m.group(2) for m in map(DONE_LINE.fullmatch, lines) if m and m.group(1) in keys), default="")


def _title_of(block):
    kind = block.get("type")
    if kind == "child_page":
        return (block.get("child_page") or {}).get("title", "")
    parts = (block.get(kind) or {}).get("rich_text") or []
    return "".join(p.get("plain_text", "") for p in parts)


def _children(client, block_id, budget):
    out, cursor = [], None
    while budget[0] > 0:
        path = f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        data = client.call("GET", path)
        out += data.get("results") or []
        budget[0] -= 1
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


def _section(text, current):
    low = text.lower()
    return RETIRED if "retired" in low else ACTIVE if "active" in low else current


def read(client, root_id, rounds=True):
    """Opportunities under the Hiring Pipeline page. Raises NotionError when the page cannot be read (the caller fails closed). rounds=False skips the one
    children read per opportunity that counts interview pages (rounds stay 0): a caller that never uses the count (the S2 closure) saves a paced call each."""
    found, budget = [], [MAX_BLOCKS]

    def walk(block_id, section, depth):
        context = section
        for block in _children(client, block_id, budget):
            kind = block.get("type")
            text = _title_of(block)
            if kind.startswith("heading") or kind == "toggle":
                context = _section(text, context)
            if kind == "child_page":
                inner = _section(text, context)
                if inner != context or re.search(r"\b(active|retired) opportunities\b", text, re.I):
                    if depth < MAX_DEPTH:
                        walk(block["id"], inner, depth + 1)                 # a container page: Active / Retired Opportunities
                else:
                    opp = Opportunity(block["id"], text, context or ACTIVE)
                    opp.company, opp.role = split_title(text)
                    if rounds:
                        kids = _children(client, block["id"], budget)
                        opp.rounds = sum(1 for c in kids if c.get("type") == "child_page")
                        if kids and kids[0].get("type") == "toggle" and kids[0].get("has_children"):
                            try:
                                lines = [_title_of(c) for c in _children(client, kids[0]["id"], budget)]
                                if not opp.rounds:
                                    opp.lead, opp.replied = any(LEAD_LINE.match(t) for t in lines), replied_on(lines)
                                opp.holds = tuple(t for t in lines if t.startswith(HOLD_PREFIX))
                            except NotionError:
                                pass                                         # best effort: an unread fold is simply not a lead and not on hold (it stays listed as active), as before
                    found.append(opp)
            elif block.get("has_children") and depth < MAX_DEPTH and (kind in ("toggle", "column_list", "column") or kind.startswith("heading")):
                walk(block["id"], context, depth + 1)

    walk(root_id, None, 0)
    return found


def _text(prop, kind):
    return "".join((p.get("plain_text") or "") for p in (prop or {}).get(kind) or []).strip()


def _select(prop):
    return ((prop or {}).get("select") or {}).get("name")


def _fold_lines(client, kids, budget):
    """The lines of the row page's "LIFE OS" fold (the first top-level toggle that holds it), else []. An unreadable fold is simply no lines, as in `read`."""
    fold = next((c for c in kids if c.get("type") == "toggle" and c.get("has_children") and "LIFE OS" in _title_of(c)), None)
    if not fold:
        return []
    try:
        return [_title_of(c) for c in _children(client, fold["id"], budget)]
    except NotionError:
        return []


def from_ledger(client, rounds=True):
    """LIFE-352: the pursuits of every Job Ledger row whose Stage is set, as Opportunity objects (page_id = the row page id). Section: ACTIVE when Stage is Applied, Interviewing or
    Offer and Hiring Decision is empty, else RETIRED. rounds = the child pages of the row; lead, replied and holds come from the row's fold lines with the same helpers as `read`.
    Raises NotionError when the Ledger page set cannot be proven complete (the caller fails closed)."""
    rows = [row for page in query(client, None, {"page_size": 100, "filter": {"property": STAGE, "select": {"is_not_empty": True}}}, NotionError, incomplete="HIRING_LEDGER_INCOMPLETE", limit=LEDGER_MAX_PAGES) for row in page["results"]]
    out, budget = [], [MAX_BLOCKS]
    for row in rows:
        props = row.get("properties") or {}
        company, role = _text(props.get("Company"), "rich_text"), _text(props.get("Job"), "title")
        stage, decision = _select(props.get(STAGE)), _select(props.get(DECISION))
        if not (company and role and stage and row.get("id")):
            continue                                                         # an unnamed row is not a pursuit (read_applied drops it the same way)
        active = stage in ACTIVE_STAGES and not decision
        opp = Opportunity(row["id"], f"{company} — {role}", ACTIVE if active else RETIRED, company=company, role=role, from_row=True)
        if rounds:
            kids = _children(client, row["id"], budget)
            if budget[0] <= 0:
                raise NotionError("HIRING_LEDGER_INCOMPLETE")
            opp.rounds = sum(1 for c in kids if c.get("type") == "child_page")
            lines = _fold_lines(client, kids, budget)
            if not opp.rounds:
                opp.lead, opp.replied = any(LEAD_LINE.match(t) for t in lines), replied_on(lines)
            opp.holds = tuple(t for t in lines if t.startswith(HOLD_PREFIX))
        out.append(opp)
    return out


def merge(ledger_opps, tree_opps):
    """Ledger pursuits first; a page-tree opportunity is kept only when no Ledger pursuit matches it on company AND role (the fallback for a row with Stage empty).
    Each Ledger pursuit remembers its first matching tree page (tree_id) so the writers that still move tree pages keep working."""
    for opp in ledger_opps:
        match = next((t for t in tree_opps if same_company(t.company, opp.company) and same_role(t.role, opp.role)), None)
        opp.tree_id = match.page_id if match else ""
    kept = [t for t in tree_opps if not any(same_company(t.company, o.company) and same_role(t.role, o.role) for o in ledger_opps)]
    return list(ledger_opps) + kept


def handoff_for(company, title, opportunities):
    """Handoff for one job. protected = an ACTIVE opportunity matches on company AND role."""
    matches = [o for o in opportunities if o.section == ACTIVE and same_company(o.company, company) and same_role(o.role, title)]
    if not matches:
        return Handoff(False)
    interview = any(o.rounds for o in matches)
    return Handoff(True, INTERVIEW_ACTIVE if interview else HIRING_ACTIVE, interview,
                   [{"type": "hiring_pipeline_opportunity", "source_id": o.page_id} for o in matches])


def snapshot(client, root_id, rounds=True, ledger=None):
    """-> (opportunities, status) with status 'ok' | 'unreadable'. Never raises. `ledger` (LIFE-352): a Job Ledger client; when given, Ledger pursuits (Stage set) come first and the
    page tree fills in only what the Ledger does not hold. Either read failing is 'unreadable' (never a partial list)."""
    try:
        tree = read(client, root_id, rounds)
        return (merge(from_ledger(ledger, rounds), tree) if ledger is not None else tree), "ok"
    except NotionError:
        return [], "unreadable"
