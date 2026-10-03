"""Reed job-alert cards. Reed course links are intentionally outside this parser."""
import re

from lifeos.sources.newsletters.parsers import _anchors
from lifeos.sources.newsletters.parsers.lensa import Card

JOB = re.compile(r"(?:https?://)?(?:www\.)?reed\.co\.uk/jobs/[^/?#]+/(\d+)(?:[/?#\"'&\s]|$)", re.I)
NOISE = re.compile(r"^(?:view (?:job|details)|apply(?: now)?|learn more)$", re.I)


def looks_like_jobs(html):
    return bool(JOB.search(html or ""))


def parse_counted(html, received_epoch=None):
    """Return (cards, skipped); only complete linked job cards are emitted."""
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
        # Reed alert cards place the role first and employer second.
        title, company = texts[0], texts[1]
        if not title or not company:
            skipped += 1
            continue
        location = next((t.removeprefix("Location:").strip() for t in texts[2:] if t.lower().startswith("location:")), None)
        salary = next((t.removeprefix("Salary:").strip() for t in texts[2:] if t.lower().startswith("salary:")), None)
        cards.append(Card(company=company, title=title, salary_text=salary, location_text=location,
                          url=anchor["href"], age_days=None))
    return cards, skipped


def parse(html):
    return parse_counted(html)[0]
