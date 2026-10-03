"""Dice alert cards linked through elinks.dice.com tracking redirects."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re

from lifeos.sources.newsletters.parsers.lensa import Card

JOB_LINK = re.compile(r"^https?://elinks\.dice\.com/a/sc/", re.I)
ALERT_TEXT = re.compile(r"job alert|new matches", re.I)
POSTED = re.compile(r"Posted:\s*(\d{2}-\d{2}-\d{4})", re.I)
FONT_20 = re.compile(r"font-size\s*:\s*20(?:\.0)?px\b", re.I)
BOLD_STYLE = re.compile(r"font-weight\s*:\s*(?:bold|[7-9]00)\b", re.I)


class _Rows(HTMLParser):
    """Collect only /a/sc links in bold 20px cells, with neighboring row paragraphs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.td_bold_20 = [], None, False
        self.p, self.strong_depth, self.link = None, 0, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row = {"links": [], "paragraphs": []}
        elif tag == "td" and self.row is not None:
            style = attrs.get("style") or ""
            self.td_bold_20 = bool(FONT_20.search(style) and (BOLD_STYLE.search(style) or self.strong_depth))
        elif tag == "p" and self.row is not None:
            self.p = {"text": [], "strong": False}
        elif tag in ("strong", "b"):
            self.strong_depth += 1
        elif tag == "a" and self.row is not None and self.td_bold_20:
            href = attrs.get("href") or ""
            if JOB_LINK.match(href):
                self.link = {"href": href, "text": []}

    def handle_endtag(self, tag):
        if tag == "a" and self.link is not None:
            self.link["title"] = " ".join(" ".join(self.link["text"]).split())
            self.row["links"].append(self.link)
            self.link = None
        elif tag == "p" and self.p is not None:
            self.p["value"] = " ".join(" ".join(self.p["text"]).split())
            self.row["paragraphs"].append(self.p)
            self.p = None
        elif tag in ("strong", "b") and self.strong_depth:
            self.strong_depth -= 1
        elif tag == "td":
            self.td_bold_20 = False
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.p is not None:
            self.p["text"].append(data)
            if self.strong_depth:
                self.p["strong"] = True
        if self.link is not None:
            self.link["text"].append(data)

    def close(self):
        super().close()
        if self.row is not None:
            self.rows.append(self.row)
            self.row = None


def looks_like_jobs(html):
    text = html or ""
    return bool(ALERT_TEXT.search(text) or re.search(r"https?://elinks\.dice\.com/a/sc/", text, re.I))


def parse_counted(html, received_epoch=None):
    """Return (cards, skipped). Mail date is an epoch in UTC when available."""
    parser = _Rows()
    parser.feed(html or "")
    parser.close()
    received_date = (datetime.fromtimestamp(received_epoch, timezone.utc).date()
                     if received_epoch is not None else None)
    cards, skipped, seen = [], 0, set()
    for row_index, row in enumerate(parser.rows):
        if not row["links"]:
            continue
        row_links = [link for link in row["links"] if link["title"].casefold() not in {
            "log in", "view all jobs", "manage your daily job alert >", "dice knowledge center (faqs)",
            "unsubscribe", "terms & conditions"}]
        if not row_links:
            continue
        detail_row = parser.rows[row_index + 1] if row_index + 1 < len(parser.rows) else {}
        posted_row = parser.rows[row_index + 2] if row_index + 2 < len(parser.rows) else {}
        paragraphs = detail_row.get("paragraphs", [])
        company_index = next((i for i, p in enumerate(paragraphs) if p["strong"] and p["value"]), None)
        location = next((p["value"] for p in paragraphs[company_index + 1:] if p["value"]), None) if company_index is not None else None
        company = paragraphs[company_index]["value"] if company_index is not None else None
        posted_text = " ".join(p["value"] for p in posted_row.get("paragraphs", []))
        posted_match = POSTED.search(posted_text)
        age_days = None
        if posted_match and received_date:
            try:
                posted_date = datetime.strptime(posted_match.group(1), "%m-%d-%Y").date()
                if posted_date <= received_date:
                    age_days = (received_date - posted_date).days
            except ValueError:
                pass
        for link in row_links:
            title = link["title"]
            if not title or not company or not location:
                skipped += 1
                continue
            identity = (" ".join(title.casefold().split()), " ".join(company.casefold().split()))
            if identity in seen:
                continue
            seen.add(identity)
            cards.append(Card(company=company, title=title, salary_text=None, location_text=location,
                              url=link["href"], age_days=age_days))
    return cards, skipped


def parse(html):
    return parse_counted(html)[0]
