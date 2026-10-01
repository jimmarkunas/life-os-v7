"""Jobright alert emails. Card link: jobright.ai/jobs/info/<id>. Text nodes, in order:
[company, industry, '. seniority', match, '%', TITLE, salary?, location?, experience, 'N minutes ago', ...].
The provider's match % is stored as evidence only (provider_score); it counts zero toward fit until Phase 2 decides a weight."""
import re

from pipeline.parsers import _anchors
from pipeline.parsers.lensa import AGE, AGE_DAYS, Card

JOB = re.compile(r"jobright\.ai/jobs/info/([0-9A-Za-z]+)")
MONEY = re.compile(r"\$\s*[\d.,]+\s*[KkMm]?\s*/\s*(?:yr|hr|year|hour)", re.I)
DOT = "\u00b7"
MATCH = re.compile(r"\d{1,3}\s*%")
NOT_LOCATION = re.compile(r"referral|experience|years?|apply|first applicants|^\W*$", re.I)


def looks_like_jobs(html):
    """True when the email links to job pages at all (distinguishes a parse gap from a non-job email)."""
    return bool(JOB.search(html or ""))


def parse(html):
    seen, cards = set(), []
    for anchor in _anchors.collect(html, JOB):
        job_id = JOB.search(anchor["href"]).group(1)
        texts = anchor["texts"]
        marker = next((i for i, t in enumerate(texts) if t == "%" or MATCH.fullmatch(t)), None)
        if job_id in seen or marker is None or marker < 1 or marker + 1 >= len(texts):
            continue
        seen.add(job_id)
        company, title = texts[0], texts[marker + 1]
        score_text = texts[marker - 1] if texts[marker] == "%" else texts[marker]
        score = int(re.sub(r"\D", "", score_text)) if re.search(r"\d", score_text) else None
        detail_end = marker - 1 if texts[marker] == "%" else marker      # instant: '89','%' | digest: '80%'
        detail = [t.lstrip(DOT + " ").strip() for t in texts[1:detail_end] if t.strip(DOT + " ")]
        tail = texts[marker + 2:]
        salary = next((t for t in tail if MONEY.search(t)), None)
        age_text = next((t for t in tail if AGE.search(t) and len(t) < 40), None)
        location = next((t for t in tail if t != salary and t != age_text and not NOT_LOCATION.search(t)), None)
        age_days = int(AGE.search(age_text).group(1)) * AGE_DAYS[AGE.search(age_text).group(2).lower()] if age_text else None
        cards.append(Card(company=company, title=title, salary_text=salary,
                          location_text=" / ".join(x for x in ([location] + detail) if x) or None,
                          url=f"https://jobright.ai/jobs/info/{job_id}", age_days=age_days, provider_score=score))
    return cards
