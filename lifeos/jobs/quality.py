"""Precision guards: is this URL a single job page, and is this text a real job description?
Pure functions. Found by the first live Notion batch (2 of 3 pages were wrong: an apply-form template and a listing page)."""
import re
from urllib.parse import parse_qs, urlsplit

GENERIC_LAST = {"search", "jobs", "job", "careers", "career", "openings", "positions", "vacancies", "join-us", "join",
                "apply", "application", "all", "listings", "opportunities", "work-with-us", "teams", "locations"}
ATS_FORM_SUFFIX = ("breezy.hr", "lever.co", "workable.com", "ashbyhq.com", "recruitee.com", "applytojob.com", "jobvite.com")
PLACEHOLDER = re.compile(r"%[A-Z][A-Z0-9_]{4,}%")
LISTING_HINT = re.compile(r"(\b\d+ Locations\b|\bHybrid Remote,|\bRemote,\s*$|\bOn-?site,\s*$)", re.M)
JD_WORDS = re.compile(r"responsibilit|qualification|requirement|experience|you will|you'll|we are looking|about the (role|job|"
                      r"position|team)|skills|benefits|what you|who you are", re.I)


def canonical_job_url(url):
    """Apply-FORM urls (.../apply) -> the job page that carries the description."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in ATS_FORM_SUFFIX):
        segs = parts.path.rstrip("/").split("/")
        if len(segs) > 2 and segs[-1].lower() in ("apply", "application"):
            return parts._replace(path="/".join(segs[:-1]), query="", fragment="").geturl()
    return url


JOB_ID_PARAMS = {"gh_jid", "jid", "job_id", "jobid", "jobref", "job_ref", "job", "ashby_jid", "lever_jid", "req", "req_id", "reqid", "requisition",
                 "requisition_id", "posting", "posting_id", "vacancy", "vacancy_id", "opening", "opening_id", "position_id", "positionid", "id"}


HOST_ID_PARAMS = {"greenhouse.io": {"token"},                     # boards.greenhouse.io/embed/job_app?for=<board>&token=<job id>: Enrich reads this form
                  "eightfold.ai": {"pid"}}                        # <company>.eightfold.ai/careers?pid=<position id>


def _id_in_query(query, host=""):
    """True when a query parameter that names a job carries an id (a digit, at least three characters): /careers?gh_jid=123 is one vacancy."""
    names = set(JOB_ID_PARAMS)
    for suffix, extra in HOST_ID_PARAMS.items():
        if host == suffix or host.endswith("." + suffix):
            names |= extra
    return any(key.lower() in names and any(len(v) >= 3 and re.search(r"\d", v) for v in values) for key, values in query.items())


def url_problem(url):
    """None for a plausible single-job URL, else a fixed code."""
    parts = urlsplit(url or "")
    segs = [s for s in parts.path.split("/") if s]
    query = parse_qs(parts.query)
    if any(k in query for k in ("p", "page", "q", "keyword", "keywords", "query", "search")):
        return "listing_url"
    if _id_in_query(query, (parts.hostname or "").lower()):
        return None                                  # the id may live in the query (Greenhouse embeds on an employer's own /careers page)
    if not segs:
        return "root"
    if segs[-1].lower() in GENERIC_LAST or "search" in (s.lower() for s in segs):
        return "listing_url"
    last = segs[-1]
    has_id = bool(re.search(r"\d", "".join(segs))) or bool(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", last))
    slug = len([w for w in re.split(r"[-_]", last) if w]) >= 3
    return None if (has_id or slug) else "no_job_id"


AMBIGUOUS = ("listing_url", "no_job_id")        # shape cannot say: a vacancy or a portal page. Only the page itself can (title proof). A root never qualifies.

WORKABLE_NO_ACCOUNT = re.compile(r"^https?://apply\.workable\.com/j/[^/]+/?$", re.I)


def link_problem(url):
    """None for a link that can be a single vacancy, else a fixed code. The same test Enrich opens with, so a link that would be rejected
    there is never marked RESOLVED here (it would only bounce NEW -> RESOLVED -> NEW)."""
    url = canonical_job_url(url)
    if WORKABLE_NO_ACCOUNT.match(url or ""):
        return "workable_no_account"
    return url_problem(url)


def jd_problem(text):
    """None for a real description, else 'template' | 'listing' | 'thin'."""
    text = text or ""
    if len(PLACEHOLDER.findall(text)) >= 3 or text.count("{{") >= 2:
        return "template"
    if len(LISTING_HINT.findall(text)) >= 5 or re.search(r"\d+-\d+ of \d+", text):
        return "listing"
    if len(text) < 400 or not JD_WORDS.search(text):
        return "thin"
    return None
