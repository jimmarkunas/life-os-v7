"""First-party careers-page readers for the Scale-Up sponsors that have no public ATS API.

Ported (smallest proven mechanics only) from the V2 Scale-Up acquirer: each reader takes the page HTML and returns job dicts
(id, title, location, url, posted, content). A reader that cannot PROVE the inventory raises ValueError: the lister turns that into FAILED,
never into 'zero jobs' and never into a removal. A page that says explicitly it has no vacancies is the only empty COMPLETE listing."""
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

IGNORE = re.compile(r"(privacy|login|sign.?in|cookie|benefit|culture|people|about|contact|connect|alert|talent.?community)", re.I)
NON_JOB = re.compile(r"^(careers?|jobs?( explore jobs)?|current openings?|open positions?|see open positions?|view open roles?|view career openings?|view job|apply|apply now|join us|opportunities|get in touch\.?)$", re.I)
JOBISH = re.compile(r"(job|career|position|vacanc|opening|role|apply)", re.I)
ATS_HOSTS = ("ashbyhq.com", "greenhouse.io", "lever.co", "workdayjobs.com", "teamtailor.com", "join.com", "workable.com", "pinpointhq.com", "rippling.com")


def job(ident, title, location, url, content=None):
    return {"id": str(ident), "title": (title or "").strip(), "location": (location or "").strip(), "url": url or "", "posted": None, "content": content}


def _sid(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def strip_html(value):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.scripts, self.headings = [], [], []
        self._href = self._heading = None
        self._parts, self._heading_parts, self._script_parts = [], [], []
        self._script, self._script_type = False, ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self._href, self._parts = attrs["href"], []
        if tag == "script":
            self._script, self._script_type, self._script_parts = True, attrs.get("type", ""), []
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading, self._heading_parts = tag, []

    def handle_data(self, data):
        if self._href:
            self._parts.append(data)
        if self._script:
            self._script_parts.append(data)
        if self._heading:
            self._heading_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(" ".join(self._parts).split())))
            self._href = None
        if tag == "script" and self._script:
            if self._script_type.casefold() == "application/ld+json":
                self.scripts.append("".join(self._script_parts))
            self._script, self._script_type, self._script_parts = False, "", []
        if tag == self._heading:
            text = " ".join(" ".join(self._heading_parts).split())
            if text:
                self.headings.append((tag, text))
            self._heading, self._heading_parts = None, []


def parse_page(text):
    page = _Page()
    page.feed(text)
    return page


def _jsonld_jobs(page, source):
    rows, seen = [], set()

    def walk(value):
        if isinstance(value, dict):
            kind = value.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                url = value.get("url") or source["url"]
                ident = value.get("identifier")
                ident = ident.get("value") if isinstance(ident, dict) else ident
                if url not in seen:
                    rows.append(job(ident or _sid(url), value.get("title"), None, url))
                    seen.add(url)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for raw in page.scripts:
        try:
            walk(json.loads(raw))
        except (TypeError, ValueError):
            pass
    return rows


def _path_jobs(text, source, pattern, reject=None):
    page = parse_page(text)
    rows = _jsonld_jobs(page, source)
    seen = {r["url"] for r in rows}
    rx = re.compile(pattern, re.I)
    for href, label in page.links:
        url = urljoin(source["url"], href)
        path = urlparse(url).path.rstrip("/")
        title = " ".join(label.split())
        if not rx.fullmatch(path) or len(title) < 3 or NON_JOB.fullmatch(title) or (reject and reject.search(title)) or url in seen:
            continue
        rows.append(job(_sid(url), title, None, url))
        seen.add(url)
    return rows


STREAM_SLUG = re.compile(r"^(?P<role>.+)-(?P<city>london|new-york|arlington|manchester|dublin|edinburgh)-\d+$", re.I)


def _stream(text, source):
    """Stream lists "<role> <city> <department>" as one label and says nothing else about the place; its URL slug carries the city
    (/careers/delivery-manager-london-8459831). The role keeps its own words, the place becomes the job's location (D111)."""
    rows = _path_jobs(text, source, PATH_KINDS["stream_html"])
    for row in rows:
        m = STREAM_SLUG.match(urlparse(row["url"]).path.rstrip("/").rsplit("/", 1)[-1])
        if not m:
            continue
        city = m.group("city").replace("-", " ").title()
        label = row["title"]
        cut = re.search(r"\b" + re.escape(city) + r"\b", label, re.I)
        if cut and label[:cut.start()].strip():
            row["title"] = label[:cut.start()].strip(" -–—")
        row["location"] = row["location"] or city
    return rows


def _generic(text, source):
    page = parse_page(text)
    rows = _jsonld_jobs(page, source)
    seen = {r["url"] for r in rows}
    base_host = urlparse(source["url"]).netloc.replace("www.", "")
    for href, label in page.links:
        title = " ".join(label.split())
        if not JOBISH.search(f"{href} {title}") or IGNORE.search(title) or NON_JOB.fullmatch(title):
            continue
        url = urljoin(source["url"], href)
        host = urlparse(url).netloc.replace("www.", "")
        if host and base_host and host != base_host and not any(x in host for x in ATS_HOSTS):
            continue
        if len(title) >= 3 and url not in seen:
            rows.append(job(_sid(url), title, None, url))
            seen.add(url)
    return rows


def _join(text, source):
    page = parse_page(text)
    base_path = urlparse(source["url"]).path.rstrip("/")
    rows = _jsonld_jobs(page, source)
    seen = {r["url"] for r in rows}
    for href, label in page.links:
        url = urljoin(source["url"], href)
        path = urlparse(url).path.rstrip("/")
        title = " ".join(label.split())
        if path.startswith(base_path + "/") and len(title) >= 3 and not NON_JOB.fullmatch(title) and url not in seen:
            rows.append(job(_sid(url), title, None, url))
            seen.add(url)
    return rows


def _bluestonex(text, source):
    rows, seen = [], set()
    for href, label in parse_page(text).links:
        title = " ".join(label.split())
        if "full-time more information" not in title.casefold():
            continue
        url = urljoin(source["url"], href)
        if url not in seen:
            rows.append(job(_sid(url), title, None, url))
            seen.add(url)
    return rows


def _sixflow(text, source):
    plain = strip_html(text)
    zero = re.search(r"we don['’]?t have any live vacancies right now", plain, re.I)
    rows, seen = [], set()
    for href, label in parse_page(text).links:
        url = urljoin(source["url"], href)
        path = urlparse(url).path.rstrip("/")
        title = " ".join(label.split())
        if not re.fullmatch(r"/careers/(?!future-opportunities$)[^/]+", path, re.I) or len(title) < 3 or NON_JOB.fullmatch(title) or url in seen:
            continue
        rows.append(job(_sid(url), title, None, url))
        seen.add(url)
    if zero and rows:
        raise ValueError("contradictory zero and live vacancy evidence")
    return [] if zero else rows or _fail("inventory is ambiguous")


def _fail(message):
    raise ValueError(message)


def _futuristic(text, source):
    if "rjjobportal-careers-wrapper" not in text:
        raise ValueError("careers portal wrapper not found")
    rows, seen = [], set()
    for part in text.split('<div class="rjjobportal-job-item"')[1:]:
        title_m = re.search(r'rjjobportal-job-title">([^<]+)</span>', part, re.I)
        meta = {strip_html(k): strip_html(v) for k, v in re.findall(r'rjjobportal-meta-label">([^<]+)</span>\s*<span class="rjjobportal-meta-val">(.*?)</span>', part, re.I | re.S)}
        ref = meta.get("Job Reference Number")
        if not title_m or not ref or ref in seen:
            raise ValueError("vacancy item missing title or reference, or duplicate")
        seen.add(ref)
        title = re.sub(r"^\s*\d+\.\s*", "", strip_html(title_m.group(1))).strip()
        pay = meta.get("Annual Salary")
        rows.append(job(ref, title, meta.get("Location"), source["url"], f"Compensation: {pay}" if pay else None))
    return rows


def _balanced_js_array(text, start):
    depth, quote, escaped = 0, None, False
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in {'"', "'", "`"}:
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("unterminated careers array")


def _doubleword(text, source, fetch_text):
    scripts = [urljoin(source["url"], s) for s in re.findall(r'<script[^>]+src=["\']([^"\']+)', text, re.I) if "/assets/index-" in s and s.endswith(".js")]
    if len(scripts) != 1:
        raise ValueError("expected one first-party app bundle")
    js = fetch_text(scripts[0])
    q = r'"(?:\\.|[^"\\])*"'
    head = re.search(rf'([A-Za-z_$][\w$]*)=\[\{{title:{q},slug:{q},department:{q},type:{q},seniority:{q},location:{q},compensation:', js)
    if not head or f"{head.group(1)}.map" not in js or "Open Positions" not in js:
        raise ValueError("Open Positions array not proven")
    raw = _balanced_js_array(js, js.find("[", head.start()))
    rx = re.compile(rf"\{{title:(?P<title>{q}),slug:(?P<slug>{q}),department:(?P<department>{q}),type:(?P<type>{q}),seniority:(?P<seniority>{q}),location:(?P<location>{q}),compensation:(?P<compensation>{q}),applyEmail:(?P<applyEmail>{q})")
    rows, seen = [], set()
    for match in rx.finditer(raw):
        fields = {k: json.loads(match.group(k)) for k in match.groupdict()}
        if fields["slug"] in seen:
            raise ValueError("duplicate slug")
        seen.add(fields["slug"])
        pay = fields["compensation"]
        rows.append(job(fields["slug"], fields["title"], fields["location"], urljoin(source["url"], f"/careers/{fields['slug']}"), f"Compensation: {pay}" if pay else None))
    if not rows:
        raise ValueError("Open Positions parsed zero roles")
    return rows


def _revolut(text, source):
    script = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.I | re.S)
    if not script:
        raise ValueError("first-party inventory missing")
    payload = json.loads(unescape(script.group(1)))
    positions = payload.get("props", {}).get("pageProps", {}).get("positions")
    if not isinstance(positions, list):
        raise ValueError("positions is not a list")

    def total(value):
        if isinstance(value, dict):
            for key, child in value.items():
                folded = str(key).casefold()
                if isinstance(child, int) and not isinstance(child, bool) and ("count" in folded or "total" in folded) and any(t in folded for t in ("position", "job", "role", "open")):
                    return child
                found = total(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = total(child)
                if found is not None:
                    return found
        return None
    plain = strip_html(text)
    visible = re.search(r"(?:we have|currently have)\s+(\d+)\s+(?:open positions|open roles|positions)|showing\s+\d+\s*(?:[-–]\s*\d+\s+of\s+|of\s+)(\d+)\s+(?:jobs|roles|positions)", plain, re.I)
    count = int(next(g for g in visible.groups() if g is not None)) if visible else total(payload)
    if count is None:
        raise ValueError("authoritative open-position count missing")
    ids = [str(p.get("id") or "") for p in positions if isinstance(p, dict)]
    if len(positions) != count or len(ids) != count or any(not i for i in ids) or len(set(ids)) != count:
        raise ValueError("exhaustive inventory mismatch")
    rows = []
    for p in positions:
        title = strip_html(p.get("text"))
        locs = p.get("locations")
        if not title or not isinstance(locs, list) or any(not isinstance(l, dict) or not l.get("name") for l in locs):
            raise ValueError("position lacks title or locations")
        place = " | ".join(" · ".join(x for x in (strip_html(l.get("name")), strip_html(l.get("type")), strip_html(l.get("country"))) if x) for l in locs)
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
        rows.append(job(p["id"], title, place, f"https://www.revolut.com/en-GB/careers/position/{slug}-{p['id']}/"))
    return rows


def _tg0(text, source):
    page = parse_page(text)
    start = next((i for i, (_, v) in enumerate(page.headings) if v.strip().upper() == "JOIN US"), None)
    if start is None:
        raise ValueError("JOIN US section not found")
    offices = {"LONDON HQ", "BEIJING OFFICE", "HONG KONG OFFICE", "SINGAPORE OFFICE"}
    roles = [v for tag, v in page.headings[start + 1:] if tag == "h3" and v.upper() not in offices and not NON_JOB.fullmatch(v)]
    apply_url = next((urljoin(source["url"], h) for h, label in page.links if "submit your application" in label.casefold()), source["url"])
    if not roles:
        raise ValueError("JOIN US exposed no roles")
    return [job(_sid(v), v, "London / global offices", apply_url) for v in roles]


def _veramed(text, source, fetch_text):
    cards = []
    for raw in re.findall(r"<section\b(?=[^>]*\bdata-role=)[^>]*>.*?</section>", text, re.I | re.S):
        card = parse_page(raw)
        title = next((v for tag, v in card.headings if tag == "h2"), None)
        url = next((urljoin(source["url"], h) for h, _ in card.links if "gh_jid=" in h), None)
        if title or url:
            cards.append((title, url))
    if not cards:
        raise ValueError("no vacancy cards")
    rows = []
    for title, url in cards:
        match = re.search(r"[?&]gh_jid=([^&]+)", url or "")
        if not title or not url or not match:
            raise ValueError("vacancy card missing title or index")
        rows.append(job(match.group(1), title, None, url))
    return rows


# kind -> reader(text, source, fetch_text); `zero_marker` (registry) is the only way an empty page is a COMPLETE zero
PATH_KINDS = {"wttj_html": r"/jobs/[^/]+", "stream_html": r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?careers/[^/]+", "popsa_html": r"/careers/[^/]+"}
READERS = {
    "static_complete_html": lambda t, s, f: _generic(t, s), "rippling_html": lambda t, s, f: _generic(t, s),
    "wttj_html": lambda t, s, f: _path_jobs(t, s, PATH_KINDS["wttj_html"]),
    "stream_html": lambda t, s, f: _stream(t, s),
    "popsa_html": lambda t, s, f: _path_jobs(t, s, PATH_KINDS["popsa_html"]),
    "join_html": lambda t, s, f: _join(t, s), "bluestonex_html": lambda t, s, f: _bluestonex(t, s),
    "sixflow_html": lambda t, s, f: _sixflow(t, s), "futuristic_html": lambda t, s, f: _futuristic(t, s),
    "doubleword_bundle": _doubleword, "revolut_html": lambda t, s, f: _revolut(t, s),
    "tg0_html": lambda t, s, f: _tg0(t, s), "veramed_html": _veramed,
}
SELF_PROVING = {"sixflow_html", "futuristic_html", "doubleword_bundle", "revolut_html", "tg0_html", "veramed_html"}   # these raise on an ambiguous page themselves
