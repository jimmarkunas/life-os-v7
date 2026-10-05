"""Counts-only shape probes for sources whose real pages the build sandbox cannot reach. Run from Actions; prints facts (status, sizes,
key NAMES, counts) and never any posting text, URL or personal data. Result decides the next parser, nothing here writes anything."""
import json
import os
import re
from urllib.parse import urlsplit

from lifeos.platform.http import fetch
from lifeos.sources.web import registry

OPEN_JOBS = "https://backend.dehnbostele.workers.dev/data/"
JSONLD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)


def _shape(value, depth=0):
    if isinstance(value, dict):
        return {k: (_shape(v, depth + 1) if depth < 2 else type(v).__name__) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [len(value), _shape(value[0], depth + 1) if value and depth < 2 else None]
    return type(value).__name__


def open_jobs(fetcher=fetch):
    contact = os.environ.get("OPEN_JOBS_CONTACT", "")
    headers = {"User-Agent": f"life-os-v7 personal job feed reader ({contact})", "Accept": "application/json"}
    out = {}
    for name in ("manifest.json", "diffs/index.json"):
        page = fetcher(OPEN_JOBS + name, timeout=30, max_bytes=3_000_000, headers=headers)
        entry = {"status": page.status, "bytes": len(page.html)}
        try:
            entry["shape"] = _shape(json.loads(page.html))
        except ValueError:
            entry["shape"] = "not_json"
        out[name] = entry
    return out


def teamtailor(fetcher=fetch):
    out = {}
    for source in registry.load(registry.PATHS["Scale-Up"]):
        if source["kind"] != "teamtailor":
            continue
        page = fetcher(source["url"], timeout=20, max_bytes=3_000_000)
        rss = fetcher(source["url"].rstrip("/") + ".rss", timeout=20, max_bytes=3_000_000)
        out[source["id"][3:15]] = {
            "status": page.status, "bytes": len(page.html), "job_links": len(set(re.findall(r'href="[^"]*/jobs/\d[^"]*"', page.html))),
            "jobposting_ld": sum("JobPosting" in b for b in JSONLD.findall(page.html)), "rss_status": rss.status, "rss_items": rss.html.count("<item>")}
    return out


DICE_SAMPLES = ("1dde497d-8758-4eed-8aac-816bed294b63",)             # a public job id from a Dice search the user shared


def dice(fetcher=fetch):
    search = fetcher("https://www.dice.com/jobs?q=technical%20program%20manager&filters.workplaceTypes=Remote", timeout=25, max_bytes=3_000_000)
    links = sorted(set(re.findall(r'href="(https://www\.dice\.com/job-detail/[0-9a-f-]{36})', search.html)))[:3]
    out = {"search_status": search.status, "detail_links": len(links), "pages": []}
    links += [f"https://www.dice.com/job-detail/{i}" for i in DICE_SAMPLES]
    for link in links:
        page = fetcher(link, timeout=25, max_bytes=3_000_000)
        blocks = [b for b in JSONLD.findall(page.html) if "JobPosting" in b]
        description = ""
        try:
            description = (json.loads(blocks[0]).get("description") or "") if blocks else ""
        except ValueError:
            pass
        out["pages"].append({"status": page.status, "bytes": len(page.html), "jobposting_ld": len(blocks), "ld_description_chars": len(description),
                             "easy_apply_marker": "easy apply" in page.html.lower(), "apply_link_marker": "applyurl" in page.html.lower(),
                             "next_data": "__NEXT_DATA__" in page.html, "title_in_html": "<title>" in page.html.lower()})
    return out


def hiring(environ=os.environ):
    """Can the runner read the Hiring Pipeline page, and what does the tree look like (counts only)?"""
    from lifeos.jobs import hiring_pipeline                                                  # noqa: PLC0415
    from lifeos.platform.notion_client import Client                                          # noqa: PLC0415
    page_id = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    if not page_id:
        return {"configured": False}
    opportunities, status = hiring_pipeline.snapshot(Client(environ), page_id)
    return {"configured": True, "status": status, "active": sum(o.section == hiring_pipeline.ACTIVE for o in opportunities),
            "retired": sum(o.section == hiring_pipeline.RETIRED for o in opportunities), "with_rounds": sum(bool(o.rounds) for o in opportunities),
            "unparsed_titles": sum(not o.role for o in opportunities)}


def interview_environ(environ=os.environ):
    """The environment for Interview's Notion client: ONLY the Interview token, never the Jobs token (no fallback), or None when it is missing.
    Notion's `Client` insists on a data-source id; Interview does not use one, so a fixed placeholder satisfies it."""
    token = (environ.get("NOTION_INTERVIEW_TOKEN") or "").strip()
    return {"NOTION_API_TOKEN": token, "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "interview-unused"} if token else None


def interview_access(environ=os.environ, client_factory=None):
    """Can the Interview token read the Hiring Pipeline page? Counts and fixed codes only; writes nothing; never prints an id, a title or the token."""
    from lifeos.jobs import hiring_pipeline                                                  # noqa: PLC0415
    from lifeos.platform.notion_client import Client                                          # noqa: PLC0415
    page_id = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    mapped = interview_environ(environ)
    if not page_id or mapped is None:
        return {"status": "config_missing", "token": mapped is not None, "page": bool(page_id)}
    opportunities, status = hiring_pipeline.snapshot((client_factory or Client)(mapped), page_id)
    return {"status": status, "token": True, "page": True, "active": sum(o.section == hiring_pipeline.ACTIVE for o in opportunities),
            "retired": sum(o.section == hiring_pipeline.RETIRED for o in opportunities), "with_rounds": sum(bool(o.rounds) for o in opportunities),
            "unparsed_titles": sum(not o.role for o in opportunities)}


def ledger_target(environ=os.environ):
    """Does the configured data source pass the Job Ledger target check? Property names that fail are schema, not content."""
    from lifeos.jobs import ledger                                                           # noqa: PLC0415
    from lifeos.platform.notion_client import Client, NotionError                             # noqa: PLC0415
    client = Client(environ)
    status = ledger.verify(client)
    out = {"status": status}
    if status == ledger.MISMATCH:
        try:
            out["missing_or_wrong_type"] = ledger.missing(client)
        except NotionError:
            pass
    return out


ATS_PATTERNS = (                                   # (reader kind, regex over the result URL) -> slug; only kinds V7 can already list
    ("greenhouse", r"^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([a-z0-9_-]+)"),
    ("lever", r"^https?://jobs(?:\.eu)?\.lever\.co/([a-z0-9_-]+)"),
    ("ashby", r"^https?://jobs\.ashbyhq\.com/([a-z0-9_.-]+)"),
    ("workable", r"^https?://apply\.workable\.com/([a-z0-9_-]+)"),
    ("teamtailor", r"^https?://([a-z0-9-]+)\.teamtailor\.com"),
    ("rippling_html", r"^https?://ats\.rippling\.com/([a-z0-9_-]+)"),
    ("join_html", r"^https?://join\.com/companies/([a-z0-9_-]+)"),
)


def discover(search=None, sources=None):
    """For each Scale-Up sponsor with no readable job board, ask web search (free TinyFish Search) where its jobs live. Prints, per sponsor id,
    only `kind:slug` for a board V7 can already list, or `own:<host>` for the sponsor's own site, or None. No posting text, no URLs."""
    from lifeos.platform import tinyfish_search                                                # noqa: PLC0415
    from lifeos.platform.tinyfish import TinyFishError                                         # noqa: PLC0415
    from lifeos.jobs.resolve import ats_match                                                  # noqa: PLC0415
    from urllib.parse import urlsplit                                                          # noqa: PLC0415
    search = search or tinyfish_search.search
    out = {}
    for source in sources if sources is not None else [r for r in registry.load(registry.PATHS["Scale-Up"]) if r["status"] == "fallback"]:
        tokens = [w for w in ats_match.norm(source["company"]).split() if w not in ats_match.SUFFIX and len(w) > 2][:2]
        found = None
        try:
            for result in search(f"{source['company']} careers jobs UK")[:8]:
                url = result.get("url") or ""
                hay = ats_match.norm(url + " " + (result.get("title") or "") + " " + (result.get("snippet") or ""))
                if not tokens or not all(t in hay for t in tokens):
                    continue
                for kind, pattern in ATS_PATTERNS:
                    match = re.match(pattern, url, re.I)
                    if match:
                        found = f"{kind}:{match.group(1)}"
                        break
                if found:
                    break
                host = (urlsplit(url).hostname or "").removeprefix("www.")
                if found is None and host and re.search(r"career|jobs|join|work-with|vacanc|opportunit", url, re.I) \
                        and not any(x in host for x in ("linkedin.", "indeed.", "glassdoor.", "reed.", "totaljobs.", "cv-library.", "jooble.", "adzuna.", "ziprecruiter.")):
                    found = f"own:{host}"
        except TinyFishError as error:
            found = str(error).lower()
        out[source["id"]] = found
    return out


def lane_funnel():
    """Where do Scale-Up jobs stop? Counts only: v7_jobs by lane and status, then the fit decision, admission and (digit-free) reason."""
    from lifeos.jobs import store                                                            # noqa: PLC0415
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT lane, status, COUNT(*) FROM v7_jobs GROUP BY lane, status")
        by_status = {f"{lane}/{status}": n for lane, status, n in cursor.fetchall()}
        cursor.execute("SELECT f.lane, f.admission, f.admission_reason, COUNT(*) FROM v7_jobs j JOIN v7_job_fit f ON f.job_id = j.id "
                       "WHERE j.lane = 'Scale-Up' GROUP BY f.lane, f.admission, f.admission_reason")
        reasons = {}
        for lane, admission, reason, n in cursor.fetchall():
            key = f"{lane}/{admission}/{re.sub(r'[0-9]+', '#', reason or '')[:48]}"
            reasons[key] = reasons.get(key, 0) + n
    return {"by_lane_status": by_status, "scale_up_fit": reasons}


def scale_up_listing(fetcher=fetch, only=None):
    """Per ready Scale-Up sponsor: listing status, failure reason and job count from the runner's own network (counts only, no text)."""
    from lifeos.sources.web import lister                                                    # noqa: PLC0415
    out = {}
    for source in registry.load(registry.PATHS["Scale-Up"]):
        if source["status"] != "ready" or (only and source["id"] not in only):
            continue
        listing = lister.list_source(source, fetcher)
        out[source["id"]] = f"{listing.status}:{listing.reason or ''}:{len(listing.jobs)}"
    return out


def source_pages(ids=("su-futuristic-technologies-ltd", "su-otto-car-limited", "su-truvi-holdings-ltd"), plain=fetch):
    """Shape of a sponsor's page, plain versus Chrome-impersonated (counts and flags only): status, size, anchors, JobPosting JSON-LD,
    __NEXT_DATA__, and the hostnames of outbound links that look like a job board."""
    from lifeos.platform import impersonate                                                  # noqa: PLC0415
    from urllib.parse import urlsplit                                                        # noqa: PLC0415
    out = {}
    for source in registry.load(registry.PATHS["Scale-Up"]):
        if source["id"] not in ids:
            continue
        shapes = {}
        for how, got in (("plain", plain(source["url"], timeout=20, max_hops=3, max_bytes=3_000_000)),
                         ("chrome", impersonate.fetch(source["url"], warm_url=source.get("warm_url"), rounds=1))):
            text = got.html or ""
            hosts = sorted({(urlsplit(h).hostname or "").removeprefix("www.") for h in re.findall(r'href=["\'](https?://[^"\']+)', text, re.I)
                            if re.search(r"greenhouse|lever|ashby|workable|teamtailor|bamboohr|recruitee|personio|breezy|join\.com|pinpoint|smartrecruiters|rippling|jobs", h, re.I)})[:6]
            shapes[how] = {"status": got.status, "bytes": len(text), "anchors": len(re.findall(r"<a\b", text, re.I)), "jsonld_job": len(re.findall(r"JobPosting", text)),
                           "next_data": "__NEXT_DATA__" in text, "job_hosts": hosts, "final": (urlsplit(got.final_url).path or "/")[:40]}
        out[source["id"]] = shapes
    return out


def _rows(html_readers, source, text):
    """How many rows the source's own reader finds in `text`, or the error class when it refuses the page."""
    if not text:
        return None
    try:
        return len(html_readers.READERS[source["kind"]](text, source, None))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return type(error).__name__


def egress_options(ids=("su-futuristic-technologies-ltd", "su-otto-car-limited", "su-truvi-holdings-ltd")):
    """For each stubborn sponsor, which route reaches its careers page from the runner? Per route: status, bytes, anchors, job markers, and what the
    sponsor's own reader makes of it (rows, or the error class). Counts and flags only."""
    from lifeos.platform import egress                                                       # noqa: PLC0415
    from lifeos.sources.web import html_readers                                              # noqa: PLC0415
    routes = {"honest_ua": egress.honest, "reader_proxy": egress.reader_proxy, "wayback": egress.wayback}
    out = {}
    for source in registry.load(registry.PATHS["Scale-Up"]):
        if source["id"] not in ids:
            continue
        results = {}
        for name, route in routes.items():
            try:
                got = route(source["url"])
            except Exception as error:                                                       # noqa: BLE001 - a probe route must never stop the probe
                results[name] = {"error": type(error).__name__}
                continue
            text = got.html or ""
            results[name] = {"status": got.status, "error": got.error, "bytes": len(text), "anchors": len(re.findall(r"<a\b", text, re.I)),
                             "job_markup": len(re.findall(r"JobPosting|rjjobportal", text)), "rows": _rows(html_readers, source, text)}
        out[source["id"]] = results
    return out


GLASSDOOR_URLS = ("https://www.glassdoor.co.uk/Jobs/Revolut-London-Jobs-EI_IE1176471_IL.8,14_IC2671300.htm",          # an employer's London listing (Jim's methodology URL shape)
                  "https://www.glassdoor.co.uk/Job/london-program-manager-jobs-SRCH_IL.0,6_IC2671300_KO7,22.htm",
                  "https://www.glassdoor.com/Job/remote-program-manager-jobs-SRCH_KO0,23.htm")
BLOCK_MARKERS = ("just a moment", "verify you are human", "captcha", "cf-chl", "access denied", "enable javascript")


def _glassdoor_facts(text, status):
    low = (text or "").lower()
    return {"status": status, "bytes": len(text or ""), "job_markup": len(re.findall(r"JobPosting|jobListing|job-listing|data-test=\"job", text or "")),
            "job_links": len(re.findall(r"/job-listing/", text or "")), "blocked": [m for m in BLOCK_MARKERS if m in low]}


def glassdoor(plain=fetch, reader=None, tinyfish_fetch=None):
    """D99: can the runner read a Glassdoor search page at all, and by which route? Per route and URL: status, bytes, job markup and link counts, and which
    bot-wall phrases appear. Counts and flags only; nothing is stored. Decides whether a crawl is possible or the alert-email path (JFM-197) is the only one."""
    from lifeos.platform import egress, tinyfish                                            # noqa: PLC0415
    reader = reader or egress.reader_proxy
    tinyfish_fetch = tinyfish_fetch or tinyfish.fetch_many
    out = {}
    for n, url in enumerate(GLASSDOOR_URLS, 1):
        routes = {}
        try:
            got = plain(url, timeout=20, max_hops=3)
            routes["plain"] = _glassdoor_facts(got.html, got.status)
        except Exception as error:                                                           # noqa: BLE001 - a probe route must never stop the probe
            routes["plain"] = {"error": type(error).__name__}
        try:
            got = reader(url)
            routes["reader_proxy"] = _glassdoor_facts(got.html, got.status)
        except Exception as error:                                                           # noqa: BLE001
            routes["reader_proxy"] = {"error": type(error).__name__}
        try:
            results, errors = tinyfish_fetch([url], fmt="html", links=True)
            item = results.get(url) or next(iter(results.values()), None) or {}
            text = item.get("html") or item.get("content") or item.get("text") or ""
            facts = _glassdoor_facts(text, item.get("status") or (200 if text else 0))
            facts["fetch_errors"] = len(errors)
            facts["links"] = len(item.get("links") or [])
            routes["tinyfish_fetch"] = facts
        except Exception as error:                                                           # noqa: BLE001
            routes["tinyfish_fetch"] = {"error": type(error).__name__}
        out[f"page{n}"] = routes
    return out


REVOLUT_DIRECT = ("https://www.revolut.com/careers/position/c7078b70-e10b-4f47-b983-bbe6d08d098a/",
                  "https://www.revolut.com/careers/position/campaign-creative-lead-92db1b5f-56bc-4524-bc8b-5f1d2188ae1a/")


def _next_data(html):
    script = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.I | re.S)
    try:
        return json.loads(script.group(1)) if script else None
    except ValueError:
        return None


def revolut(fetch_page=None):
    """Why are two live Revolut roles missing from the listing page V7 reads? Counts, key NAMES and yes/no only: the page's own inventory size,
    whether the two known roles are in it, what else the page carries (names of its props and any count-like numbers), and what each direct
    role page offers (JSON-LD JobPosting, its own __NEXT_DATA__ shape)."""
    from lifeos.platform import impersonate                                                  # noqa: PLC0415
    fetch_page = fetch_page or impersonate.fetch
    source = next(s for s in registry.load(registry.PATHS["Scale-Up"]) if s["id"] == "su-revolut-ltd")
    listing = fetch_page(source["url"], warm_url=source.get("warm_url"), alt_urls=source.get("alt_urls", ()), must_contain=source.get("must_contain", ""))
    out = {"listing_status": listing.status, "listing_bytes": len(listing.html)}
    data = _next_data(listing.html)
    props = (data or {}).get("props", {}).get("pageProps", {}) if isinstance(data, dict) else {}
    positions = props.get("positions") if isinstance(props, dict) else None
    out["page_props_keys"] = sorted(props)[:30] if isinstance(props, dict) else None
    out["count_like_numbers"] = {k: v for k, v in props.items() if isinstance(v, int) and not isinstance(v, bool)} if isinstance(props, dict) else {}
    out["positions"] = len(positions) if isinstance(positions, list) else None
    out["position_keys"] = sorted(positions[0])[:25] if isinstance(positions, list) and positions and isinstance(positions[0], dict) else None
    ids = {str(p.get("id")) for p in positions if isinstance(p, dict)} if isinstance(positions, list) else set()
    out["known_roles_in_listing"] = [any(part in i for i in ids) for part in ("c7078b70", "92db1b5f")]
    out["london_positions"] = sum(any("london" in str(l.get("name", "")).lower() for l in p.get("locations", []) if isinstance(l, dict)) for p in positions if isinstance(p, dict)) if isinstance(positions, list) else None
    out["direct"] = []
    for url in REVOLUT_DIRECT:
        page = fetch_page(url, warm_url=source.get("warm_url"))
        blocks = [b for b in JSONLD.findall(page.html) if "JobPosting" in b]
        nd = _next_data(page.html)
        pp = (nd or {}).get("props", {}).get("pageProps", {}) if isinstance(nd, dict) else {}
        out["direct"].append({"status": page.status, "bytes": len(page.html), "jobposting_ld": len(blocks), "next_data": nd is not None,
                              "page_props_keys": sorted(pp)[:25] if isinstance(pp, dict) else None,
                              "title_in_ld": bool(blocks) and '"title"' in blocks[0], "location_in_ld": bool(blocks) and "jobLocation" in blocks[0]})
    return out


REVOLUT_QUERIES = ("Campaign Creative Lead", "Product Owner Crypto", "Product Owner Website", "Head of Product", "Operations Manager", "Partnerships Manager",
                   "Product Designer", "Strategy Operations Manager", "Product Manager")


def revolut_titles(search=None, fetch_many=None, fetch_page=None):
    """Can a title search find Revolut roles the listing page does not carry, and can their text be read? Counts and key NAMES only: per query how many
    revolut.com position pages the search returns and how many are NOT among the listing's ids; then, for a few unlisted ones, what a browser fetch returns."""
    from lifeos.platform import impersonate, tinyfish, tinyfish_search                       # noqa: PLC0415
    search, fetch_many = search or tinyfish_search.search, fetch_many or tinyfish.fetch_many
    fetch_page = fetch_page or impersonate.fetch
    source = next(s for s in registry.load(registry.PATHS["Scale-Up"]) if s["id"] == "su-revolut-ltd")
    data = _next_data(fetch_page(source["url"], warm_url=source.get("warm_url"), alt_urls=source.get("alt_urls", ()), must_contain=source.get("must_contain", "")).html)
    positions = (data or {}).get("props", {}).get("pageProps", {}).get("positions") or []
    listed = {str(p.get("id")) for p in positions if isinstance(p, dict)}
    out, unlisted = {"listing_positions": len(listed), "queries": {}}, []
    for query in REVOLUT_QUERIES:
        try:
            results = search(f"Revolut {query} careers", include_domains=["revolut.com"])
        except Exception as error:                                                          # noqa: BLE001 - a fixed code, never a body
            out["queries"][query] = {"error": str(error)[:60]}
            continue
        pages = [r["url"] for r in results if "/careers/position/" in r.get("url", "")]
        new = [u for u in pages if not any(i and i in u for i in listed)]
        unlisted += [u for u in new if u not in unlisted]
        out["queries"][query] = {"results": len(results), "position_pages": len(pages), "unlisted": len(new),
                                 "title_hit": sum(query.split()[0].casefold() in (r.get("title") or "").casefold() for r in results)}
    out["unlisted_total"] = len(unlisted)
    out["known_found"] = [any(part in u for u in unlisted) for part in ("c7078b70", "92db1b5f")]
    sample = unlisted[:3] + [u for u in REVOLUT_DIRECT if u not in unlisted]
    if sample:
        try:
            got, errors = fetch_many(sample[:10], fmt="html", links=False)
        except Exception as error:                                                          # noqa: BLE001
            got, errors = {}, [{"error": str(error)[:60]}]
        out["fetch"] = [{"status": (item or {}).get("status"), "keys": sorted(item)[:12] if isinstance(item, dict) else None,
                         "text_chars": max((len(v) for v in item.values() if isinstance(v, str)), default=0) if isinstance(item, dict) else 0,
                         "jobposting_ld": sum("JobPosting" in b for b in JSONLD.findall(str((item or {}).get("html") or (item or {}).get("content") or "")))}
                        for item in got.values()]
        out["fetch_errors"] = len(errors)
    return out


def _embed_api(html, url, title=""):
    """Veramed-style Greenhouse embed: the board API's status for the role (a number only)."""
    from lifeos.jobs import enrich                                                           # noqa: PLC0415
    target = enrich._greenhouse_embed(url or "", html or "")
    if not target:
        return None
    board, token = re.search(r"for=([^&]+)", target).group(1), re.search(r"token=(\d+)", target).group(1)
    from lifeos.jobs.resolve import ats_match                                                # noqa: PLC0415
    got = fetch(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{token}", timeout=15, max_hops=2)
    try:
        data = json.loads(got.html)
    except ValueError:
        data = {}
    words = [w for w in ats_match.norm(title or "").split() if len(w) > 2]
    found = set(ats_match.norm(data.get("title") or "").split())
    read = enrich._api_job(target)
    return {"reader": "none" if not read else "closed" if read.get("closed") else "ok", "role": got.status, "title_share": round(sum(w in found for w in words) / len(words), 2) if words else 0,
            "content_chars": len(data.get("content") or ""), "api_title_words": len(found), "stored_title_words": len(words)}


def _replay(url, title):
    """What today's reader makes of the held role's page: its outcome and reason code only."""
    from lifeos.jobs import enrich                                                           # noqa: PLC0415
    try:
        proof = "unproven" if url and enrich.quality.link_problem(url) in enrich.quality.AMBIGUOUS else None          # what the pipeline passes for an ambiguous shape on an employer's own board
        got = enrich.read_page(url, title or "", lane="Scale-Up", proof=proof, first_party=True) if url else {}
    except Exception as error:                                                               # noqa: BLE001 - a probe never stops on one page
        return type(error).__name__
    return f"{got.get('outcome')}:{got.get('reason') or ''}"


def held_first_party(fetcher=fetch, rows=None):
    """Why do Scale-Up roles on their employer's own board sit on HOLD? Replays the link test on each held role's own page (counts and yes/no only):
    page status, whether the job-title words are in the page <title>, in the first 1500 characters of readable text, or anywhere in the HTML, and what the
    page carries (JobPosting JSON-LD, a Greenhouse embed). Reads the store, writes nothing."""
    from lifeos.jobs import store                                                            # noqa: PLC0415
    from lifeos.jobs.resolve import ats_match                                              # noqa: PLC0415
    if rows is None:
        with store.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT source, source_url, title, unresolved_reason FROM v7_jobs WHERE source LIKE 'web:su-%%' AND status='HOLD' LIMIT 200")
            rows = cursor.fetchall()

    def share(words, text):
        hay = set(ats_match.norm(text).split())
        return round(sum(w in hay for w in words) / len(words), 2) if words else 0
    out = {}
    for source, url, title, reason in rows:
        words = [w for w in ats_match.norm(title or "").split() if len(w) > 2]
        page = fetcher(url, timeout=15, max_hops=6) if url else None
        head = (re.search(r"<title[^>]*>(.*?)</title>", page.html or "", re.S | re.I) or [None, ""])[1] if page else ""
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page.html or "")) if page else ""
        html = page.html or "" if page else ""
        out.setdefault(source, []).append({
            "reason": reason, "status": page.status if page else None, "bytes": len(html), "title_in_head": share(words, head),
            "title_in_text_1500": share(words, text[:1500]), "title_in_html": share(words, html), "jobposting_ld": html.count("JobPosting"),
            "greenhouse_embed": "greenhouse" in html.lower(), "replay": _replay(url, title), "url_shape": re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", urlsplit(url or "").path + ("?" + urlsplit(url or "").query if urlsplit(url or "").query else ""))), "embed_api": _embed_api(html, url, title)})
    return out


def run(limit, live):
    return {"revolut": revolut(), "revolut_titles": revolut_titles(), "glassdoor": glassdoor(), "egress_options": egress_options(), "source_pages": source_pages(), "scale_up_listing": scale_up_listing(), "lane_funnel": lane_funnel(), "held_first_party": held_first_party(), "discover_scale_up": discover(), "open_jobs": open_jobs(), "teamtailor": teamtailor(), "dice": dice(), "hiring_pipeline": hiring(), "ledger_target": ledger_target()}
