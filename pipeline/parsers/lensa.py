"""Lensa job-alert emails: each job is a tracked-link card (company, title, salary estimate, location).

Handles both the digest ("jobalert@...") and the "career advocate" layouts: every card is an
<a href=".../f/a/...">(contains a <table>) whose text nodes are [company, title, salary?, location...].
Card links are per-recipient tracking redirects; they are returned as-is and resolved in a later stage.
"""
from html.parser import HTMLParser
import re

CARD_HOST = re.compile(r"^https?://[a-z0-9-]*email(?:\.[a-z0-9]+)?\.lensa\.com/")   # email./sg3email./email.mg3. tracking hosts
AGE = re.compile(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", re.I)
AGE_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}
SALARY = re.compile(r"\$\s*[\d.,]+\s*[KkMm]?(?:\s*-\s*\$\s*[\d.,]+\s*[KkMm]?)?\s*/\s*(?:yr|hr|year|hour)", re.I)


class Card:
    __slots__ = ("company", "title", "salary_text", "location_text", "url", "age_days")

    def __init__(self, company, title, salary_text, location_text, url, age_days=None):
        self.company, self.title, self.salary_text, self.location_text, self.url, self.age_days = (
            company, title, salary_text, location_text, url, age_days)


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards, self._cur, self._depth = [], None, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href") or ""
        if tag == "a":
            if self._cur is not None and href == self._cur["href"]:
                self._depth += 1                      # nested title link of the same card
            elif CARD_HOST.match(href):
                self._close()                         # sloppy HTML: a new card ends the previous one
                self._cur, self._depth = {"href": href, "texts": [], "tables": 0}, 1
            elif self._cur is not None:
                self._depth += 1
        elif tag == "table" and self._cur is not None:
            self._cur["tables"] += 1

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self._depth -= 1
            if self._depth <= 0:
                self._close()

    def _close(self):
        if self._cur is not None:
            self.cards.append(self._cur)
            self._cur = None

    def close(self):
        super().close()
        self._close()

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and self._cur is not None:
            self._cur["texts"].append(text.replace("․", "."))  # Lensa uses a one-dot leader in "Inc."


def parse(html):
    """Return a list of Card, one per distinct job link, in email order."""
    collector = _Collector()
    collector.feed(html)
    collector.close()
    cards, seen = [], set()
    for raw in collector.cards:
        texts = [t for t in raw["texts"] if t not in ("›", "•")]
        if not raw["tables"] or len(texts) < 2 or raw["href"] in seen:
            continue
        seen.add(raw["href"])
        salary_index = next((i for i, t in enumerate(texts) if SALARY.search(t)), None)
        tail = texts[salary_index + 1:] if salary_index is not None else texts[2:]
        age_days, rest = None, []
        for text in tail:
            found = AGE.search(text)
            if found and age_days is None and len(text) < 40:
                age_days = int(found.group(1)) * AGE_DAYS[found.group(2).lower()]
            else:
                rest.append(text)
        cards.append(Card(company=texts[0], title=texts[1],
                          salary_text=texts[salary_index] if salary_index is not None else None,
                          location_text=" / ".join(rest) or None, url=raw["href"], age_days=age_days))
    return cards
