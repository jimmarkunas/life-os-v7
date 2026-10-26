"""LIFE-121 (Jim, Oct 26: "Approved, make the PR"): counts-only report of WHY Scale-Up jobs are EXCLUDED_FIT. Reads v7_jobs and v7_job_fit, writes nothing, prints no title, link, company or text.

EXCLUDED_FIT is one status for several causes (a Fit score under the floor, a lane rule such as pay or age, the precision audit). This splits it by stored reason, by Fit band and by
source, so Jim can see how many roles sit just under the floor before anyone changes the Fit model or a gate. Lane reasons are printed only as the fixed codes of
lanes.public_reason; `high_score` lists the jobs scored at or over the floor that are still excluded, by stored reason, lane reason code and Fit exclusion rule."""
import re

from lifeos.jobs import store
from lifeos.jobs.lanes import FIT_FLOOR, NEAR_MISS_FLOOR, REVIEW_FLOOR, public_reason

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
    out = {"total": 0, "by_reason": {}, "by_band": {}, "by_admission": {}, "by_exclusion": {}, "by_band_and_reason": {}, "by_source": {}, "high_score": {}}
    for source, reason, score, admission, admission_reason, exclusion in rows:
        out["total"] += 1
        b, r = band(score), word(reason)
        for table, key in ((out["by_reason"], r), (out["by_band"], b), (out["by_admission"], f"{word(admission)}/{public_reason(admission_reason)}"),
                           (out["by_exclusion"], word(exclusion)), (out["by_band_and_reason"], f"{b}/{r}")):
            table[key] = table.get(key, 0) + 1
        out["by_source"][source] = out["by_source"].get(source, 0) + 1
        if score is not None and score >= FIT_FLOOR:                    # scored at or over the floor and still excluded: the ones worth a second look, by stored reason, lane reason code and Fit rule
            key = f"{r}/{public_reason(admission_reason)}/{word(exclusion)}"
            out["high_score"][key] = out["high_score"].get(key, 0) + 1
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


REVIEW_RULE = "excluded_private_rule"


def review_rows(rows):
    """rows: (company, title, location, link, score, admission_reason, exclusion, reason). -> the high-scoring jobs excluded by Jim's own Fit rules, as tab-separated lines (PRIVATE text:
    only ever printed through sealed_report.seal)."""
    keep = [r for r in rows if r[4] is not None and r[4] >= FIT_FLOOR and public_reason(r[5]) == REVIEW_RULE]
    keep.sort(key=lambda r: (str(r[7] or ""), str(r[6] or ""), str(r[0] or ""), str(r[1] or "")))
    clean = lambda v: " ".join(str(v if v is not None else "").split())
    return ["\t".join(clean(v) for v in (r[0], r[1], r[2], r[3], r[4], r[6], r[7])) for r in keep]


def review(limit, live, connect=store.connect, seal=None):
    """LIFE-121 (Jim, Oct 26: "Yes, list the 59 in Notion"): the Scale-Up jobs scored at or over the Fit floor that Jim's own exclusion rules removed, sealed for Jim. Read-only. The log carries the count and one
    sealed block (ciphertext); the text itself is never printed."""
    from lifeos.jobs import sealed_report
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT j.company, j.title, j.location_text, COALESCE(j.final_apply_url, j.source_url), f.score, f.admission_reason, f.exclusion, j.unresolved_reason FROM v7_jobs j "
                       "JOIN v7_job_fit f ON f.job_id = j.id WHERE j.source LIKE 'web:su-%%' AND j.status='EXCLUDED_FIT' AND f.score >= %s", (FIT_FLOOR,))
        lines = review_rows(cursor.fetchall())
    header = "company\ttitle\tlocation\tlink\tfit\trule\tstored_reason"
    print((seal or sealed_report.seal)("\n".join([header, *lines])))
    return {"rows": len(lines), "sealed": True}
