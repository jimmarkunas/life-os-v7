"""JOBS-1.1A one-time audit (Jim-approved queries A-C): counts-only, read-only SELECTs on the Jobs working store. Writes nothing; prints no company, title, link or text.

A: work mode x admission x job status x published.  B: why jobs got REVIEW/EXCLUDE (digits masked).  C: company labels that look like a job-board host (the pattern Enrich already uses), by source family."""
from lifeos.jobs import store

QUERIES = {
    "work_mode_by_disposition": (
        "SELECT COALESCE(f.lane,'-') lane, COALESCE(f.work_mode,'-') work_mode, COALESCE(f.admission,'-') admission, j.status, (j.notion_page_id IS NOT NULL) published, COUNT(*) n "
        "FROM v7_job_fit f JOIN v7_jobs j ON j.id = f.job_id GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5"),
    "reasons": (
        "SELECT COALESCE(f.work_mode,'-') work_mode, f.admission, REGEXP_REPLACE(COALESCE(f.admission_reason,'-'),'[0-9]+','N') reason, COUNT(*) n "
        "FROM v7_job_fit f WHERE f.admission IN ('REVIEW','EXCLUDE') GROUP BY 1,2,3 ORDER BY n DESC LIMIT 40"),
    "board_host_company_labels": (
        "SELECT SUBSTRING_INDEX(COALESCE(j.source,'-'),':',1) source_family, (j.notion_page_id IS NOT NULL) published, j.status, COUNT(*) n, COUNT(DISTINCT j.company) distinct_labels "
        "FROM v7_jobs j WHERE j.company REGEXP '(^| )(com|io|net|org|co|wd[0-9]+|myworkdayjobs)( |$)' GROUP BY 1,2,3 ORDER BY n DESC LIMIT 60"),
}


def run(limit, live):
    out = {}
    with store.connect() as connection, connection.cursor() as cursor:
        for name, sql in QUERIES.items():
            cursor.execute(sql)
            out[name] = [[(int(v) if isinstance(v, bool) else v) for v in row] for row in cursor.fetchall()]
    return out
