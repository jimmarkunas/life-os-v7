"""Lensa job-alert emails: each job is a tracked-link card (company, title, salary estimate, location).

Handles both the digest ("jobalert@...") and the "career advocate" layouts: every card is an
<a href=".../f/a/...">(contains a <table>) whose text nodes are [company, title, salary?, location...].
Card links are per-recipient tracking redirects; they are returned as-is and resolved in a later stage.
"""
from html.parser import HTMLParser
import re

CARD_HOST = "email.lensa.com/f/a/"
SALARY = re.compile(r"\$\s*[\d.,]+\s*[KkMm]?\s*-\s*\$\s*[\d.,]+\s*[KkMm]?")


class Card:
    __slots__ = ("company", "title", "salary_text", "location_text", "url")

    def __init__(self, company, title, salary_text, location_text, url):
        self.company, self.title, self.salary_text, self.location_text, self.url = (
            company, title, salary_text, location_text, url)


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards, self._cur, self._depth = [], None, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            if self._cur is None and CARD_HOST in (attrs.get("href") or ""):
                self._cur, self._depth = {"href": attrs["href"], "texts": [], "tables": 0}, 1
            elif self._cur is not None:
                self._depth += 1
        elif tag == "table" and self._cur is not None:
            self._cur["tables"] += 1

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self._depth -= 1
            if self._depth == 0:
                self.cards.append(self._cur)
                self._cur = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and self._cur is not None:
            self._cur["texts"].append(text.replace("․", "."))  # Lensa uses a one-dot leader in "Inc."


def parse(html):
    """Return a list of Card, one per distinct job link, in email order."""
    collector = _Collector()
    collector.feed(html)
    cards, seen = [], set()
    for raw in collector.cards:
        texts = [t for t in raw["texts"] if t not in ("›", "•")]
        if not raw["tables"] or len(texts) < 2 or raw["href"] in seen:
            continue
        seen.add(raw["href"])
        salary_index = next((i for i, t in enumerate(texts) if SALARY.search(t)), None)
        tail = texts[salary_index + 1:] if salary_index is not None else texts[2:]
        cards.append(Card(company=texts[0], title=texts[1],
                          salary_text=texts[salary_index] if salary_index is not None else None,
                          location_text=" / ".join(tail) or None, url=raw["href"]))
    return cards
