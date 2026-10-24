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


_COUNTRY = {"united states", "united states of america", "usa", "us"}
_PREFIX = {"north", "south", "east", "west", "fort", "ft", "port", "saint", "st", "lake", "mount", "mt", "new", "old", "upper", "lower", "west", "el", "la", "los", "las"}   # "South San Francisco" is not San Francisco


def same_place(location, url, other_location=""):
    """LIFE-64 (D205 refinement): a place named on a link with no vacancy id ("Chicago, IL") is the place of an ATS vacancy when its city and its
    two-letter region, in order, appear in that vacancy's link path or its own location. Only "City, ST" is a place: a state on its own ("New York,
    United States"), a bare city ("San Francisco"), Remote or nothing never matches."""
    parts = [norm(x) for x in (location or "").split(",") if norm(x)]
    parts = parts[:-1] if parts and parts[-1] in _COUNTRY else parts
    if len(parts) != 2 or not re.fullmatch(r"[a-z]{2}", parts[1]) or "remote" in parts[0].split():
        return False
    place, haystack = f"{parts[0]} {parts[1]}".split(), f"{norm(urlsplit(url or '').path)} {norm(other_location)}".split()
    n = len(place)
    return any(haystack[i:i + n] == place and (i == 0 or haystack[i - 1] not in _PREFIX) for i in range(len(haystack) - n + 1))


def aggregator_twin(url, location, ats_url, ats_location, aggregator):
    """True when `url` is an aggregator link with no vacancy id and `ats_url` names a vacancy at the same place: one opening seen twice (LIFE-64)."""
    return aggregator(url) and vacancy_id(url) is None and vacancy_id(ats_url) is not None and same_place(location, ats_url, ats_location)
