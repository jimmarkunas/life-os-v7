"""NET-2.2: how many admitted jobs have network leads, and how many each? Read-only, counts only, writes nothing, creates no table, prints no name, company or title.

Targets (this slice): published jobs whose stored Fit admission is ADMIT. Applied jobs and interviews are later targets: they live on human-owned Notion pages, which a surface decision
(NET-2.3) must settle first. The roster is the whole stored network. Matching is lifeos.network.match; this stage only reads the two stores and counts."""
from datetime import date, datetime, timezone

from lifeos.jobs import store as jobs_store
from lifeos.network import match, parse
from lifeos.network.errors import NetworkError
from lifeos.platform import db


def _today():
    return datetime.now(timezone.utc).date()


def _read(connection):
    with connection.cursor() as cursor:
        try:
            cursor.execute("SELECT s.person_id, s.company_key, s.title, s.position_state, s.last_verified FROM v7_network_positions s JOIN v7_network_people p ON p.id = s.person_id WHERE p.status='ACTIVE'")
            rows = [(pid, key, title, state, verified if isinstance(verified, date) else date.fromisoformat(str(verified)[:10])) for pid, key, title, state, verified in cursor.fetchall()]
            cursor.execute("SELECT j.id, j.company, j.title FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id WHERE j.status='PUBLISHED' AND j.notion_page_id IS NOT NULL AND f.admission='ADMIT'")
            jobs = cursor.fetchall()
        except Exception:                                              # noqa: BLE001 - a driver message may carry names; only the fixed code leaves
            raise NetworkError("NETWORK_MATCH_READ_FAILED") from None
    return rows, jobs


def count(rows, jobs, today):
    if not rows:
        raise NetworkError("NETWORK_NO_ROSTER")                        # an empty roster is a failed read, never "no leads"
    index = match.build_index(rows)
    out = {"roster_positions": len(rows), "roster_companies": len(index), "jobs_considered": len(jobs), "jobs_with_leads": 0, "jobs_without_leads": 0, "jobs_without_company_key": 0,
           "leads_total": 0, "leads_per_job": {str(n): 0 for n in range(match.MAX_LEADS + 1)}, "by_tier": {t: 0 for t in match.TIERS}, "by_freshness": {"FRESH": 0, "AGING": 0, "STALE": 0},
           "jobs_top_lead_stale": 0, "jobs_capped": 0}
    for _, company, title in jobs:
        key = parse.company_key(company)
        if not key:
            out["jobs_without_company_key"] += 1
            continue
        found = match.leads_for(key, title or "", index, today)
        out["leads_per_job"][str(len(found))] += 1
        if not found:
            out["jobs_without_leads"] += 1
            continue
        out["jobs_with_leads"] += 1
        out["leads_total"] += len(found)
        out["jobs_capped"] += len(found) == match.MAX_LEADS
        out["jobs_top_lead_stale"] += found[0]["freshness"] == "STALE"
        for lead in found:
            out["by_tier"][lead["tier"]] += 1
            out["by_freshness"][lead["freshness"]] += 1
    out["live"] = False                                                 # matching only: nothing is ever written
    return out


def run(limit, live, connection=None, today=None):
    today = today or _today()
    if connection is not None:
        return count(*_read(connection), today)
    with db.connect() as opened:
        return count(*_read(opened), today)
