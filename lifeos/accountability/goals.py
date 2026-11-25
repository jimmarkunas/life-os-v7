"""Stage `accountability-goals`: the one Quarterly Goals & Review callout of Jim and of Matt on Jim & Matt V3 (LIFE-671, LIFE-840).

Title with the quarter, the refresh label, one row per quarter Epic (linked to Jira, done / all) and the Quarterly Reviews toggle linking the review pages. This stage is the only writer of that callout.
"""
import os

from lifeos.accountability import common, index, preview, read, region, render, switches


def lanes(jira, person, year, n):
    """-> ([(lane, done, total, epic key)], epic keys of this person)."""
    first, last = read.quarter_bounds(year, n)
    mine = [e for e in read.epics(jira, year, n) if e["person"] == person]
    counts = read.tally(jira, mine, first, last)
    return [(e["lane"], *counts[e["key"]], e["key"]) for e in mine], [e["key"] for e in mine]


def _build(client, block_id, person, project, jira, now, live, site, notion, shadow=False):
    year, n, _, _ = read.quarter(now.date())
    found, keys = lanes(jira, person, year, n)
    stamp = render.stamp(now)
    if found:
        extra = f"{read.unparented(jira, project, set(keys))} open tasks are not under a quarter epic."
    else:
        extra = render.NO_EPICS
    values = {name: (render.goal_cell(done, total), read.issue_url(site, key)) for name, done, total, key in found}
    links = index.reviews(notion, person)
    wide = None
    if shadow:                                                             # LIFE-918: the preview column, inside this same block (no separate block); the legacy figures stay official
        first, last = read.quarter_bounds(year, n)
        mine = [e for e in read.epics(jira, year, n) if e["person"] == person]
        wide = preview.cells(found, preview.reading(jira, person, mine, first, last))
    out = region.write_combined(client, block_id, render.combined_heading(person, year, n), stamp, values, extra, render.review_parts(links), live, wide)
    out["epics"] = len(found)
    out["review_pages"] = len(links or [])
    return out


def _build_shadow(client, block_id, person, project, jira, now, live, site, notion):
    return _build(client, block_id, person, project, jira, now, live, site, notion, shadow=True)


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    environ = os.environ if environ is None else environ
    build = _build_shadow if switches.on("V2_QUARTER_SHADOW", environ) else _build           # LIFE-918: off (the default) is exactly the stage as it was
    return common.run_region("goals", build, environ, jira, notion, now, live)
