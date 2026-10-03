"""Dice alert cards linked through elinks.dice.com tracking redirects."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re

from lifeos.sources.newsletters.parsers.lensa import Card

JOB_LINK = re.compile(r"^https?://elinks\.dice\.com/a/sc/", re.I)
ALERT_TEXT = re.compile(r"job alert|new matches", re.I)
POSTED = re.compile(r"Posted:\s*(\d{2}-\d{2}-\d{4})", re.I)
DECOYS = {"log in", "view all jobs", "manage your daily job alert >", "dice knowledge center (faqs)",
          "unsubscribe", "terms & conditions"}


class _Events(HTMLParser):
    """A flat stream of eligible title links and following paragraphs, independent of table nesting."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events, self.link, self.paragraph = [], None, None
        self.strong_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            href = attrs.get("href") or ""
            if JOB_LINK.match(href):
                self.link = {"href": href, "text": []}
        elif tag == "p":
            self.paragraph = {"text": [], "strong_text": []}
        elif tag == "strong":
            self.strong_depth += 1

    def handle_endtag(self, tag):
        if tag == "a" and self.link is not None:
            title = " ".join(" ".join(self.link["text"]).split())
            if title:
                self.events.append(("link", self.link["href"], title))
            self.link = None
        elif tag == "p" and self.paragraph is not None:
            text = " ".join(" ".join(self.paragraph["text"]).split())
            strong = " ".join(" ".join(self.paragraph["strong_text"]).split())
            self.events.append(("p", text, strong))
            self.paragraph = None
        elif tag == "strong" and self.strong_depth:
            self.strong_depth -= 1

    def handle_data(self, data):
        if self.link is not None:
            self.link["text"].append(data)
        if self.paragraph is not None:
            self.paragraph["text"].append(data)
            if self.strong_depth:
                self.paragraph["strong_text"].append(data)


def looks_like_jobs(html):
    text = html or ""
    return bool(ALERT_TEXT.search(text) or re.search(r"https?://elinks\.dice\.com/a/sc/", text, re.I))


def parse_counted(html, received_epoch=None):
    """Return (cards, skipped). Mail date is an epoch in UTC when available."""
    parser = _Events()
    parser.feed(html or "")
    parser.close()
    received_date = (datetime.fromtimestamp(received_epoch, timezone.utc).date()
                     if received_epoch is not None else None)
    cards, skipped, seen = [], 0, set()
    for i, event in enumerate(parser.events):
        if event[0] != "link" or event[2].casefold() in DECOYS:
            continue
        end = next((j for j in range(i + 1, len(parser.events)) if parser.events[j][0] == "link"), len(parser.events))
        paragraphs = [entry for entry in parser.events[i + 1:end] if entry[0] == "p"]
        company_index = next((j for j, p in enumerate(paragraphs) if p[2] and p[1]), None)
        company = paragraphs[company_index][2] if company_index is not None else None
        next_p = company_index + 1 if company_index is not None else None
        location = paragraphs[next_p][1] if next_p is not None and next_p < len(paragraphs) else None
        posted_match = next((POSTED.search(p[1]) for p in paragraphs[company_index + 1:] if POSTED.search(p[1])), None) \
            if company_index is not None else None
        age_days = None
        if posted_match and received_date:
            try:
                posted_date = datetime.strptime(posted_match.group(1), "%m-%d-%Y").date()
                if posted_date <= received_date:
                    age_days = (received_date - posted_date).days
            except ValueError:
                pass
        title = event[2]
        if not company or not location:
            skipped += 1
            continue
        identity = (" ".join(title.casefold().split()), " ".join(company.casefold().split()))
        if identity in seen:
            continue
        seen.add(identity)
        cards.append(Card(company=company, title=title, salary_text=None, location_text=location,
                          url=event[1], age_days=age_days))
    return cards, skipped


def parse(html):
    return parse_counted(html)[0]
