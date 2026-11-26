"""Read-only check that the Jobs Notion integration can read the synthetic sandbox Job Ledger.

Manual only; never on a tick. Exactly three reads against fixed sandbox ids (the data source, a one-row query, the page
holding it); there is no write path and --live is refused.
Outputs fixed codes and counts only ("200", or Notion's HTTP and error code), never a title, id, token or content. Any
refused read fails the step with NOTION_SANDBOX_ACCESS_DENIED and its codes still printed."""
import os

from lifeos.platform.notion_ids import notion_id
from lifeos.platform.notion_client import Client, NotionError

SANDBOX_SOURCE = notion_id("SANDBOX_SOURCE")       # "SANDBOX — Job Ledger layout test (synthetic only)"
SANDBOX_PAGE = notion_id("SANDBOX_PAGE")             # "🧪 TEST — Job Ledger Apply UI · ExampleCo (SYNTHETIC)", the top-level private page holding it


def _code(error):
    return " ".join(part for part in (str(error)[:40], error.detail) if part)


def run(limit, live, environ=os.environ, client=None):
    if live:
        raise NotionError("NOTION_SANDBOX_ACCESS_READ_ONLY")
    client = client or Client({"NOTION_API_TOKEN": environ.get("NOTION_API_TOKEN") or "", "SANDBOX_SOURCE": SANDBOX_SOURCE}, source_name="SANDBOX_SOURCE")
    counts = {"notion_sandbox_access": "read_only", "data_source": "", "query": "", "rows": 0, "page": "", "failed": 0}
    reads = (("data_source", lambda: client.call("GET", f"/data_sources/{SANDBOX_SOURCE}")),
             ("query", lambda: client.query_data_source(SANDBOX_SOURCE, {"page_size": 1})),
             ("page", lambda: client.get_page(SANDBOX_PAGE)))
    for name, read in reads:
        try:
            body = read()
        except NotionError as error:
            counts[name], counts["failed"] = _code(error), counts["failed"] + 1
            continue
        counts[name] = "200"
        if name == "query":
            counts["rows"] = len(body.get("results") or []) if isinstance(body, dict) else 0
    return counts
