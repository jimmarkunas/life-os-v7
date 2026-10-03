"""Dice job-alert cards. Only linked Dice job-detail cards with a title and company are accepted."""
import re

from lifeos.sources.newsletters.parsers import _anchors
from lifeos.sources.newsletters.parsers.lensa import Card

JOB = re.compile(r"(?:https?://)?(?:www\.)?dice\.com/(?:job-detail|jobs/detail)/([A-Za-z0-9_-]+)(?:[/?#\"'&\s]|$)", re.I)
NOISE = re.compile(r"^(?:view (?:job|details)|apply(?: now)?|learn more)$", re.I)


def looks_like_jobs(html):
    return bool(JOB.search(html or ""))


def parse_counted(html):
    """Return (cards, skipped); malformed Dice job links are counted and never guessed."""
    cards, seen, skipped = [], set(), 0
    for anchor in _anchors.collect(html, JOB):
        match = JOB.search(anchor["href"])
        job_id = match.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        texts = [" ".join(text.split()) for text in anchor["texts"]]
        texts = [text for text in texts if text and not NOISE.fullmatch(text)]
        if len(texts) < 2:
            skipped += 1
            continue
        # Dice alert cards place the title first and employer second. Required fields are
        # taken only from those slots; optional details are not inferred.
        title, company = texts[0], texts[1]
        if not title or not company:
            skipped += 1
            continue
        salary = next((t.removeprefix("Salary:").strip() for t in texts[2:] if t.lower().startswith("salary:")), None)
        location = next((t.removeprefix("Location:").strip() for t in texts[2:] if t.lower().startswith("location:")), None)
        cards.append(Card(company=company, title=title, salary_text=salary, location_text=location,
                          url=anchor["href"], age_days=None))
    return cards, skipped


def parse(html):
    return parse_counted(html)[0]
