"""Direct job readers for ATS pages that are JavaScript shells. The job URL already names the tenant and the job, so each
reader asks the ATS's public JSON (or the JSON embedded in the page) for that one job: no browser, no daily cap.
Endpoint shapes follow open-jobs (CC0) and career-ops (MIT). read(url) -> dict(title, html, posted) | {"closed": True} | None.
"""
import html as htmllib
import json
import re
from urllib.parse import quote, urlsplit

from lifeos.platform import limits
from lifeos.platform.http import fetch

LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2,4})?$")


_last = {"status": 0}
_misses = {}


def _fetch(url, **kwargs):
    page = fetch(url, **kwargs)
    _last["status"] = page.status
    return page


def _get_json(url):
    page = _fetch(url, timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2, headers={"Accept": "application/json"})
    if page.status == 403:                       # some tenants refuse a browser user agent on their JSON endpoint
        page = _fetch(url, timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2,
                      headers={"Accept": "application/json", "User-Agent": "lifeos-jobs/0.1"})
    if page.status in (404, 410):
        return "gone"
    if page.status != 200:
        return None
    try:
        return json.loads(page.html)
    except ValueError:
        return None


def _job(title, html, posted):
    return {"title": title, "html": html or "", "posted": str(posted or "")[:10]}


def workday_cxs_url(url):
    """The CXS JSON URL for a Workday job URL (locale and any /apply part removed), or None."""
    parts = urlsplit(url or "")
    host, segs = (parts.hostname or "").lower(), [s for s in parts.path.split("/") if s]
    if not host.endswith("myworkdayjobs.com"):
        return None
    if segs and LOCALE.match(segs[0]):
        segs = segs[1:]
    if "apply" in segs:
        segs = segs[:segs.index("apply")]        # the apply form lives under the job path; the job is what precedes it
    if len(segs) < 3 or segs[1] != "job":
        return None
    return f"https://{host}/wday/cxs/{host.split('.')[0]}/{segs[0]}/{'/'.join(segs[1:])}"


def parse_workday(text):
    """A CXS answer (raw JSON, or JSON wrapped in page markup by a browser fetch) -> dict(title, html, posted) | None."""
    start, end = (text or "").find("{"), (text or "").rfind("}")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    info = data.get("jobPostingInfo") if isinstance(data, dict) else None
    return _job(info.get("title"), info.get("jobDescription"), info.get("startDate")) if info else None


def workday(parts):
    """https://{tenant}.wd{N}.myworkdayjobs.com[/{locale}]/{site}/job/... -> /wday/cxs/{tenant}/{site}/job/..."""
    api = workday_cxs_url(f"https://{parts.hostname}{parts.path}")
    if not api:
        return None
    data = _get_json(api)
    if data == "gone":
        return {"closed": True}
    info = (data or {}).get("jobPostingInfo") if isinstance(data, dict) else None
    return _job(info.get("title"), info.get("jobDescription"), info.get("startDate")) if info else None


def workable(parts):
    """https://apply.workable.com/{account}/j/{SHORTCODE}/ -> the account widget (all published jobs, with descriptions)."""
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 3 or segs[1] != "j":
        return None
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{quote(segs[0])}?details=true")
    if data == "gone":
        return {"closed": True}
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return None
    for job in jobs:
        if str(job.get("shortcode", "")).lower() == segs[2].lower():
            return _job(job.get("title"), job.get("description"), job.get("published_on"))
    return {"closed": True}                      # the account lists every published job; this one is not among them


def bamboohr(parts):
    """https://{slug}.bamboohr.com/careers/{id} -> /careers/{id}/detail"""
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2 or segs[0] != "careers" or not segs[1].isdigit():
        return None
    data = _get_json(f"https://{parts.hostname}/careers/{segs[1]}/detail")
    if data == "gone":
        return {"closed": True}
    opening = ((data or {}).get("result") or {}).get("jobOpening") if isinstance(data, dict) else None
    return _job(opening.get("jobOpeningName"), opening.get("description"), opening.get("datePosted")) if opening else None


def oraclecloud(parts):
    """https://{host}.oraclecloud.com/.../job/{id} -> recruitingCEJobRequisitionDetails?q=Id={id}"""
    match = re.search(r"/job/(\d+)", parts.path)
    if not match:
        return None
    url = (f"https://{parts.hostname}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
           f"?onlyData=true&q=Id={match.group(1)}")
    data = _get_json(url)
    if data == "gone":
        return {"closed": True}
    if not isinstance(data, dict):
        return None
    items = data.get("items") or []
    if not items:
        return {"closed": True}                  # unknown or closed requisitions answer with an empty list
    item = items[0]
    body = "\n\n".join(item.get(k) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                              "ExternalQualificationsStr") if item.get(k))
    return _job(item.get("Title"), body, item.get("ExternalPostedStartDate"))


def paylocity(parts):
    """The detail page carries a JSON-LD JobPosting whose description is entity-encoded HTML. (Its datePosted is the
    render time, so it is ignored.)"""
    page = _fetch(f"https://{parts.hostname}{parts.path}", timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=3)
    if page.status in (404, 410) or "JobNotFound" in page.final_url:
        return {"closed": True}
    match = re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', page.html, re.S) if page.status == 200 else None
    if not match:
        return None
    try:
        posting = json.loads(match.group(1))
    except ValueError:
        return None
    return _job(posting.get("title"), htmllib.unescape(posting.get("description") or ""), None) if isinstance(posting, dict) else None


def ukg(parts):
    """UKG / UltiPro job boards embed the opportunity JSON in the page; read its Description string."""
    page = _fetch(f"https://{parts.hostname}{parts.path}" + (f"?{parts.query}" if parts.query else ""),
                 timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=3)
    if page.status in (404, 410):
        return {"closed": True}
    match = re.search(r'"Description":"((?:[^"\\]|\\.)*)"', page.html) if page.status == 200 else None
    if not match:
        return None
    try:
        return _job(None, json.loads(f'"{match.group(1)}"'), None)
    except ValueError:
        return None


def smartrecruiters(parts):
    """https://jobs.smartrecruiters.com/{company}/{id}[-slug] -> api.smartrecruiters.com/v1/companies/{company}/postings/{id}"""
    segs = [s for s in parts.path.split("/") if s]
    match = re.match(r"(\d+)", segs[1]) if len(segs) >= 2 else None
    if not match:
        return None
    data = _get_json(f"https://api.smartrecruiters.com/v1/companies/{quote(segs[0])}/postings/{match.group(1)}")
    if data == "gone":
        return {"closed": True}
    if not isinstance(data, dict):
        return None
    sections = ((data.get("jobAd") or {}).get("sections") or {})
    body = "\n\n".join((sections.get(k) or {}).get("text") or "" for k in
                        ("jobDescription", "qualifications", "additionalInformation"))
    return _job(data.get("name"), body, data.get("releasedDate"))


def ashby(parts):
    """https://jobs.ashbyhq.com/{org}/{id} -> the org's public job board (every published job, with descriptionHtml)."""
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        return None
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(segs[0])}")
    if data == "gone":
        return {"closed": True}
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return None
    for job in jobs:
        if str(job.get("id", "")).lower() == segs[1].lower():
            return _job(job.get("title"), job.get("descriptionHtml"), job.get("publishedAt"))
    return {"closed": True}


READERS = (("myworkdayjobs.com", workday), ("apply.workable.com", workable), ("bamboohr.com", bamboohr),
           ("oraclecloud.com", oraclecloud), ("recruiting.paylocity.com", paylocity), ("ultipro.com", ukg), ("ukg.com", ukg),
           ("smartrecruiters.com", smartrecruiters), ("ashbyhq.com", ashby))


def _shape(path):
    """Counts-only shape of a URL path (never the words in it): loc = locale, n = digits, w = any other word."""
    out = []
    for seg in (x for x in path.split("/") if x):
        out.append("loc" if LOCALE.match(seg) else "n" if seg.isdigit() else seg if seg in KEYWORDS else "w")
    return "/".join(out)


KEYWORDS = {"job", "jobs", "j", "careers", "details", "sites", "apply"}


def misses():
    """Reader failures since the last call, as counts: {'workday:http_406:w/job/w/w': n}. Fixed codes and path shapes only."""
    out = dict(_misses)
    _misses.clear()
    return out


def read(url):
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    for suffix, reader in READERS:
        if host == suffix or host.endswith("." + suffix):
            _last["status"] = 0
            try:
                result = reader(parts)
            except Exception:                    # noqa: BLE001 - a reader must never take the stage down
                result = None
            if result is None:
                key = f"{suffix}:http_{_last['status']}:{_shape(parts.path)}"
                _misses[key] = _misses.get(key, 0) + 1
            return result
    return None
