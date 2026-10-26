"""LIFE-121 (Jim, Oct 26: "Approved, make the PR"): counts-only report of WHY Scale-Up jobs are EXCLUDED_FIT. Reads v7_jobs and v7_job_fit, writes nothing, prints no title, link, company or text.

EXCLUDED_FIT is one status for several causes (a Fit score under the floor, a lane rule such as pay or age, the precision audit). This splits it by stored reason, by Fit band and by
source, so Jim can see how many roles sit just under the floor before anyone changes the Fit model or a gate."""
import re

from lifeos.jobs import store
from lifeos.jobs.lanes import FIT_FLOOR, NEAR_MISS_FLOOR, REVIEW_FLOOR

FIXED = re.compile(r"[a-z][a-z_ ]{0,39}")
TOP_SOURCES = 8


def band(score):
    """The Fit band of a stored score: at or over the floor, near miss (D100), review range (D84), or below it; None = never scored."""
    if score is None:
        return "unscored"
    if score >= FIT_FLOOR:
        return f"{FIT_FLOOR}_plus"
    if score >= NEAR_MISS_FLOOR:
        return f"{NEAR_MISS_FLOOR}_to_{FIT_FLOOR - 1}"
    if score >= REVIEW_FLOOR:
        return f"{REVIEW_FLOOR}_to_{NEAR_MISS_FLOOR - 1}"
    return f"under_{REVIEW_FLOOR}"


def word(text):
    """A stored reason as a fixed word: digits dropped, anything that is not plain lower-case words becomes `other`, so no text from a posting can reach the log."""
    cleaned = re.sub(r"\s+", " ", re.sub(r"\d+", "", str(text or "")).strip().lower()).strip(" _")
    return cleaned if FIXED.fullmatch(cleaned) else ("none" if not cleaned else "other")


def tally(rows):
    """rows: (source, unresolved_reason, score, admission, admission_reason, exclusion). -> the counts-only report."""
    out = {"total": 0, "by_reason": {}, "by_band": {}, "by_admission": {}, "by_exclusion": {}, "by_band_and_reason": {}, "by_source": {}}
    for source, reason, score, admission, admission_reason, exclusion in rows:
        out["total"] += 1
        b, r = band(score), word(reason)
        for table, key in ((out["by_reason"], r), (out["by_band"], b), (out["by_admission"], f"{word(admission)}/{word(admission_reason)}"),
                           (out["by_exclusion"], word(exclusion)), (out["by_band_and_reason"], f"{b}/{r}")):
            table[key] = table.get(key, 0) + 1
        out["by_source"][source] = out["by_source"].get(source, 0) + 1
    out["by_source"] = dict(sorted(out["by_source"].items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_SOURCES])
    out["near_floor"] = sum(n for k, n in out["by_band"].items() if k == f"{NEAR_MISS_FLOOR}_to_{FIT_FLOOR - 1}")
    return out


def run(limit, live, connect=store.connect):
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT j.source, j.unresolved_reason, f.score, f.admission, f.admission_reason, f.exclusion FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id = j.id "
                       "WHERE j.source LIKE 'web:su-%%' AND j.status='EXCLUDED_FIT'")
        report = tally(cursor.fetchall())
        cursor.execute("SELECT status, COUNT(*) FROM v7_jobs WHERE source LIKE 'web:su-%%' GROUP BY status")
        report["status_totals"] = {s: n for s, n in cursor.fetchall()}
    report["floor"], report["near_miss_floor"], report["review_floor"] = FIT_FLOOR, NEAR_MISS_FLOOR, REVIEW_FLOOR
    return report
