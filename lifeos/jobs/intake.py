"""The one way a producer adds a job to Jobs OS. A source emits a normalized job here and stops; dedupe, repost
linking and everything downstream (resolve, enrich, quality, publish) belong to Jobs OS."""
from lifeos.jobs import identity, repost


def add_job(cursor, job, now):
    """job: url, status, title, company, location, salary, source, provider, lane, age_days, received, provider_score.
    Returns (dedupe_key, is_new). A repeat of the same URL bumps seen_count; the same opening under a new URL
    becomes a DUPLICATE of the original (D6)."""
    key = identity.url_hash(job["url"])
    fuzzy = identity.fuzzy_key(job["company"], job["title"], job["location"])
    cursor.execute(
        "INSERT IGNORE INTO v7_jobs (dedupe_key, status, title, company, location_text, source_url, salary_text,"
        " source, fuzzy_key, posted_age_days, mail_received_at, provider_score, first_seen, last_seen, updated_at,"
        " lane, provider) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (key, job["status"], job["title"][:300], job["company"][:200], (job["location"] or "")[:200], job["url"],
         (job["salary"] or "")[:80], job["source"], fuzzy, job["age_days"], job["received"], job["provider_score"],
         now, now, now, job["lane"], job["provider"]))
    is_new = bool(cursor.rowcount)
    if is_new:
        repost.link(cursor, key, fuzzy, now)
    else:
        cursor.execute("UPDATE v7_jobs SET seen_count=seen_count+1, last_seen=%s WHERE dedupe_key=%s", (now, key))
    return key, is_new
