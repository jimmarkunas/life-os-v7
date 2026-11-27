"""Enrich stage: read the FINAL page of RESOLVED rows for description, posting date, liveness and match.

Description order: ATS API (Greenhouse, Lever) -> JobPosting JSON-LD -> page text. Outcomes: READY | CLOSED |
EXCLUDED_STALE | back to NEW (page does not match the job) | pending with a fixed reason. Plain HTTP only. Counts
only.
"""
from datetime import date, datetime, timedelta, timezone
import html as htmllib
import json
import re
from urllib.parse import urlsplit

from lifeos.jobs.resolve import ats_match, search_match
from lifeos.platform import egress, impersonate, usage, limits, tinyfish
from lifeos.jobs import ats_detail, jd, jsonld, lanes, quality, store
from lifeos.platform.http import fetch

TRIED_REASON = "description_empty_tf"
CLOSED_TEXT = re.compile(r"no longer accepting|no longer available|position (has been|is) filled|job (is )?closed|"
                         r"posting (has )?expired|this job has expired|not accepting applications", re.I)


_now = store.now


GONE = {"closed": True}


def _board_json(url):
    page = fetch(url, timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2)
    if page.status in (404, 410):
        return GONE
    try:
        return json.loads(page.html) if page.status == 200 else None
    except ValueError:
        return None


def _api_job(url):
    parts = urlsplit(url)
    host, segs = (parts.hostname or "").lower(), [s for s in parts.path.split("/") if s]
    if host.endswith("greenhouse.io"):
        board = (re.search(r"(?:^|&)for=([^&]+)", parts.query) or [None, segs[0] if segs else ""])[1]
        ident = (re.search(r"(?:^|&)token=(\d+)", parts.query) or [None, None])[1] or next(
            (s for s in reversed(segs) if s.isdigit()), None)
        data = _board_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{ident}") if board and ident else None
        if data is None or data is GONE:
            return data
        posted = str(data.get("first_published") or data.get("updated_at") or "")[:10]
        return {"title": data.get("title"), "html": htmllib.unescape(data.get("content") or ""), "posted": posted}
    if host == "jobs.lever.co" and len(segs) >= 2:
        data = _board_json(f"https://api.lever.co/v0/postings/{segs[0]}/{segs[1]}")
        if data is None or data is GONE:
            return data
        created = data.get("createdAt")
        posted = datetime.fromtimestamp(created / 1000, timezone.utc).date().isoformat() if created else ""
        lists = "".join(f"<h3>{x.get('text') or ''}</h3><ul>{x.get('content') or ''}</ul>" for x in (data.get("lists") or []))
        return {"title": data.get("text"), "html": (data.get("description") or "") + lists + (data.get("additional") or ""),
                "posted": posted}
    return None


def _parse_date(value):
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _title_ok(want, *found, strict=False):
    tokens = [t for t in _joined(re.sub(r"\([^)]*\)", " ", want or "")).split() if len(t) > 2] or [t for t in _joined(want).split() if len(t) > 2]
    hay = _joined(" ".join(f for f in (found[:1] if strict else found) if f))
    return bool(tokens) and sum(t in hay.split() for t in tokens) / len(tokens) >= (0.8 if strict else 0.6)


def _joined(text):
    return re.sub(r"\b([a-z]) (?=[0-9])", r"\1", ats_match.norm(text))


def _greenhouse_embed(url, html):
    job = re.search(r"[?&]gh_jid=(\d+)", url)
    board = re.search(r"greenhouse\.io/[^\"'\s<>]*?[?&;]for=([A-Za-z0-9_-]+)", html or "")
    return f"https://boards.greenhouse.io/embed/job_app?for={board.group(1)}&token={job.group(1)}" if job and board else None


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
BOARD_PREFIX = {"gh": "greenhouse", "lv": "lever", "ab": "ashby"}


def board_of(source):
    match = re.fullmatch(r"web:(gh|lv|ab)-([a-z0-9][a-z0-9._-]*)", source or "")
    return (BOARD_PREFIX[match.group(1)], match.group(2)) if match else None


def board_job_url(board, url):
    kind, slug = board
    if kind == "greenhouse":
        ident = re.search(r"[?&](?:gh_jid|token)=(\d+)", url or "") or re.search(r"/(\d{6,})(?:[/?#]|$)", urlsplit(url or "").path + "/")
        return f"https://boards.greenhouse.io/{slug}/jobs/{ident.group(1)}" if ident else None
    ident = UUID.search(url or "")
    if not ident:
        return None
    return f"https://jobs.lever.co/{slug}/{ident.group(0)}" if kind == "lever" else f"https://jobs.ashbyhq.com/{slug}/{ident.group(0)}"


def _title_words_in(want, html):
    tokens = [t for t in ats_match.norm(want).split() if len(t) > 2]
    hay = set(ats_match.norm(re.sub(r"<[^>]+>", " ", html or "")).split())
    return sum(t in hay for t in tokens) / len(tokens) if tokens else 0


def read_page(url, title, lane=None, proof=None, first_party=False, board=None):
    """Facts for one final URL, with fixed outcome codes; proof='unproven' means the page must prove identity by
    title.
    """
    problem = quality.link_problem(url)
    strict = bool(problem) and proof == "unproven" and problem in quality.AMBIGUOUS
    if problem and not strict:
        return {"outcome": "mismatch", "reason": problem}
    api = ats_detail.read(url) or _api_job(url)
    if api is None and board:
        own = board_job_url(board, url)
        api = (ats_detail.read(own) or _api_job(own)) if own and own != url else None
        if api and api.get("closed"):
            api = None
    if api and api.get("closed"):
        return {"outcome": "closed"}
    if api:
        text_source = api.get("plain") or api["html"]
        desc = jd.describe(text_source, is_html=not api.get("plain"))
        found_title, posted, source_kind = api.get("title") or desc["full_text"][:300], _parse_date(api.get("posted")), "ats_api"
        valid_through = None
    else:
        page = fetch(url, timeout=15, max_hops=6)
        if page.status in (401, 403) and (urlsplit(url).hostname or "").lower().endswith("revolut.com"):
            page = impersonate.fetch(url, warm_url="https://www.revolut.com/en-GB/careers/")
        if page.status in (401, 403) and first_party:
            page = egress.honest(url, timeout=15, max_hops=6)
        if page.status in (404, 410):
            return {"outcome": "closed"}
        if page.status != 200 or not page.html:
            return {"outcome": "blocked", "reason": f"http_{page.status or 0}"}
        embedded = _greenhouse_embed(url, page.html)
        if embedded:
            api = _api_job(embedded)
            if api and not api.get("closed"):
                desc = jd.describe(api["html"], is_html=True)
                return finish(title, desc, api.get("title") or desc["full_text"][:300], _parse_date(api.get("posted")), "ats_api", None, lane, strict, first_party,
                              thin_ok=bool(board))
        result = parse_html(url, title, page.html, lane, strict, first_party)
        if (urlsplit(url).hostname or "").lower().endswith("dice.com") and "easy apply" in page.html.lower():
            result["apply_kind"] = "easy_apply"
        return result
    return finish(title, desc, found_title, posted, source_kind, valid_through, lane, strict, first_party, thin_ok=bool(board))


def parse_html(url, title, html, lane=None, strict=False, first_party=False):
    posting = jsonld.job_posting(html)
    body_text = jd.html_to_text(html)
    if CLOSED_TEXT.search(body_text[:4000]):
        return {"outcome": "closed"}
    if posting and posting.get("description"):
        desc, source_kind = jd.describe(str(posting["description"]), is_html=True), "jsonld"
        found_title, posted = posting.get("title") or "", jsonld.posted_date(posting)
        valid_through = _parse_date(str(posting.get("validThrough") or ""))
    else:
        embedded = jsonld.embedded_description(html)
        if embedded:
            desc, source_kind = jd.describe(embedded, is_html=True), "next_data"
        else:
            region = jd.linkedin_description(html)
            desc, source_kind = jd.describe(body_text if region is None else region, is_html=False), "page_text"
        posted = valid_through = None
        found_title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1]
        found_title = jd.linkedin_title(html) or found_title
    result = finish(title, desc, found_title, posted, source_kind, valid_through, lane, strict, first_party)
    if first_party and result.get("outcome") == "mismatch" and not result.get("reason") and _title_words_in(title, html) >= 0.8:
        result = finish(title, desc, title, posted, source_kind, valid_through, lane, False, first_party)
        if result.get("outcome") in ("ready", "stale"):
            result["proof"] = "page_text"
    where = jsonld.location(posting) if posting else None
    if where and result.get("outcome") in ("ready", "stale"):
        result["location"] = where
    org = jsonld.hiring_organization(posting) if posting else None
    if org and result.get("outcome") in ("ready", "stale"):
        result["company"] = org
    return result


def finish(title, desc, found_title, posted, source_kind, valid_through, lane=None, strict=False, first_party=False, thin_ok=False):
    if len(desc["full_text"]) < 200:
        return {"outcome": "blocked", "reason": "description_empty", "source_kind": source_kind}
    problem = quality.jd_problem(desc["full_text"])
    if problem in ("template", "listing"):
        return {"outcome": "mismatch", "reason": "jd_" + problem}
    if problem == "thin" and not (thin_ok or (first_party and lanes.lane_for(lane) == "Scale-Up")):
        return {"outcome": "blocked", "reason": "jd_thin", "source_kind": source_kind}
    if not _title_ok(title, found_title, desc["full_text"][:1500] if source_kind in ("page_text", "next_data") else "", strict=strict):
        return {"outcome": "mismatch"}
    today = _now().date()
    if valid_through and valid_through < today:
        return {"outcome": "closed"}
    max_age = lanes.first_party_age(lane) if first_party else lanes.POLICIES[lanes.lane_for(lane)].max_age_days
    if posted and max_age is not None and (today - posted) > timedelta(days=max_age):
        return {"outcome": "stale", "posted": posted, "description": desc, "source_kind": source_kind, **({"proof": "title"} if strict else {})}
    return {"outcome": "ready", "posted": posted, "description": desc, "source_kind": source_kind, **({"proof": "title"} if strict else {})}


TRUST = {"employer": 1, "aggregator": 2, "newsletter": 3, "email": 4}


def _as_date(value):
    return date.fromisoformat(str(value)[:10]) if value else None


def accepted_date(old, old_source, new, new_source="employer"):
    """-> (posting date, source) to store: a weaker source never replaces a stronger; between equal sources the
    earliest wins.
    """
    old_source = old_source or ("employer" if old else None)
    if new is None:
        return old, old_source
    if old is None:
        return new, new_source
    rank, held = TRUST.get(new_source, len(TRUST) + 1), TRUST.get(old_source, 1)
    if rank > held:
        return old, old_source
    if rank < held:
        return new, new_source
    return min(old, new), old_source


BOARD_HOST_COMPANY = r"(^| )(com|io|net|org|co|wd[0-9]+|myworkdayjobs)( |$)"


def save_description(cursor, job_id, result, now=None):
    d = result["description"]
    cursor.execute(
        "INSERT INTO v7_job_descriptions (job_id, source_kind, full_text, summary, responsibilities, requirements,"
        " qualifications, fingerprint, fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE"
        " source_kind=VALUES(source_kind), full_text=VALUES(full_text), summary=VALUES(summary),"
        " responsibilities=VALUES(responsibilities), requirements=VALUES(requirements),"
        " qualifications=VALUES(qualifications), fingerprint=VALUES(fingerprint), fetched_at=VALUES(fetched_at)",
        (job_id, result["source_kind"], d["full_text"], d["summary"], d["responsibilities"], d["requirements"],
         d["qualifications"], d["fingerprint"], now or _now()))


def save(connection, job_id, result):
    now, outcome = _now(), result["outcome"]
    with connection.cursor() as cursor:
        if outcome in ("ready", "stale"):
            d = result["description"]
            save_description(cursor, job_id, result, now)
            if result.get("apply_kind"):
                cursor.execute("UPDATE v7_jobs SET apply_kind=%s WHERE id=%s", (result["apply_kind"], job_id))
            if result.get("company"):
                cursor.execute("UPDATE v7_jobs SET company=%s WHERE id=%s AND company REGEXP %s", (result["company"][:200], job_id, BOARD_HOST_COMPANY))
            if result.get("location"):
                cursor.execute("UPDATE v7_jobs SET location_text=%s WHERE id=%s AND (location_text IS NULL OR location_text='')", (result["location"][:2000], job_id))
            cursor.execute("SELECT posted_date, posted_source FROM v7_jobs WHERE id=%s", (job_id,))
            known = cursor.fetchone() or (None, None)
            posted, source = accepted_date(_as_date(known[0]), known[1], result.get("posted"), result.get("posted_source") or "employer")
            age = (now.date() - posted).days if posted else None
            cursor.execute("UPDATE v7_jobs SET status=%s, posted_date=%s, posted_source=%s, final_apply_url=%s, "
                           "posted_age_days=COALESCE(%s, posted_age_days), link_proof=COALESCE(%s, link_proof), unresolved_reason=NULL, updated_at=%s WHERE id=%s",
                           ("READY" if outcome == "ready" else "EXCLUDED_STALE", posted, source,
                            result.get("final_url") or None, age, result.get("proof"), now, job_id))
        elif outcome == "closed":
            cursor.execute("UPDATE v7_jobs SET status='CLOSED', unresolved_reason='closed', updated_at=%s WHERE id=%s",
                           (now, job_id))
        elif outcome == "linkedin_page":
            cursor.execute("SELECT id FROM v7_jobs WHERE final_apply_url=%s AND id<>%s LIMIT 1", (result["url"], job_id))
            if cursor.fetchone():
                cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', final_apply_url=NULL, unresolved_reason=%s, updated_at=%s WHERE id=%s", ("dup_of_final_url", now, job_id))
            else:
                cursor.execute("UPDATE v7_jobs SET status='RESOLVED', final_apply_url=%s, apply_kind='aggregator', link_proof=NULL, unresolved_reason=NULL, updated_at=%s WHERE id=%s",
                               (result["url"], now, job_id))
        elif outcome == "mismatch":
            cursor.execute("UPDATE v7_jobs SET status=IF(resolve_attempts+1>=%s, 'HOLD', 'NEW'), final_apply_url=NULL, apply_kind=NULL, link_proof=NULL, "
                           "unresolved_reason=%s, resolve_attempts=resolve_attempts+1, updated_at=%s WHERE id=%s",
                           (limits.RESOLVE_MAX_ATTEMPTS, (result.get("reason") or "link_mismatch")[:100], now, job_id))
        else:
            cursor.execute("UPDATE v7_jobs SET unresolved_reason=%s, enrich_attempts=enrich_attempts+1, "
                           "status=IF(enrich_attempts>=%s,'HOLD',status), updated_at=%s WHERE id=%s",
                           ((result.get("reason") or "blocked")[:100], limits.ENRICH_MAX_ATTEMPTS, now, job_id))


DAILY_SLOT = "MOD(CRC32(id), 24) = HOUR(UTC_TIMESTAMP())"


def _first_party_over_age(now):
    clauses, params = [], []
    for name in lanes.POLICIES:
        days = lanes.first_party_age(name)
        if days is None:
            continue
        rows = [name] + [alias for alias, target in lanes.LANE_ALIAS.items() if target == name]
        clauses.append("(lane IN (" + ", ".join(["%s"] * len(rows)) + ") AND posted_date IS NOT NULL AND posted_date < %s)")
        params += rows + [(now - timedelta(days=days)).date()]
    return ("", []) if not clauses else (" AND NOT (" + " OR ".join(clauses) + ")", params)


def relink_first_party(cursor, now=None):
    """Put a first-party job (source web:*) bounced to NEW or HOLD back to RESOLVED with its own URL when it passes
    the link test. -> count.
    """
    now = now or _now()
    cursor.execute("SELECT id, source_url FROM v7_jobs WHERE source LIKE 'web:%%' AND source<>'web:openjobs' AND final_apply_url IS NULL AND source_url IS NOT NULL AND (resolve_attempts < %s OR status='HOLD') AND "
                   "((status='NEW' AND resolve_attempts < %s AND (unresolved_reason LIKE 'audit_%%' OR unresolved_reason IN ('link_mismatch', 'no_job_id', 'listing_url') OR unresolved_reason LIKE 'Fit %% below 72')) OR "
                   "(status='HOLD' AND (unresolved_reason LIKE 'http_40%%' OR unresolved_reason IN ('link_mismatch', 'jd_listing', 'jd_template')) AND " + DAILY_SLOT + ")) LIMIT 500",
                   (limits.RESOLVE_MAX_ATTEMPTS, limits.RESOLVE_MAX_ATTEMPTS))
    ids = []
    for job_id, url in cursor.fetchall():
        problem = quality.link_problem(url)
        if problem is None or problem in quality.AMBIGUOUS:
            ids.append((job_id, url, "unproven" if problem else None))
    for job_id, url, proof in ids:
        cursor.execute("UPDATE v7_jobs SET status='RESOLVED', final_apply_url=%s, apply_kind='ats', link_proof=%s, unresolved_reason=NULL, enrich_attempts=0, resolve_attempts=LEAST(resolve_attempts, %s), updated_at=%s WHERE id=%s",
                       (url, proof, limits.RESOLVE_MAX_ATTEMPTS - 1, now, job_id))
    cursor.execute("UPDATE v7_jobs SET status='RESOLVED', unresolved_reason=NULL, enrich_attempts=LEAST(enrich_attempts, %s), updated_at=%s WHERE status='HOLD' AND final_apply_url IS NOT NULL"
                   " AND source LIKE 'web:%%' AND source<>'web:openjobs' AND notion_page_id IS NULL AND (unresolved_reason LIKE 'http_40%%' OR unresolved_reason IN ('jd_thin', 'description_empty', 'description_empty_tf'))"
                   " AND " + DAILY_SLOT, (limits.ENRICH_MAX_ATTEMPTS - 1, now))
    reread = int(cursor.rowcount or 0)
    aged, params = _first_party_over_age(now)
    cursor.execute("UPDATE v7_jobs SET status='RESOLVED', unresolved_reason='requeued_age', enrich_attempts=0, updated_at=%s WHERE status='EXCLUDED_STALE'"
                   " AND source LIKE 'web:%%' AND source<>'web:openjobs' AND notion_page_id IS NULL AND final_apply_url IS NOT NULL" + aged, (now, *params))
    return len(ids) + reread + int(cursor.rowcount or 0)


def host_family(url):
    host = (urlsplit(url or "").hostname or "").lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin"
    for domain in search_match.ATS_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return domain
    return "employer_site"


def _bump(table, key):
    table[key] = table.get(key, 0) + 1


def blocked_report(results, urls):
    reasons, families, sources = {}, {}, {}
    for job_id, result in results:
        if result["outcome"] == "blocked":
            _bump(sources, result.get("source_kind") or "none")
            _bump(reasons, result.get("reason") or "blocked")
            _bump(families, host_family(urls.get(job_id)))
    return {"reasons": reasons, "families": families, "by_source": sources}


def mismatch_report(results, urls, source_of, previous):
    reasons, sources, families, repeat = {}, {}, {}, 0
    for job_id, result in results:
        if result["outcome"] != "mismatch":
            continue
        _bump(reasons, result.get("reason") or "title")
        _bump(sources, source_of.get(job_id) or "unknown")
        _bump(families, host_family(urls.get(job_id)))
        repeat += previous.get(job_id) == (result.get("reason") or "link_mismatch")
    return {"reasons": reasons, "by_source": sources, "families": families, "repeat": repeat}


def run(limit, live, drain=False):
    """drain: the backlog pass; it never spends the TinyFish Fetch day cap."""
    relinked = 0
    with store.connect() as connection:
        store.ensure_schema(connection)
        if live:
            with connection.cursor() as cursor:
                relinked = relink_first_party(cursor)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, final_apply_url, title, apply_kind, unresolved_reason, lane, source, link_proof, source_url FROM v7_jobs WHERE status='RESOLVED' "
                           "AND final_apply_url IS NOT NULL ORDER BY last_seen DESC LIMIT %s", (limit,))
            fetched = [tuple(r) for r in cursor.fetchall()]
    rows = [r[:5] for r in fetched]
    lane_of = {r[0]: r[5] for r in fetched}
    source_of = {r[0]: r[6] for r in fetched}
    proof_of = {r[0]: (r[7] if len(r) > 7 else None) for r in fetched}
    page_of = {r[0]: (r[8] if len(r) > 8 else None) for r in fetched}
    counts = {"picked": len(rows), "outcome": {}, "source": {}, "relinked": relinked}
    results = []
    for job_id, url, title, kind, _prev in rows:
        url = quality.canonical_job_url(url)
        target = url
        if kind == "easy_apply" or "linkedin.com" in (urlsplit(url).hostname or ""):
            from lifeos.jobs.resolve.aggregators import li_apply                # noqa: PLC0415 - the public guest page carries the JD
            jid = li_apply.job_id(url)
            target = (li_apply.GUEST_API + jid) if jid else url
        try:
            result = read_page(target, title, lane_of.get(job_id), proof_of.get(job_id), lanes.first_party_source(source_of.get(job_id)),
                               board_of(source_of.get(job_id)))
        except Exception as error:                                   # noqa: BLE001 - one unreadable page never stops the batch
            result = {"outcome": "blocked", "reason": "read_" + type(error).__name__.lower()[:30]}
        if result.get("reason") == "description_empty" and _prev == TRIED_REASON:
            result["reason"] = TRIED_REASON
        result["final_url"] = url
        results.append((job_id, result))
        _bump(counts["outcome"], result["outcome"])
        if result.get("source_kind"):
            _bump(counts["source"], result["source_kind"])
    if drain:
        left = [job_id for job_id, r in results if r["outcome"] == "blocked"]
        results = [(job_id, r) for job_id, r in results if r["outcome"] != "blocked"]
        counts["left_for_hourly"] = len(left)
    else:
        _fallback(results, rows, counts, lane_of, {i for i, p in proof_of.items() if p == "unproven"}, {i for i, src in source_of.items() if lanes.first_party_source(src)})
        url_of = {job_id: quality.canonical_job_url(url) for job_id, url, _, _, _ in rows}
        for job_id, result in results:
            result.setdefault("final_url", url_of[job_id])
    counts["reader_misses"] = ats_detail.misses()
    counts["blocked_final"] = blocked_report(results, {job_id: url for job_id, url, _, _, _ in rows})
    counts["mismatch"] = mismatch_report(results, {r[0]: r[1] for r in rows}, source_of, {r[0]: r[4] for r in rows})
    counts["linkedin_page"] = linkedin_page(results, source_of, page_of)
    if live and results:
        with store.connect() as connection:
            for job_id, result in results:
                save(connection, job_id, result)
    counts["saved"] = bool(live)
    return counts


def linkedin_page(results, source_of, page_of):
    """A LinkedIn row whose employer link failed the title match takes its own LinkedIn posting for Enrich to prove
    next. -> count.
    """
    from lifeos.jobs.resolve.aggregators import li_apply                    # noqa: PLC0415
    switched = 0
    for job_id, result in results:
        own = page_of.get(job_id) or ""
        if (result["outcome"] != "mismatch" or result.get("reason") or not str(source_of.get(job_id) or "").startswith("linkedin")
                or li_apply.is_linkedin(result.get("final_url") or "") or not (li_apply.is_linkedin(own) and li_apply.job_id(own))):
            continue
        result.update(outcome="linkedin_page", url="https://www.linkedin.com/jobs/view/" + li_apply.job_id(own))
        switched += 1
    return switched


def _strict(job_id, titles, unproven_ids):
    return job_id in unproven_ids and bool(quality.link_problem(titles[job_id][0]))


def _mark_tried(result):
    if result.get("reason") == "description_empty":
        result["reason"] = TRIED_REASON


def _swap(results, counts, i, result):
    results[i] = (results[i][0], result)
    counts["outcome"]["blocked"] -= 1
    _bump(counts["outcome"], result["outcome"])


def _fallback(results, rows, counts, lane_of=None, unproven_ids=(), first_party_ids=()):
    """One TinyFish Fetch of an already-resolved final URL when plain HTTP and the ATS API gave no usable page;
    counted against the daily cap first.
    """
    titles = {job_id: (url, title) for job_id, url, title, _, _ in rows}
    tried = {job_id for job_id, _, _, _, prev in rows if prev == TRIED_REASON}
    blocked = [i for i, (job_id, r) in enumerate(results) if r["outcome"] == "blocked" and job_id not in tried]
    if not blocked:
        return
    try:
        with store.connect() as connection:
            allowed = usage.reserve(connection, len(blocked))
    except store.StoreError:
        return
    counts["fallback_attempted"] = min(allowed, len(blocked))
    pacer, done = usage.Pacer(), 0
    for start in range(0, allowed, limits.TINYFISH_FETCH_BATCH):
        batch = blocked[start:min(start + limits.TINYFISH_FETCH_BATCH, allowed)]
        asked = {i: ats_detail.workday_cxs_url(titles[results[i][0]][0]) for i in batch}
        urls = [asked[i] or titles[results[i][0]][0] for i in batch]
        pacer.wait()
        try:
            found, _errors = tinyfish.fetch_many(urls, fmt="html", links=False)
        except tinyfish.TinyFishError:
            counts["tinyfish"] = "error"
            return
        for i, url in zip(batch, urls):
            item = found.get(url)
            text = item.get("text") if item and isinstance(item.get("text"), str) else ""
            job_id = results[i][0]
            job = ats_detail.parse_workday(text) if asked[i] and text else None
            if job:
                desc = jd.describe(job["html"], is_html=True)
                _swap(results, counts, i, finish(titles[job_id][1], desc, job["title"] or desc["full_text"][:300],
                                                 _parse_date(job["posted"]), "ats_api", None, (lane_of or {}).get(job_id),
                                                 _strict(job_id, titles, unproven_ids), job_id in first_party_ids))
            elif text:
                _swap(results, counts, i, parse_html(url, titles[job_id][1], text, (lane_of or {}).get(job_id),
                                                     _strict(job_id, titles, unproven_ids), job_id in first_party_ids))
                _mark_tried(results[i][1])
            else:
                _mark_tried(results[i][1])
                continue
            done += 1
    counts["tinyfish_recovered"] = done
