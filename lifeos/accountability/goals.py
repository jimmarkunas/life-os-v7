"""Stage `accountability-goals` (LIFE-671): Quarterly goals for Jim and Matt, done / all tasks under each quarter epic."""
from lifeos.accountability import common, read, region, render


def lanes(jira, person, year, n):
    """-> ([(lane, done, total)], epic keys of this person)."""
    first, last = read.quarter_bounds(year, n)
    mine = [e for e in read.epics(jira, year, n) if e["person"] == person]
    counts = read.tally(jira, mine, first, last)
    return [(e["lane"], *counts[e["key"]]) for e in mine], [e["key"] for e in mine]


def _build(client, block_id, person, project, jira, now, live):
    year, n, _, _ = read.quarter(now.date())
    found, keys = lanes(jira, person, year, n)
    stamp = render.stamp(now)
    if found:
        extra = f"{read.unparented(jira, project, set(keys))} open tasks are not under a quarter epic."
    else:
        extra = render.NO_EPICS
    values = {name: render.goal_cell(done, total) for name, done, total in found}
    out = region.write_goals(client, block_id, stamp, values, extra, live)
    page = out.pop("summary_page")
    out["epics"] = len(found)
    out["summary_written"] = region.write_summary(client, page, render.summary(f"Quarterly goals Q{n} {year} ({person})", stamp, [render.progress(found), extra]), live)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("goals", _build, environ, jira, notion, now, live)
