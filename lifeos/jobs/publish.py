"""Publish READY jobs to the Notion Job Ledger (official API, <= 2.5 req/s, counts-only logs).

The store is the dedupe brain: each job is created once and its page id stored. The ONE cold-start read of the Ledger
is remembered in v7_ledger_urls until SEED_MARKER changes. With V7_FIT_GATE=true only jobs the lane policy admits (or
sends to Review) publish. The description goes in the page BODY (contract v7.jd.1, docs/PLAN.md).
"""
import os

from lifeos.jobs import classify, meta, lanes, ledger, store
from lifeos.platform import ledger_body as body
from lifeos.jobs.identity import aggregator_twin, norm, url_key, vacancy_id
from lifeos.platform import ledger_claim, limits
from lifeos.platform.notion_client import Client, NotionError, rich_text

SECTIONS = (("summary", "Summary"), ("responsibilities", "Responsibilities"), ("requirements", "Requirements"),
            ("qualifications", "Qualifications"))


_now = store.now


def _block(kind, content):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(content)}}


def same_opening(cursor, job_id, company, title, url=None, location=None):
    """The id of a PUBLISHED job with the same company and title (three words or more), else None.

    One opening posted under several locations or links is one page, but only when both links name the same ATS
    vacancy (identity.vacancy_id) or neither names one. Exception: an aggregator link with no id whose place is the
    place the ATS vacancy names. A title under three words merges only when both links name the same ATS vacancy id.
    """
    if not norm(company):
        return None
    short, mine = len(norm(title).split()) < 3, vacancy_id(url)
    if short and mine is None:
        return None
    cursor.execute("SELECT id, final_apply_url, location_text FROM v7_jobs WHERE status='PUBLISHED' AND id<>%s AND LOWER(TRIM(company))=LOWER(TRIM(%s))"
                   " AND LOWER(TRIM(title))=LOWER(TRIM(%s)) ORDER BY id", (job_id, company, title))
    rows = cursor.fetchall()
    if short:
        return next((row[0] for row in rows if vacancy_id(row[1]) == mine), None)
    return next((row[0] for row in rows if vacancy_id(row[1]) == mine), None) or next(
        (row[0] for row in rows if aggregator_twin(url, location, row[1], row[2], AGGREGATOR)), None)


AGGREGATOR = lambda url: classify.apply_kind(url) == "aggregator"  # noqa: E731 - the one host rule, shared with the audit


def body_blocks(key, description):
    """Contract v7.jd.1 (flat body). Used by diagnostics and synthetic proofs; real pages are written by
    lifeos.jobs.body.build.
    """
    return body.legacy_blocks(key, description)


def properties(row):
    """row: dict with title, company, url, provider (or source), lane, key, first_seen (date), posted (date|None)."""
    provider = row.get("provider") or row["source"]
    props = {
        "Job": {"title": rich_text(row["title"] or "Untitled")},
        "Role": {"rich_text": rich_text(row["title"] or "")},
        "Company": {"rich_text": rich_text(row["company"] or "")},
        "Apply URL": {"url": row["url"]},
        "Source Provider": {"rich_text": rich_text(provider)},
        "Source Types": {"multi_select": [{"name": provider}] + [{"name": flag} for kind, flag in (("easy_apply", "Easy Apply"), ("aggregator", "Aggregator Link")) if row.get("apply_kind") == kind]},
        "Stable Job Key": {"rich_text": rich_text(row["key"])},
        "First Surfaced": {"date": {"start": row["first_seen"].isoformat()}},
        "Freshness Status": {"select": {"name": "Fresh"}},
        "Liveness": {"select": {"name": "Live"}},
        "Visible Lane": {"select": {"name": meta.VISIBLE_LANE_LABEL.get(lanes.lane_for(row["lane"]), lanes.lane_for(row["lane"]))}},
        "Eligible Lanes": {"multi_select": [{"name": n} for n in (row.get("eligible") or [lanes.lane_for(row["lane"])])]},
        "Admission Status": {"select": {"name": lanes.ADMISSION_LABEL.get(row.get("admission"), "Passed / Review")}},
    }
    if row.get("admission") == lanes.REVIEW and row.get("admission_reason"):
        props["Review Reason"] = {"rich_text": rich_text(row["admission_reason"])}
    if row.get("work_mode") in ("remote", "hybrid", "onsite", "unknown"):
        props["Work Mode"] = {"select": {"name": row["work_mode"].capitalize()}}
    props.update(meta.properties(row.get("location"), row.get("salary"), row.get("text"), row.get("route"),
                                 ",".join(row.get("eligible") or []), lanes.lane_for(row["lane"])))
    if row.get("posted"):
        props["Posting Date"] = {"date": {"start": row["posted"].isoformat()}}
    if row.get("fit") is not None:
        props["LIFE OS Fit"] = {"number": row["fit"]}
        props["Why It Fits"] = {"rich_text": rich_text(row.get("fit_line") or "")}
        props["Fit Authority"] = {"select": {"name": "Authoritative"}}
    return props


def existing_page(client, key):
    """The Ledger page that already holds this Stable Job Key, or None.

    A create Notion accepted but the database never recorded is adopted next run, never repeated. Two matches or an
    unreadable answer is refused (a fixed NotionError).
    """
    found = client.call("POST", f"/data_sources/{client.source}/query", {"filter": {"property": "Stable Job Key", "rich_text": {"equals": key}}, "page_size": 2}).get("results")
    if found is None or not isinstance(found, list) or len(found) > 1 or any(not isinstance(page, dict) or not page.get("id") for page in found):
        raise NotionError("ledger_key_ambiguous" if isinstance(found, list) and len(found) > 1 else "ledger_lookup_unreadable")
    return found[0]["id"] if found else None


def adopted(pages):
    """existing_page()'s answer from a batched lookup: the one page holding the key, None for none, a refusal for two.
    """
    if len(pages) > 1:
        raise NotionError("ledger_key_ambiguous")
    return pages[0]["id"] if pages else None


def _listed(client, keys, counts):
    """The Ledger pages for every key this run may publish: one query per readback.KEY_CHUNK keys.

    An unreadable answer is never read as `no page`: publish falls back to the per-key lookup (None).
    """
    from lifeos.jobs import readback                                                  # noqa: PLC0415
    keys = list(dict.fromkeys(k for k in keys if k))
    if not keys:
        return {}
    try:
        return readback.ledger_pages(client, keys)
    except NotionError:
        counts["key_lookup"] = "per_key"
        return None


def ledger_urls(client):
    """Every Apply URL already in the Ledger (paged, 100 per request)."""
    urls, cursor = set(), None
    while True:
        body = {"page_size": limits.NOTION_PAGE_SIZE}
        if cursor:
            body["start_cursor"] = cursor
        data = client.call("POST", f"/data_sources/{client.source}/query", body)
        for page in data.get("results", []):
            value = ((page.get("properties") or {}).get("Apply URL") or {}).get("url")
            if value:
                urls.add(url_key(value))
        if not data.get("has_more"):
            return urls
        cursor = data.get("next_cursor")


SEED_MARKER = "0" * 63 + "3"  # the Ledger read is repeated once per marker; change it to force a fresh read


def _seeded(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM v7_ledger_urls WHERE source='seed' AND url_hash=%s", (SEED_MARKER,))
        return cursor.fetchone()[0] > 0


def _store_seed(connection, urls):
    """Replace the remembered Ledger URLs with the ones in the Ledger now (plus a marker row so the read is not repeated).

    Then release the jobs the old list held back: a role a purge archived is no longer a duplicate.
    """
    now = _now()
    rows = [("0" * 64, "seed", now), (SEED_MARKER, "seed", now)] + [(key, "seed", now) for key in urls]
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM v7_ledger_urls WHERE source='seed'")
        for start in range(0, len(rows), 500):
            cursor.executemany("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,%s,%s)",
                               rows[start:start + 500])
    return release_in_ledger(connection)


def release_in_ledger(connection):
    """Jobs set aside as `in_ledger` whose URL is no longer in the remembered Ledger URLs go back to READY. -> count.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, final_apply_url FROM v7_jobs WHERE status='DUPLICATE' AND unresolved_reason='in_ledger' AND notion_page_id IS NULL AND final_apply_url IS NOT NULL")
        held = cursor.fetchall()
        cursor.execute("SELECT url_hash FROM v7_ledger_urls")
        known = {row[0] for row in cursor.fetchall()}
        free = [row[0] for row in held if url_key(row[1]) not in known]
        for job_id in free:
            cursor.execute("UPDATE v7_jobs SET status='READY', unresolved_reason='released_ledger', updated_at=%s WHERE id=%s AND status='DUPLICATE'", (_now(), job_id))
    return len(free)


# one place in four goes first to the strongest jobs not from an employer-board scan (mail, alerts)
MAIL_SHARE = 4
FAIR_SHARE = 4  # one slot in four goes to the oldest waiting jobs, so no valid job starves


def order(rows, n, hour=0):
    """Which READY jobs a run publishes, as ids. rows: (id, admission, fit score, posted date, first surfaced[, source]).

    The first n // MAIL_SHARE places go to the strongest rows not from an employer-board scan (`web:*`). Strongest
    first: admitted before Review (no score or date sorts last), then higher Fit, then newer posting; ties by first
    surfaced, then id. The last n // FAIR_SHARE places are the oldest of the rest. A run of fewer than four places
    reserves its one place for the oldest only when the clock hour is a multiple of four.
    """
    ranked = sorted(rows, key=lambda r: ({"ADMIT": 0, "REVIEW": 1}.get(r[1], 2), -(r[2] if r[2] is not None else -1), -(r[3] or r[4].date()).toordinal(), r[4], r[0]))
    fair = n // FAIR_SHARE or int(n > 0 and hour % FAIR_SHARE == 0)
    mail = [r for r in ranked if not str(r[5] if len(r) > 5 else "").startswith("web:")][:n // MAIL_SHARE]
    taken = {r[0] for r in mail}
    first = mail + [r for r in ranked if r[0] not in taken][:max(n - fair - len(mail), 0)]
    ranked = mail + [r for r in ranked if r[0] not in taken]
    rest = sorted(ranked[len(first):], key=lambda r: (r[4], r[0]))
    return [r[0] for r in first + rest[:n - len(first)]]


def _pick(connection, limit, gated=False, hour=None, stats=None):
    """The jobs this run publishes. `stats`, when given, receives waiting (publishable now) and ready_unpublished (also
    those awaiting a score).
    """
    base = (" FROM v7_jobs j JOIN v7_job_descriptions d ON d.job_id=j.id LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='READY'"
            " AND j.notion_page_id IS NULL" + (" AND f.admission IN ('ADMIT', 'REVIEW')" if gated else ""))
    with connection.cursor() as cursor:
        cursor.execute("SELECT j.id, f.admission, f.score, j.posted_date, j.first_seen, j.source" + base)
        eligible = [tuple(r) for r in cursor.fetchall()]
        if stats is not None:
            stats["waiting"] = len(eligible)
            if gated:
                cursor.execute("SELECT COUNT(*)" + base.replace(" AND f.admission IN ('ADMIT', 'REVIEW')", ""))
                stats["ready_unpublished"] = int(cursor.fetchone()[0])
            else:
                stats["ready_unpublished"] = len(eligible)
        ids = order(eligible, min(limit, limits.NOTION_PER_RUN), _now().hour if hour is None else hour)
        if not ids:
            return [], set()
        cursor.execute(
            "SELECT j.id, j.dedupe_key, j.title, j.company, j.final_apply_url, j.source, j.provider, j.lane, j.first_seen, j.posted_date,"
            " d.summary, d.responsibilities, d.requirements, d.qualifications, d.full_text, f.score, f.line, f.admission, f.admission_reason, f.work_mode, j.salary_text, j.apply_kind, f.lane, f.eligible, j.location_text, j.route_evidence"
            + base + " AND j.id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids))
        by_id = {row[0]: row for row in cursor.fetchall()}
        rows = [by_id[i] for i in ids if i in by_id]
        known = set()
        for row in rows:
            cursor.execute("SELECT 1 FROM v7_ledger_urls WHERE url_hash=%s", (url_key(row[4]),))
            # a keep-list role the audit once trashed is a user choice now; the Ledger listing below still stops a
            # real duplicate
            if cursor.fetchone() and not lanes.on_keep_list(row[3], row[2]):
                known.add(row[0])
    return rows, known


def release_kept_in_ledger(connection):
    """A keep-list role set aside as `in_ledger` only because its remembered URL outlived a page the audit trashed goes back
    to READY. A page that still exists is found by the Ledger listing at publish time. -> count.
    """
    released = 0
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, company, title FROM v7_jobs WHERE status='DUPLICATE' AND unresolved_reason='in_ledger' AND notion_page_id IS NULL")
        for job_id, company, title in cursor.fetchall():
            if lanes.on_keep_list(company, title):
                cursor.execute("UPDATE v7_jobs SET status='READY', unresolved_reason='released_keep_list', updated_at=%s WHERE id=%s AND status='DUPLICATE'", (_now(), job_id))
                released += 1
    return released


def run(limit, live, environ=os.environ):
    # shadow mode: the lane decision is stored but not shown as Admission Status
    gated = environ.get("V7_FIT_GATE") == "true"
    """No database connection is ever held across slow work (Notion calls): read, close, call, reconnect, write."""
    counts = {"picked": 0, "created": 0, "duplicate": 0, "failed": 0, "seeded": 0, "calls": 0, "claim_won": 0, "claim_held": 0, "claim_readbacks": 0}
    claimant = ledger_claim.who("publish")
    client = Client(environ)
    counts["target"] = ledger.OK if not live else ledger.verify(client)
    if counts["target"] != ledger.OK:
        return counts
    with store.connect() as connection:
        store.ensure_schema(connection)
        need_seed = live and not _seeded(connection)
    if need_seed:
        urls = ledger_urls(client)
        with store.connect() as connection:
            counts["released"] = _store_seed(connection, urls)
        counts["seeded"] = len(urls)
    if live:
        with store.connect() as connection:
            counts["kept_released"] = release_kept_in_ledger(connection)
    stats = {}
    with store.connect() as connection:
        rows, known = _pick(connection, limit, gated, stats=stats)
    counts["picked"] = len(rows)
    # before this run's picks: the backlog size a capped run hides
    counts["waiting"], counts["ready_unpublished"] = stats.get("waiting", 0), stats.get("ready_unpublished", 0)
    counts["picked_not_board"] = sum(1 for r in rows if not str(r[5] or "").startswith("web:"))
    listed = _listed(client, [r[1] for r in rows if r[0] not in known], counts) if live else None
    for (job_id, key, title, company, url, source, provider, lane, first_seen, posted, summary, resp, req, qual, full, fit, fit_line, admission, admission_reason, work_mode, salary, apply_kind, fit_lane, eligible, location, route) in rows:
        if job_id in known:
            counts["duplicate"] += 1
            if live:
                with store.connect() as connection, connection.cursor() as cursor:
                    cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', unresolved_reason='in_ledger', "
                                   "updated_at=%s WHERE id=%s", (_now(), job_id))
            continue
        if live:
            with store.connect() as connection, connection.cursor() as cursor:
                original = same_opening(cursor, job_id, company, title, url, location)
                if original:
                    cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', repost_of=%s, unresolved_reason='same_opening', updated_at=%s WHERE id=%s",
                                   (original, _now(), job_id))
                    counts["duplicate"] += 1
                    continue
        if not live:
            counts["created"] += 1
            continue
        desc = {"summary": summary, "responsibilities": resp, "requirements": req, "qualifications": qual}
        if not any((summary, resp, req, qual)):
            desc["summary"] = (full or "")[:6000]
        row = {"title": title, "company": company, "url": url, "source": source, "provider": provider, "lane": lane,
               "key": key,
               "first_seen": ledger.ct_day(first_seen), "posted": posted,
               "fit": fit, "fit_line": fit_line,
               "admission": admission if gated else None, "admission_reason": admission_reason if gated else None,
               "work_mode": work_mode, "salary": salary, "apply_kind": apply_kind,
               "location": location, "route": route, "text": full}
        if gated and fit_lane:
            row["lane"], row["eligible"] = fit_lane, [n for n in (eligible or "").split(",") if n]
        try:
            page_id, created = (adopted(listed[key]) if listed is not None and key in listed else None), False
            if not page_id:
                # no create without the cross-process claim on this key
                state, recorded = ledger_claim.claim(key, claimant, store.connect)
                if state == ledger_claim.CREATED:
                    page_id = recorded
                else:
                    counts["claim_won" if state == ledger_claim.WON else "claim_held"] += 1
                    counts["claim_readbacks"] += 1
                    # the authoritative read-back by Stable Job Key; a held key is adopt-only
                    page_id = existing_page(client, key)
                    if not page_id and state == ledger_claim.HELD:
                        # another creator holds the key, or an earlier create is unresolved: nothing is created
                        continue
            if not page_id:
                page_id, created = client.create(properties(row), body.build(key, desc)), True
                body.fill(client, page_id, desc)
            else:
                # an adopted page whose fill never ran is completed, never repeated
                body.fill(client, page_id, desc, strict=False)
        # the claim stays: an unresolved create is settled by read-back after the lease, never repeated
        except NotionError as error:
            counts["failed"] += 1
            counts["error"] = f"{error} {getattr(error, 'detail', '')}".strip()[:80]
            if counts["failed"] >= 3:
                break
            continue
        # A page is verified ONLY by the read-back reading Notion, so publish never sets verified_at. A page this run
        # created carries the Fit it was scored with, so it is recorded as synced.
        synced = created and fit is not None
        with store.connect() as connection, connection.cursor() as cursor:  # record at once: a crash cannot duplicate
            cursor.execute("UPDATE v7_jobs SET status='PUBLISHED', notion_page_id=%s, updated_at=%s, fit_synced_at=%s, meta_v=%s, ledger_claim_by=NULL, ledger_claim_at=NULL WHERE id=%s",
                           (page_id, _now(), _now() if synced else None, meta.META_VERSION if synced else None, job_id))
            cursor.execute("INSERT IGNORE INTO v7_ledger_urls (url_hash, source, seen_at) VALUES (%s,'v7',%s)",
                           (url_key(url), _now()))
        counts["created"] += 1
    from lifeos.jobs import fit_sync                                                 # noqa: PLC0415
    counts["fit_sync"] = fit_sync.run(client, live, gated)
    if live:
        from lifeos.jobs import readback                                              # noqa: PLC0415
        counts["readback"] = readback.run(client, live)
        # a page that does not read back fails the stage (PUBLISH_RECORDS_FAILED)
        counts["failed"] += counts["readback"].get("failed", 0)
    counts["calls"] = client.calls
    return counts
