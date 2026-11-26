"""The two Notion index pages the stages read (reviews, artifacts); read only. A page is found by its V3 block ID
register row and accepted only with its expected title, else None. Review bullet: `<Person> | <Qn YYYY> | current
or past → <record page>`. Artifact bullet: `<Owner> | <Title> | <file id> | <Jira keys> | <Doc or Sheet> |
<verified, unresolved or excluded> | <date>`.
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
    found = rows(notion, "index.reviews")
    if found is None:
        return None
    mine = [(cells[0], cells[1], cells[2].split(" ")[0], url) for cells, url in found if len(cells) == 3 and cells[0].lower() == person.lower() and url]
    mine.sort(key=lambda row: row[2] != "current")
    return [(quarter + (" (current)" if status == "current" else ""), url) for _, quarter, status, url in mine]


def artifacts(notion, person):
    """-> ([{title, url, file_id, keys, kind, state}] verified, unresolved count, {excluded file ids}); None when no
    index.
    """
    found = rows(notion, "index.artifacts")
    if found is None:
        return None
    mine, unresolved, excluded = [], 0, set()
    for cells, url in found:
        if len(cells) < 6 or cells[0].lower() != person.lower():
            continue
        if cells[5] == "verified" and url:
            mine.append({"title": cells[1], "url": url, "file_id": cells[2], "keys": [k for k in cells[3].split(",") if k], "kind": cells[4], "state": cells[5]})
        elif cells[5] == "excluded":
            excluded.add(cells[2])
        else:
            unresolved += 1
    return mine, unresolved, excluded
