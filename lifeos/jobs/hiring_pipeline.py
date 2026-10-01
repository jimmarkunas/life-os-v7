"""INT-7.1A, the bounded progression handoff: which jobs already have a human hiring process behind them.

Source: the Notion Hiring Pipeline page (HIRING_PIPELINE_PAGE_ID). It is a page, not a database: Active Opportunities and Retired Opportunities
hold one opportunity page per pursuit ("Company - Role"), and an opportunity page holds one child page per interview round. This module
reads that tree and answers the retention question for one job: does protected human progression exist? The answer is a Handoff with its
evidence, TRUE only on a deterministic match (company AND role), never on a company name alone, and never inferred by a model.
Absence of a match proves nothing (a recruiter thread may exist only in Gmail), so this source can protect a pursuit but can never unprotect one.
Legacy Lifecycle / Liveness are never read.
"""
from dataclasses import dataclass, field
import re

from lifeos.platform.names import LEGAL, norm, same_company, same_role, split_title          # noqa: F401 - shared with Interview OS
from lifeos.platform.notion_client import NotionError

ACTIVE, RETIRED = "ACTIVE", "RETIRED"
INTERVIEW_ACTIVE, HIRING_ACTIVE = "INTERVIEW_ACTIVE", "HIRING_ACTIVE"
MAX_DEPTH, MAX_BLOCKS = 4, 2000


@dataclass
class Opportunity:
    page_id: str
    title: str
    section: str                  # ACTIVE | RETIRED
    rounds: int = 0               # interview child pages
    company: str = ""
    role: str = ""


@dataclass
class Handoff:
    protected: bool
    progression_state: str | None = None
    interview_progression: bool = False
    evidence: list = field(default_factory=list)     # [{"type", "source_id"}]  (page ids only, never titles or text)
    confidence: str = "authoritative"


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


def read(client, root_id):
    """Opportunities under the Hiring Pipeline page. Raises NotionError when the page cannot be read (the caller fails closed)."""
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
                    opp.rounds = sum(1 for c in _children(client, block["id"], budget) if c.get("type") == "child_page")
                    found.append(opp)
            elif block.get("has_children") and depth < MAX_DEPTH and (kind in ("toggle", "column_list", "column") or kind.startswith("heading")):
                walk(block["id"], context, depth + 1)

    walk(root_id, None, 0)
    return found


def handoff_for(company, title, opportunities):
    """Handoff for one job. protected = an ACTIVE opportunity matches on company AND role."""
    matches = [o for o in opportunities if o.section == ACTIVE and same_company(o.company, company) and same_role(o.role, title)]
    if not matches:
        return Handoff(False)
    interview = any(o.rounds for o in matches)
    return Handoff(True, INTERVIEW_ACTIVE if interview else HIRING_ACTIVE, interview,
                   [{"type": "hiring_pipeline_opportunity", "source_id": o.page_id} for o in matches])


def snapshot(client, root_id):
    """-> (opportunities, status) with status 'ok' | 'unreadable'. Never raises."""
    try:
        return read(client, root_id), "ok"
    except NotionError:
        return [], "unreadable"
