"""NET-1a: measure the connections file attached to a private Notion page, by count only.  python -m lifeos.run network-inspect

Read-only by construction: it never writes to Notion or the database, creates no table, and prints counts and the file's export date, never a
name, company, address or link. `live` has no effect. It exists to prove the door (V7 can read the file) and the file's real shape
(columns found, blanks, duplicates, employer-less companies) before anything is stored."""
import os
from datetime import date

from lifeos.platform.notion_client import Client

from lifeos.network import parse, source
from lifeos.network.errors import NetworkError


def run(limit, live, environ=os.environ, client=None, opener=None, today=None):
    page_id = source.clean_page_id(environ.get("NETWORK_HANDOFF_PAGE_ID"))
    client = client or Client(environ, source_name="NETWORK_HANDOFF_PAGE_ID")
    blocks = source.csv_file_blocks(client, page_id)
    if not blocks:
        raise NetworkError("NETWORK_NO_FILE")
    chosen = source.newest(blocks)
    text = source.download(chosen["url"], *([opener] if opener else []))
    rows, malformed = parse.read_rows(text)
    counts = parse.analyse(rows, malformed)
    counts["csv_files_on_page"] = len(blocks)
    exported = source.export_date(chosen["name"])
    if exported:
        counts["export_date"] = exported
        counts["export_age_days"] = ((today or date.today()) - date.fromisoformat(exported)).days
    return counts
