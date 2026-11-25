"""LIFE-918 AC4 and AC5: the v2 quarterly shadow and its own V3 preview block, off by default.

Through Q4 2026 the legacy (v1) figures stay official; this module only shows what v2 would say beside them. It runs after the goals stage, only when the V2_QUARTER_SHADOW row is exactly "on" AND the
V3_PREVIEW_BLOCK_ID row names a callout on the Jim & Matt V3 page, and only writes when the run is live. It writes inside that one callout (a ScopedClient refuses every other block), never the fixed blocks
or the human regions, and a failure of the preview never fails the goals stage. The shipped frozen membership is empty, so v2 reads DEGRADED with its fixed code until Jim approves a quarter: nothing is
invented. JFM has no v2 (it stays on v1). Counts and fixed codes only; `render_preview` is pure and `python -m lifeos.accountability.preview --synthetic` prints it from fixtures with no network."""
import re
import sys
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from lifeos.accountability import read, region, render, switches
from lifeos.platform import readback, report_region
from lifeos.platform.jira import JiraError
from lifeos.platform.notion_client import NotionError

CHI = ZoneInfo("America/Chicago")
CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
GENERIC = "ACCOUNTABILITY_DEGRADED"


def _code(error):
    """The fixed code of an error, or a generic one: an error text is never shown."""
    text = str(error)
    return text if CODE.match(text) else GENERIC


def figures(jira, now):
    """-> (year, n, rows). One row per quarter lane: person, lane, key, legacy (done, all) from the unchanged v1 reader, and v2 (done, all) from the frozen-membership reader for Matt only.
    A v2 read that fails closed gives v2 None and its fixed code; JFM has no v2. A legacy read that fails raises (the official figures are not this module's to hide)."""
    year, n, _, _ = read.quarter(now.date())
    first, last = read.quarter_bounds(year, n)
    every = read.epics(jira, year, n)
    rows = []
    for person, _project in read.PEOPLE:
        mine = [e for e in every if e["person"] == person]
        if not mine:
            continue
        legacy = read.tally(jira, mine, first, last)
        v2, code = None, None
        if person == "Matt":
            try:
                v2 = read.tally(jira, mine, first, last, mej_metric_version="v2")
            except JiraError as error:
                code = _code(error)
        for e in mine:
            rows.append({"person": person, "lane": e["lane"], "key": e["key"], "legacy": tuple(legacy[e["key"]]),
                         "v2": tuple(v2[e["key"]]) if v2 is not None else None, "code": code, "applies": person == "Matt"})
    return year, n, rows


def _line(row):
    legacy = render.goal_cell(*row["legacy"])
    if not row["applies"]:
        shadow = "no v2 for JFM (stays on legacy)"
    elif row["v2"] is None:
        shadow = f"DEGRADED ({row['code'] or GENERIC})"
    else:
        shadow = render.goal_cell(*row["v2"])
    return f"{row['lane']}: Official (legacy) {legacy} · Preview (v2) {shadow}"


def render_preview(year, n, rows):
    """-> [(text, link, bold)] pieces for the preview body: a one-line statement of what is official, then each person's lanes, official legacy figures beside the preview v2 figures. Pure."""
    parts = [render.p(f"Q{n} {year}: legacy figures are official; the v2 figures are a preview only and change nothing.", None, True), render.NL]
    for person in dict.fromkeys(row["person"] for row in rows):
        parts += [render.NL, render.p(person, None, True), render.NL]
        mine = [row for row in rows if row["person"] == person]
        for i, row in enumerate(mine):
            parts += [render.p(_line(row)), render.NL] if i < len(mine) - 1 else [render.p(_line(row))]
    if not rows:
        parts += [render.NL, render.p(render.NO_EPICS)]
    return parts


def plain(parts):
    return "".join(text for text, _, _ in parts)


def write_preview(client, block_id, stamp, parts, live):
    """The preview callout is exactly: a heading, one gray stamp paragraph and one body paragraph (anything else raises ACCOUNTABILITY_SHAPE and writes nothing). Only the stamp and the body are written,
    read back before success. -> 1 when something changed (or would change when not live), else 0."""
    kids = report_region.children(client, block_id, region.AccountabilityError)
    if len(kids) != 3 or kids[0].get("type") not in region.HEADINGS or kids[1].get("type") != "paragraph" or kids[2].get("type") != "paragraph":
        raise region.fail("ACCOUNTABILITY_SHAPE")
    want = region._normalise([(content, url) for content, url, _ in parts])
    changed = region._set(client, kids[1], stamp, live, "gray")
    if region.signature(kids[2]) != want:
        changed += 1
        if live:
            client.call_once("PATCH", f"/blocks/{quote(kids[2]['id'], safe='')}", {"paragraph": {"rich_text": region._nodes(parts)}})
    if live and changed:
        ok, _ = readback.confirmed(lambda: report_region.children(client, block_id, region.AccountabilityError),
                                   lambda a: len(a) == 3 and region.text(a[1]) == stamp.strip() and region.signature(a[2]) == want)
        if not ok:
            raise region.fail("ACCOUNTABILITY_VERIFY_FAILED")
    return int(bool(changed))


def shadow(environ, jira, notion, now, live):
    """-> None when the shadow is off (nothing was read or written), else counts: {"preview": "no_block"} when no preview block is configured, {"preview": "failed", "code": ...} when it could not be
    read or written, or {"preview": "written"|"unchanged"|"planned", "rows", "degraded"}. Never raises: a preview problem is not the goals stage's failure."""
    if not switches.on("V2_QUARTER_SHADOW", environ):
        return None
    block = switches.preview_block(environ)
    if not block:
        return {"preview": "no_block"}
    try:
        if region._page_of(notion, block) != region.V3_PAGE_TITLE:
            raise region.fail("ACCOUNTABILITY_TARGET_NOT_FOUND")
        year, n, rows = figures(jira, now)
        client = region.scoped(notion, block)
        changed = write_preview(client, block, render.stamp(now), render_preview(year, n, rows), live)
    except (JiraError, NotionError) as error:
        return {"preview": "failed", "code": _code(error)}
    state = ("written" if live else "planned") if changed else "unchanged"
    return {"preview": state, "rows": len(rows), "degraded": sum(1 for row in rows if row["applies"] and row["v2"] is None)}


SYNTHETIC_NOW = datetime(2026, 12, 1, 9, 0, tzinfo=CHI)
SYNTHETIC_ROWS = [
    {"person": "Jim", "lane": "Career", "key": "JFM-9", "legacy": (3, 7), "v2": None, "code": None, "applies": False},
    {"person": "Jim", "lane": "LIFE OS", "key": "JFM-102", "legacy": (5, 9), "v2": None, "code": None, "applies": False},
    {"person": "Matt", "lane": "Career", "key": "MEJ-1", "legacy": (2, 6), "v2": (2, 5), "code": None, "applies": True},
    {"person": "Matt", "lane": "Creative", "key": "MEJ-2", "legacy": (1, 4), "v2": None, "code": "ACCOUNTABILITY_MEJ_MEMBERSHIP_NOT_APPROVED", "applies": True},
]


def main(argv):
    """`--synthetic` prints the rendered preview from fixed fixtures: no Jira, no Notion, no network, no write."""
    if list(argv) != ["--synthetic"]:
        print("usage: python -m lifeos.accountability.preview --synthetic")
        return 2
    print(render.stamp(SYNTHETIC_NOW))
    print(plain(render_preview(2026, 4, SYNTHETIC_ROWS)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
