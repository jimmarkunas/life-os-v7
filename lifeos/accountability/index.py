"""The two Notion index pages the V3 accountability stages read: the review index (child 02) and the artifact index (child 05) of the V3 master (LIFE-840).

A page is found by its V3 block ID register row (`index.reviews`, `index.artifacts`) and accepted only with its expected title; a missing row or page gives None and the stage says so. Reads only.
Review bullet: `<Person> | <Qn YYYY> | current or past`, the page link on the first piece. Artifact bullet: `<Owner> | <Title> | <file id> | <Jira keys> | <Doc or Sheet> | <verified or unresolved> | <date>`, the Google link on the Title.
"""
from urllib.parse import quote

from lifeos.accountability import region

TITLES = {"index.reviews": "V3 quarterly review index", "index.artifacts": "V3 artifact index"}
SEP = " | "


def _links(block):
    inner = block.get(block.get("type")) or {}
    out = []
    for piece in inner.get("rich_text") or []:
        url = piece.get("href") or ((piece.get("text") or {}).get("link") or {}).get("url") or (((piece.get("mention") or {}).get("page") or {}).get("id") and "https://www.notion.so/" + piece["mention"]["page"]["id"].replace("-", ""))
        if url:
            out.append(url)
    return out


def rows(notion, key):
    """-> [(cells, first link)] for every bullet of the index page `key`, or None when the register row or the page is missing or titled otherwise."""
    page_id = region.register_row(notion, key)
    if page_id is None:
        return None
    page = notion.call("GET", f"/pages/{quote(page_id, safe='')}")
    if not isinstance(page, dict) or page.get("in_trash") or page.get("archived") or region._title(page).strip() != TITLES[key]:
        return None
    out = []
    for block in region.children_of(notion, page_id):
        if block.get("type") == "bulleted_list_item":
            out.append(([c.strip() for c in region.text(block).split(SEP)], next(iter(_links(block)), None)))
    return out


def reviews(notion, person):
    """-> [(label, url)] of the person's review records, the current quarter first then older ones as indexed; None when the index is not set up."""
    found = rows(notion, "index.reviews")
    if found is None:
        return None
    mine = [(cells, url) for cells, url in found if len(cells) == 3 and cells[0].lower() == person.lower() and url]
    mine.sort(key=lambda row: row[0][2] != "current")
    return [(f"{cells[1]}" + (" (current)" if cells[2] == "current" else ""), url) for cells, url in mine]


def artifacts(notion, person):
    """-> [{title, url, file_id, keys, kind, state}] of the person's verified artifacts in index order, plus the count of unresolved ones; None when the index is not set up."""
    found = rows(notion, "index.artifacts")
    if found is None:
        return None
    mine, unresolved = [], 0
    for cells, url in found:
        if len(cells) < 6 or cells[0].lower() != person.lower():
            continue
        if cells[5] == "verified" and url:
            mine.append({"title": cells[1], "url": url, "file_id": cells[2], "keys": [k for k in cells[3].split(",") if k], "kind": cells[4], "state": cells[5]})
        else:
            unresolved += 1
    return mine, unresolved
