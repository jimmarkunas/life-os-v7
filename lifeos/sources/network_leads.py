"""NET-2.2: how many job and pursuit targets have network leads, and how many each? Read-only, counts only, writes nothing, creates no table, prints no name, company or title.

Three trigger classes (the product canon): an admitted job (published, stored Fit admission ADMIT), an applied or active-pursuit opportunity, and a scheduled or upcoming interview. The
last two come from the accepted Hiring Pipeline snapshot (`lifeos.hiring.snapshot`, the private derivative row the pipeline already writes from the Job Ledger, the Hiring Pipeline pages
and the Calendar): no Notion or Calendar read, no new source. The roster is the whole stored network. Matching is lifeos.network.match; this stage only reads and counts. A target that
belongs to more than one class is counted in each class and once in the distinct total (by company key and role words)."""
from datetime import date, datetime, timezone

from lifeos.hiring import snapshot as hiring_snapshot
from lifeos.network import match, parse
from lifeos.network.errors import NetworkError
from lifeos.platform import db, names

ADMITTED_JOB, APPLIED, INTERVIEW = "admitted_job", "applied", "interview"
CLASSES = (ADMITTED_JOB, APPLIED, INTERVIEW)
INTERVIEW_KINDS = ("hiring_manager", "interview", "recruiter_screen", "assessment")


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
    try:
        pipeline = hiring_snapshot.load(connection)
    except Exception:                                                  # noqa: BLE001 - the pursuit source is reported unavailable; the job class still counts
        pipeline = None
    return rows, jobs, pipeline


def targets(jobs, pipeline, now):
    """-> ([(class, company, title)], source status). Pipeline rows are structure from the accepted snapshot; an interview is a timed upcoming event of an interview kind."""
    out = [(ADMITTED_JOB, company, title or "") for _, company, title in jobs]
    if not pipeline or not isinstance(pipeline.get("rows"), list):
        return out, "unavailable"
    for row in pipeline["rows"]:
        company, role = str(row.get("company") or ""), str(row.get("role") or "")
        if not company:
            continue
        out.append((APPLIED, company, role))                           # every snapshot row is an applied or active-pursuit opportunity by construction
        start, kind = row.get("event_start"), row.get("event_kind")
        if kind in INTERVIEW_KINDS and start:
            try:
                upcoming = datetime.fromisoformat(str(start)) > now
            except ValueError:
                upcoming = False
            if upcoming:
                out.append((INTERVIEW, company, role))
    return out, "carried" if pipeline.get("reasons") else "ok"


def _blank():
    return {"considered": 0, "with_leads": 0, "without_leads": 0, "without_company_key": 0, "leads_total": 0, "leads_per_target": {str(n): 0 for n in range(match.MAX_LEADS + 1)},
            "by_tier": {t: 0 for t in match.TIERS}, "by_freshness": {"FRESH": 0, "AGING": 0, "STALE": 0}, "top_lead_stale": 0, "capped": 0}


def count(rows, jobs, pipeline, today, now=None):
    if not rows:
        raise NetworkError("NETWORK_NO_ROSTER")                        # an empty roster is a failed read, never "no leads"
    now = now or datetime.now(timezone.utc)
    index = match.build_index(rows)
    found, status = targets(jobs, pipeline, now)
    out = {"roster_positions": len(rows), "roster_companies": len(index), "pursuit_source": status, "by_class": {c: _blank() for c in CLASSES}, "distinct_targets": 0, "distinct_with_leads": 0, "live": False}
    seen = {}
    for kind, company, title in found:
        stats = out["by_class"][kind]
        stats["considered"] += 1
        key = parse.company_key(company)
        if not key:
            stats["without_company_key"] += 1
            continue
        leads = match.leads_for(key, title, index, today)
        stats["leads_per_target"][str(len(leads))] += 1
        if not leads:
            stats["without_leads"] += 1
        else:
            stats["with_leads"] += 1
            stats["leads_total"] += len(leads)
            stats["capped"] += len(leads) == match.MAX_LEADS
            stats["top_lead_stale"] += leads[0]["freshness"] == "STALE"
            for lead in leads:
                stats["by_tier"][lead["tier"]] += 1
                stats["by_freshness"][lead["freshness"]] += 1
        seen[(key, " ".join(sorted(set(names.norm(title).split()))))] = bool(leads)
    out["distinct_targets"], out["distinct_with_leads"] = len(seen), sum(seen.values())
    return out


def run(limit, live, connection=None, today=None, now=None):
    today = today or _today()
    if connection is not None:
        return count(*_read(connection), today, now)
    with db.connect() as opened:
        return count(*_read(opened), today, now)
