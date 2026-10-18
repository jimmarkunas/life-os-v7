"""Job identity (Jobs OS): the same keys for every producer, so one opening is one row however it arrives."""
from hashlib import sha256
import re
from urllib.parse import parse_qsl, urlsplit


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def url_hash(url):
    """Exact-URL key (dedupe_key)."""
    return sha256(url.encode()).hexdigest()


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_WORKDAY_REQ = re.compile(r"_(J?R[-_]?\d+)(?:-\d+)?$", re.I)


def vacancy_id(url):
    """The ATS's own id for one vacancy when the URL names one, else None (LIFE-59). A Greenhouse job id is global, so its board page (/jobs/N), its
    embed form (?token=N) and an employer careers page (?gh_jid=N) agree; Lever and Ashby name the posting by a UUID; Workday by the requisition."""
    parts = urlsplit((url or "").strip())
    host, segs = (parts.hostname or "").lower(), [s for s in parts.path.split("/") if s]
    query = {k.lower(): v for k, v in parse_qsl(parts.query)}
    if query.get("gh_jid", "").isdigit():
        return "greenhouse:" + query["gh_jid"]
    if host.endswith("greenhouse.io"):
        if query.get("token", "").isdigit():
            return "greenhouse:" + query["token"]
        if len(segs) >= 2 and segs[-2] == "jobs" and segs[-1].isdigit():
            return "greenhouse:" + segs[-1]
    if host in ("jobs.lever.co", "jobs.ashbyhq.com") and len(segs) >= 2 and _UUID.match(segs[1]):
        return host.split(".")[1] + ":" + segs[1].lower()
    if host.endswith(".myworkdayjobs.com") and segs and _WORKDAY_REQ.search(segs[-1]):
        return "workday:" + host.split(".")[0] + ":" + _WORKDAY_REQ.search(segs[-1]).group(1).upper().replace("-", "").replace("_", "")
    return None


def url_key(url):
    """Normalized Apply URL for the ledger: the vacancy's ATS id when the URL names one (vacancy_id: one opening under any of its URL forms),
    else host + path, no query/fragment, lowercase, no trailing slash (unchanged, so a stored key stays valid)."""
    named = vacancy_id(url)
    if named:
        return sha256(("vacancy|" + named).encode()).hexdigest()
    parts = urlsplit((url or "").strip())
    return sha256(((parts.hostname or "") + parts.path.rstrip("/")).lower().encode()).hexdigest()


def fuzzy_key(company, title, location, url=None):
    """Same opening seen under another URL: normalized company + title + location, plus the ATS vacancy id when the link names one (D205), so two
    requisitions with one company, title and place are two openings. A link with no id keeps its stored key."""
    parts = [norm(x) for x in (company, title, location)] + ([vacancy_id(url)] if vacancy_id(url) else [])
    return sha256("|".join(parts).encode()).hexdigest()
