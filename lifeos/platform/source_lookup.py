"""LIFE-888: canonical source lookup for the governed MCP: a GitHub file or Notion page with revision and freshness, and an agent lookup.

Read only: one exact read of a GitHub file or a Notion page, and one lookup through the agent directory. Authorization is the gate's (mcp_access.call); this module only shapes the answer. Every failure is a fixed word: unknown_source, stale_source, source_unavailable, output_has_secret, bad_request, plus the directory's own (unknown_agent, ambiguous_alias,
broadcast_refused, workspace_not_granted, cross_space_denied, directory_unavailable). A fetcher that is not wired is `source_unavailable`: nothing is guessed or cached. The directory status class comes straight from agent_directory and is never upgraded here."""
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from lifeos.platform import access_policy, agent_directory, ct_stamp

REPO = "jimmarkunas/life-os-v7"                                                      # the one repository a lookup may name (public)
DEFAULT_MAX_AGE = {"github": timedelta(days=90), "notion": timedelta(days=90)}      # a source last edited longer ago than this is stale unless the caller asks for a different limit
MAX_TEXT = 4000
PATH = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}$")
REF = re.compile(r"^(?!.*\.\.)[A-Za-z0-9._/-]{1,100}$")
NOTION_ID = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$")
SECRET = agent_directory.SECRET
_INTERNAL = "0" * 63 + "1"                                                           # the gate already decided; the directory's own policy check sees this one internal reader
_POLICY = access_policy.load(json.dumps({"version": 1, "default": {}, "agents": {_INTERNAL: {"notion": "read"}}}))


class LookupError_(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _ct(when):
    return ct_stamp.when(when)


def read_source(args, fetchers, now):
    """-> a result dict, or raises LookupError_(reason). `fetchers` = {"github": f(path, ref) -> {...}, "notion": f(page_id) -> {...}}; a missing one is source_unavailable."""
    source = args.get("source")
    max_age = args.get("max_age_seconds")
    if max_age is not None and (type(max_age) is not int or not 1 <= max_age <= 31_536_000):
        raise LookupError_("bad_request")
    if source == "github":
        path, ref = args.get("path"), args.get("ref") or "main"
        if not isinstance(path, str) or not PATH.match(path) or not isinstance(ref, str) or not REF.match(ref):
            raise LookupError_("unknown_source")
        got = _fetch(fetchers.get("github"), path, ref)
        ident, revision = {"repo": REPO, "path": path, "ref": ref}, {"commit_sha": got["commit"], "last_change_sha": got["last_change"], "blob_sha": got["blob"], "last_change_at": got["modified"].isoformat()}
    elif source == "notion":
        page = args.get("page_id")
        if not isinstance(page, str) or not NOTION_ID.match(page.lower()):
            raise LookupError_("unknown_source")
        got = _fetch(fetchers.get("notion"), page.lower().replace("-", ""))
        ident, revision = {"page_id": got["id"], "title": got["title"]}, {"last_edited_time": got["modified"].isoformat(), "page_version": None, "page_version_note": "Notion's API gives no page version; last_edited_time is the revision evidence"}
    else:
        raise LookupError_("unknown_source")
    limit = timedelta(seconds=max_age) if max_age else DEFAULT_MAX_AGE[source]
    age = now - got["modified"]
    if age > limit:
        raise LookupError_("stale_source")
    text = got["text"]
    if SECRET.search(text):
        raise LookupError_("output_has_secret")
    return {"source": source, **ident, "revision": revision, "read_at": _ct(now), "freshness": "fresh", "age_seconds": int(age.total_seconds()), "max_age_seconds": int(limit.total_seconds()),
            "content": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}


def _fetch(fetch, *args):
    if fetch is None:
        raise LookupError_("source_unavailable")
    try:
        got = fetch(*args)
    except LookupError_:
        raise
    except Exception:                                                                    # noqa: BLE001 - a connector failure never shows its message
        raise LookupError_("source_unavailable") from None
    if not isinstance(got, dict) or not isinstance(got.get("modified"), datetime) or not isinstance(got.get("text"), str):
        raise LookupError_("source_unavailable")
    return got


def lookup_agent(args, fetch_rows, now, workspace="Personal"):
    """-> {"agent": snapshot row, "status", "reason", ...} for ONE agent by exact Agent ID or unambiguous alias; raises LookupError_(reason). Status is the directory's own; no proof is ever supplied here, so
    `routable-verified` cannot appear: a lookup shows what the catalog says and how fresh it is, not that anyone is online."""
    agent_id, alias = args.get("agent_id"), args.get("alias")
    if fetch_rows is None:
        raise LookupError_("directory_unavailable")
    try:
        directory = agent_directory.load(fetch_rows, _POLICY, _INTERNAL, now)
        found = agent_directory.resolve(directory, now, _POLICY, _INTERNAL, agent_id=agent_id, alias=alias, workspace=workspace)
    except agent_directory.DirectoryError as error:
        raise LookupError_(error.reason) from None
    if found.decision != "allow":
        raise LookupError_(found.reason)
    row = next(r for r in agent_directory.snapshot(directory, now)["entries"] if r["agent_id"] == found.entry.agent_id)
    return {"agent": row, "status": found.status, "reason": found.reason, "read_at": _ct(now), "source": directory.source, "authorizes": [],
            "note": "catalog only: a listed agent is not online, reachable or permitted"}


def github_fetch(opener=urllib.request.urlopen, repo=REPO):
    """The live GitHub reader (public repository, no credential): the commit `ref` names, the last commit that touched the path, and the file at that commit."""
    def get(url):
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "life-os-source-lookup"})
        with opener(request, timeout=10) as reply:
            return json.loads(reply.read())

    def fetch(path, ref):
        base = f"https://api.github.com/repos/{repo}"
        try:
            head = get(f"{base}/commits/{urllib.parse.quote(ref, safe='')}")["sha"]
            last = get(f"{base}/commits?sha={head}&path={urllib.parse.quote(path)}&per_page=1")
            if not last:
                raise LookupError_("unknown_source")
            body = get(f"{base}/contents/{urllib.parse.quote(path)}?ref={head}")
        except urllib.error.HTTPError as error:
            raise LookupError_("unknown_source" if error.code in (404, 422) else "source_unavailable") from None
        if not isinstance(body, dict) or body.get("type") != "file" or body.get("encoding") != "base64":
            raise LookupError_("unknown_source")
        when = datetime.fromisoformat(last[0]["commit"]["committer"]["date"].replace("Z", "+00:00"))
        return {"commit": head, "last_change": last[0]["sha"], "blob": body["sha"], "modified": when, "text": base64.b64decode(body["content"]).decode("utf-8", "replace")}
    return fetch
