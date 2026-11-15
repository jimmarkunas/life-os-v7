"""Stage `accountability-review` (LIFE-671): Quarterly review, this quarter's progress and one closing line per past quarter."""
from lifeos.accountability import common, goals, read, region, render


def _build(client, block_id, person, project, jira, now, live, site):
    year, n, _, _ = read.quarter(now.date())
    current, _ = goals.lanes(jira, person, year, n)
    stamp = render.stamp(now)
    text = f"Q{n} {year} (current): " + render.progress(current)
    past = []
    py, pn = read.previous(year, n)
    closed, keys = goals.lanes(jira, person, py, pn)
    if closed:
        past.append(render.closing(py, pn, closed))
    out = region.write_review(client, block_id, stamp, text, past, live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"Quarterly review ({person})", stamp, [text]), live)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("review", _build, environ, jira, notion, now, live)
