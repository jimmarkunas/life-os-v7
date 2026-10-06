"""NET-2.3 (D160): surface network leads as one marker-owned "Network Leads (n)" block on the Job Ledger page of each target job. Dry unless live; counts only.

Targets: published jobs whose stored Fit admission is ADMIT, plus applied opportunities and upcoming interviews (from the accepted Hiring Pipeline snapshot) that resolve to exactly one
published Ledger page by the pipeline's own company-and-role rule. The human-owned Hiring Pipeline and Interview pages are never written; a pursuit target with no safe Ledger page
writes nothing and is counted as having no safe surface. REVIEW jobs get no block unless they are an active pursuit.

Per page, in this order: read the Ledger page and prove it is the canonical page for the job (data source, not archived, Stable Job Key equals the job's dedupe key, the v7-jd description
marker still opens the body); read Jim's Dismiss and Same company ticks from the owned block and persist them (live) before anything is replaced; recompute the leads (dismissals and learned
aliases applied, five at most); then create, replace, leave or REMOVE the owned block (no leads removes an existing block, never an empty placeholder) and read it back, proving nothing
outside it changed. Any failed read, guard, write or read-back is counted and the run ends DEGRADED (NETWORK_SURFACE_DEGRADED): it is never turned into zero leads or a silent success.
The tables v7_network_dismissals and v7_network_aliases are created only by a live run. A page budget (`limit`) bounds one run; pages with leads come first and the rest rotate daily."""
import hashlib
import json
import os
from datetime import date, datetime, timezone

from lifeos.hiring import models as hiring_models
from lifeos.jobs import ledger
from lifeos.network import match, store, surface
from lifeos.network.errors import NetworkError
from lifeos.platform import db
from lifeos.platform.notion_client import Client, NotionError
from lifeos.sources import network_leads

DEFAULT_PAGES = 300


def _read(connection, now):
    with connection.cursor() as cursor:
        try:
            cursor.execute("SELECT s.person_id, s.company_key, s.title, s.position_state, s.last_verified, s.id, s.company_name, p.display_name, p.url_key FROM v7_network_positions s "
                           "JOIN v7_network_people p ON p.id = s.person_id WHERE p.status='ACTIVE'")
            roster = cursor.fetchall()
            cursor.execute("SELECT j.dedupe_key, j.notion_page_id, j.company, j.title, f.admission FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id = j.id "
                           "WHERE j.status='PUBLISHED' AND j.notion_page_id IS NOT NULL")
            jobs = cursor.fetchall()
        except Exception:                                              # noqa: BLE001 - a driver message may carry names; only the fixed code leaves
            raise NetworkError("NETWORK_MATCH_READ_FAILED") from None
    try:
        pipeline = network_leads.hiring_snapshot.load(connection)
    except Exception:                                                  # noqa: BLE001
        pipeline = None
    return roster, jobs, pipeline


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def resolve_targets(jobs, pipeline, now):
    """-> (surfaced {job_key: (page_id, company, title, classes)}, others [(job_key, page_id, company, title)], counts). A pursuit target resolves only to exactly one published page."""
    pursuit, status = network_leads.targets([], pipeline, now)
    surfaced, counts = {}, {"pursuit_source": status, "targets_admitted": 0, "targets_pursuit_resolved": 0, "applied_no_safe_surface": 0, "interview_no_safe_surface": 0, "pursuit_ambiguous": 0}
    for key, page, company, title, admission in jobs:
        if admission == "ADMIT":
            surfaced[key] = (page, company or "", title or "", {network_leads.ADMITTED_JOB})
            counts["targets_admitted"] += 1
    for kind, company, role in pursuit:
        found = [(k, p, c, t) for k, p, c, t, _ in jobs if hiring_models.same(company, role, c or "", t or "")]
        if len(found) == 1:
            key, page, c, t = found[0]
            if key not in surfaced:
                counts["targets_pursuit_resolved"] += 1
                surfaced[key] = (page, c or "", t or "", set())
            surfaced[key][3].add(kind)
        elif len(found) > 1:
            counts["pursuit_ambiguous"] += 1
            counts[kind + "_no_safe_surface"] += 1
        else:
            counts[kind + "_no_safe_surface"] += 1
    others = [(k, p, c or "", t or "") for k, p, c, t, _ in jobs if k not in surfaced]
    return surfaced, others, counts


def _display(lead, details, today):
    pos = details.get(lead.get("position_id"))
    if not pos:
        return None
    name, url_key, company_name, title, verified = pos
    out = dict(lead, name=name, company=company_name, title=title, verified=verified, url_key=url_key)
    out["evidence_hash"] = surface.evidence_hash(lead, lead["company_key"], title, verified)
    return out


def _leads(company, title, index, details, today, aliases, dismissed, key, honour_dismissals=True):
    ckey = network_leads.parse.company_key(company)
    found = match.leads_for(ckey, title, index, today, aliases, limit=50) if ckey else []
    shown = [d for d in (_display(l, details, today) for l in found) if d]
    if honour_dismissals:
        shown = [d for d in shown if (key, d["person_id"], d["evidence_hash"]) not in dismissed]
    return shown[:match.MAX_LEADS], ckey


def _decisions(owned, full, job_key, ckey):
    """Jim's ticks that match exactly the evidence that was shown -> (dismissals, aliases, stale count)."""
    dismissals, aliases, stale = [], [], 0
    shown = {(d["person_id"], d["evidence_hash"]): d for d in full}
    for kind, person_id, evidence, checked in (owned["ticks"] if owned else []):
        if not checked:
            continue
        lead = shown.get((person_id, evidence))
        if lead is None:
            stale += 1                                                 # the evidence changed since the block was written: a tick never carries over to new evidence
        elif kind == "Dismiss":
            dismissals.append((job_key, person_id, evidence))
        elif lead["tier"] == match.POSSIBLE:
            aliases.append((lead["company_key"], ckey))
    return dismissals, aliases, stale


def _order(surfaced, others, today):
    first = sorted(surfaced)
    rest = sorted(others, key=lambda o: hashlib.sha256(f"{o[0]}{today.isoformat()}".encode()).hexdigest())
    return first, rest


def run(limit, live, environ=os.environ, connection=None, client=None, now=None, today=None):
    now = (now or datetime.now(timezone.utc))
    today = today or now.date()
    if connection is not None:
        return _run(limit, live, environ, connection, client, now, today)
    with db.connect() as opened:
        return _run(limit, live, environ, opened, client, now, today)


def _run(limit, live, environ, connection, client, now, today):
    budget = limit if limit and limit > DEFAULT_PAGES else DEFAULT_PAGES
    roster, jobs, pipeline = _read(connection, now)
    if not roster:
        raise NetworkError("NETWORK_NO_ROSTER")
    index = match.build_index([(pid, ckey, title, state, _day(verified), posid) for pid, ckey, title, state, verified, posid, *_ in roster])
    details = {posid: (name, url, cname, title, _day(verified)) for pid, ckey, title, state, verified, posid, cname, name, url in roster}
    surfaced, others, counts = resolve_targets(jobs, pipeline, now)
    counts.update({"live": bool(live), "roster_positions": len(roster), "published_pages": len(jobs), "pages_read": 0, "pages_deferred": 0, "with_leads": 0, "blocks": {}, "ticks_dismiss": 0,
                   "ticks_same_company": 0, "ticks_stale": 0, "guard_failed": {}, "failed": 0, "degraded": False, "pages_unread": 0})
    if counts["pursuit_source"] == "unavailable":
        counts["degraded"] = True                                       # the applied and interview triggers could not be read: reported, never a silent zero
    if live:
        store.ensure_surface_schema(connection)
    dismissed, aliases, counts["tables_present"] = store.load_decisions(connection)
    notion = client
    if notion is None:
        try:
            notion = Client(environ)
        except NotionError:
            notion = None
    if notion is None:
        if live:
            raise NetworkError("NETWORK_SURFACE_CONFIG_MISSING")
        counts["pages_unread"] = len(surfaced)                          # a dry run without Notion access still counts the leads it would show
        for key, (page, company, title, classes) in surfaced.items():
            shown, _ = _leads(company, title, index, details, today, aliases, dismissed, key)
            counts["with_leads"] += bool(shown)
        return _finish(counts)
    if ledger.verify(notion) != ledger.OK:
        counts["degraded"] = True
        counts["guard_failed"]["ledger_target"] = counts["guard_failed"].get("ledger_target", 0) + 1
        return _finish(counts)
    first, rest = _order(surfaced, others, today)
    queue = [(key,) + surfaced[key][:3] + (True,) for key in first] + [o + (False,) for o in rest]
    for key, page, company, title, target in queue[:budget]:
        try:
            _page(notion, connection, key, page, company, title, target, index, details, today, aliases, dismissed, live, counts)
        except (NotionError, NetworkError) as error:
            counts["failed"] += 1
            counts["degraded"] = True
            code = str(error)[:48]
            counts["guard_failed"][code] = counts["guard_failed"].get(code, 0) + 1
    counts["pages_deferred"] = max(len(queue) - budget, 0)
    return _finish(counts)


def _page(notion, connection, key, page_id, company, title, target, index, details, today, aliases, dismissed, live, counts):
    page = notion.call("GET", f"/pages/{page_id}")
    top, owned = surface.read_page(notion, page_id)
    counts["pages_read"] += 1
    why = surface.guard(page, top, key, notion.source)
    if why:
        counts["degraded"] = True
        counts["guard_failed"][why] = counts["guard_failed"].get(why, 0) + 1
        return
    if not target:                                                      # not an admitted job or active pursuit: it shows nothing, so a block left from before is removed
        action = surface.apply(notion, page_id, top, owned, [], live)
        counts["blocks"][action] = counts["blocks"].get(action, 0) + 1
        return
    full, ckey = _leads(company, title, index, details, today, aliases, dismissed, key, honour_dismissals=False)
    new_dismissals, new_aliases, stale = _decisions(owned, full, key, ckey)
    counts["ticks_dismiss"] += len(new_dismissals)
    counts["ticks_same_company"] += len(new_aliases)
    counts["ticks_stale"] += stale
    local_dismissed, local_aliases = set(dismissed) | set(new_dismissals), dict(aliases, **dict(new_aliases))
    if live and (new_dismissals or new_aliases):
        store.save_decisions(connection, new_dismissals, new_aliases)    # Jim's decisions are persisted and read back BEFORE the block is replaced
        dismissed.update(new_dismissals)
        aliases.update(dict(new_aliases))
    shown, _ = _leads(company, title, index, details, today, local_aliases, local_dismissed, key)
    counts["with_leads"] += bool(shown)
    action = surface.apply(notion, page_id, top, owned, shown, live)
    counts["blocks"][action] = counts["blocks"].get(action, 0) + 1


def _finish(counts):
    if counts["degraded"]:
        print("network-surface:", json.dumps(counts, sort_keys=True))     # counts only: a degraded run still says what it saw
        raise NetworkError("NETWORK_SURFACE_DEGRADED")
    return counts
