"""JOBS-1.1A one-time audit, second run (Jim-approved queries D and E only): counts-only, read-only SELECTs on the Jobs working store. Writes nothing; prints no company, title, link or text.

D: company labels that look like a job-board host (the pattern Enrich uses), by source family and by which part of the pattern matched.
E: jobs whose status is EXCLUDED_FIT while their stored admission is ADMIT or REVIEW, by lane, admission, age of the score (UTC) and whether the job's reason is lane_exclude."""
from lifeos.jobs import store

QUERIES = {
    "board_host_labels_by_family_and_pattern": (
        "SELECT CASE WHEN j.source = 'web:openjobs' THEN 'openjobs' WHEN j.source LIKE 'web:%%' THEN 'other_web' ELSE SUBSTRING_INDEX(COALESCE(j.source,'-'),':',1) END source_family, "
        "CASE WHEN j.company REGEXP '(^| )(wd[0-9]+|myworkdayjobs)( |$)' THEN 'workday_slug' WHEN j.company REGEXP '(^| )(com|io|net|org)( |$)' THEN 'host_word' ELSE 'co_only' END label_pattern, "
        "(j.notion_page_id IS NOT NULL) published, COUNT(*) n, COUNT(DISTINCT j.company) distinct_labels "
        "FROM v7_jobs j WHERE j.company REGEXP '(^| )(com|io|net|org|co|wd[0-9]+|myworkdayjobs)( |$)' GROUP BY 1,2,3 ORDER BY n DESC LIMIT 60"),
    "excluded_fit_with_admit_or_review": (
        "SELECT COALESCE(f.lane,'-') lane, f.admission, (f.scored_at < (UTC_TIMESTAMP() - INTERVAL 24 HOUR)) scored_over_24h_ago, (j.unresolved_reason = 'lane_exclude') reason_lane_exclude, COUNT(*) n "
        "FROM v7_job_fit f JOIN v7_jobs j ON j.id = f.job_id WHERE j.status = 'EXCLUDED_FIT' AND f.admission IN ('ADMIT','REVIEW') GROUP BY 1,2,3,4 ORDER BY 1,2,3,4"),
}


def run(limit, live):
    out = {}
    with store.connect() as connection, connection.cursor() as cursor:
        for name, sql in QUERIES.items():
            cursor.execute(sql)
            out[name] = [[(int(v) if isinstance(v, bool) else v) for v in row] for row in cursor.fetchall()]
    return out
