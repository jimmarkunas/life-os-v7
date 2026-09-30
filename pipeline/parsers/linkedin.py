"""LinkedIn job-alert emails (jobalerts-noreply / jobs-noreply). One job = two links (image + text) to
/comm/jobs/view/<id>?<tracking tokens>. The tracking query carries personal tokens, so the returned URL is the
clean canonical https://www.linkedin.com/jobs/view/<id> - tokens are never kept."""
import re

from pipeline.parsers import _anchors
from pipeline.parsers.lensa import Card

JOB = re.compile(r"linkedin\.com/comm/jobs/view/(\d+)")
NOISE = re.compile(r"connection|alumni|school|hiring|actively|easy apply|applicant|promoted|viewed|\bago\b", re.I)


def looks_like_jobs(html):
    return bool(JOB.search(html or ""))


def parse(html):
    by_id, order = {}, []
    for anchor in _anchors.collect(html, JOB):
        job_id = JOB.search(anchor["href"]).group(1)
        if job_id not in by_id:
            by_id[job_id] = []
            order.append(job_id)
        by_id[job_id].extend(anchor["texts"])
    cards = []
    for job_id in order:
        texts = by_id[job_id]
        if len(texts) < 2:
            continue
        title = texts[0]
        company, _, location = next((t.partition(" · ") for t in texts[1:] if " · " in t), (texts[1], "", ""))
        if not title or NOISE.search(company) and not location:
            continue
        cards.append(Card(company=company.strip(), title=title, salary_text=None,
                          location_text=location.strip() or None,
                          url=f"https://www.linkedin.com/jobs/view/{job_id}", age_days=None))
    return cards
