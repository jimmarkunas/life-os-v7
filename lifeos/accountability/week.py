"""Stage `accountability-week` (LIFE-671): This week for Jim and Matt on V3; tickets link to Jira, open and done apart.

LIFE-918 AC3: with the Runtime Configuration row V3_WEEK_VIEW exactly "on" (off by default, fail closed, lifeos.accountability.switches), BOTH boards get the approved weekly sprint view as native blocks
(tiles, grouped lists, Done toggle; lifeos.accountability.weekview) and read the new Jira view; otherwise the stage is exactly as it was."""
import os

from lifeos.accountability import common, read, region, render, switches, weekview


def _build(client, block_id, person, project, jira, now, live, site, notion):
    today = now.date()
    parts, lines = render.week_view(read.week(jira, project, today), today, site)
    stamp = render.stamp(now)
    out = region.write_parts(client, block_id, stamp, render.fit(parts), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"This week ({person})", stamp, lines), live)
    return out


def _build_v3(client, block_id, person, project, jira, now, live, site, notion):
    today = now.date()
    week = read.week(jira, project, today, v3_week_view=True)
    _, lines = render.week_view(week, today, site)                                   # the plain lines the summary page keeps, from the same data
    stamp = render.stamp(now)
    out = region.write_tree(client, block_id, stamp, weekview.spec(week, today, site, project, now), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"This week ({person})", stamp, lines), live)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    environ = os.environ if environ is None else environ
    return common.run_region("week", _build_v3 if switches.on("V3_WEEK_VIEW", environ) else _build, environ, jira, notion, now, live)
