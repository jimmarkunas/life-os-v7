"""Jobright alert emails. Card link: jobright.ai/jobs/info/<id>. Text nodes, in order:
[company, industry, '. seniority', match, '%', TITLE, salary?, location?, experience, 'N minutes ago', ...].
The provider's match % is deliberately NOT carried (provider scores count zero toward fit)."""
import re

from pipeline.parsers import _anchors
from pipeline.parsers.lensa import AGE, AGE_DAYS, Card

JOB = re.compile(r"jobright\.ai/jobs/info/([0-9A-Za-z]+)")
MONEY = re.compile(r"\$\s*[\d.,]+\s*[KkMm]?\s*/\s*(?:yr|hr|year|hour)", re.I)
NOT_LOCATION = re.compile(r"referral|experience|years?|apply|first applicants|^\W*$", re.I)


def parse(html):
    seen, cards = set(), []
    for anchor in _anchors.collect(html, JOB):
        job_id = JOB.search(anchor["href"]).group(1)
        texts = anchor["texts"]
        if job_id in seen or "%" not in texts or texts.index("%") + 1 >= len(texts) or texts.index("%") < 2:
            continue
        seen.add(job_id)
        marker = texts.index("%")
        company, title = texts[0], texts[marker + 1]
        detail = [t.lstrip("· ").strip() for t in texts[1:marker - 1] if t.strip("· ")]
        tail = texts[marker + 2:]
        salary = next((t for t in tail if MONEY.search(t)), None)
        age_text = next((t for t in tail if AGE.search(t) and len(t) < 40), None)
        location = next((t for t in tail if t != salary and t != age_text and not NOT_LOCATION.search(t)), None)
        age_days = int(AGE.search(age_text).group(1)) * AGE_DAYS[AGE.search(age_text).group(2).lower()] if age_text else None
        cards.append(Card(company=company, title=title, salary_text=salary,
                          location_text=" / ".join(x for x in ([location] + detail) if x) or None,
                          url=f"https://jobright.ai/jobs/info/{job_id}", age_days=age_days))
    return cards
