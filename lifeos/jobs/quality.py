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


def url_problem(url):
    """None for a plausible single-job URL, else a fixed code."""
    parts = urlsplit(url or "")
    segs = [s for s in parts.path.split("/") if s]
    query = parse_qs(parts.query)
    if not segs:
        return "root"
    if segs[-1].lower() in GENERIC_LAST or "search" in (s.lower() for s in segs):
        return "listing_url"
    if any(k in query for k in ("p", "page", "q", "keyword", "keywords", "query", "search")):
        return "listing_url"
    last = segs[-1]
    has_id = bool(re.search(r"\d", "".join(segs))) or bool(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", last))
    slug = len([w for w in re.split(r"[-_]", last) if w]) >= 3
    return None if (has_id or slug) else "no_job_id"


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
