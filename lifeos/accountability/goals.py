"""Stage `accountability-goals`: the one Quarterly Goals & Review callout of Jim and of Matt on Jim & Matt V3 (LIFE-671, LIFE-840).

Title with the quarter, the refresh label, one row per quarter Epic (linked to Jira, done / all) and the Quarterly Reviews toggle linking the review pages. This stage is the only writer of that callout.
"""
from lifeos.accountability import common, index, read, region, render


def lanes(jira, person, year, n):
    """-> ([(lane, done, total, epic key)], epic keys of this person)."""
    first, last = read.quarter_bounds(year, n)
    mine = [e for e in read.epics(jira, year, n) if e["person"] == person]
    counts = read.tally(jira, mine, first, last)
    return [(e["lane"], *counts[e["key"]], e["key"]) for e in mine], [e["key"] for e in mine]


def _build(client, block_id, person, project, jira, now, live, site, notion):
    year, n, _, _ = read.quarter(now.date())
    found, keys = lanes(jira, person, year, n)
    stamp = render.stamp(now)
    if found:
        extra = f"{read.unparented(jira, project, set(keys))} open tasks are not under a quarter epic."
    else:
        extra = render.NO_EPICS
    values = {name: (render.goal_cell(done, total), read.issue_url(site, key)) for name, done, total, key in found}
    links = index.reviews(notion, person)
    out = region.write_combined(client, block_id, render.combined_heading(person, year, n), stamp, values, extra, render.review_parts(links), live)
    out["epics"] = len(found)
    out["review_pages"] = len(links or [])
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("goals", _build, environ, jira, notion, now, live)
