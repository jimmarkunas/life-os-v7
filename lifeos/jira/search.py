"""Every issue a JQL matches, read through Jira's enhanced search API (token paging, bounded). Read only. Errors are fixed codes."""
from lifeos.platform.jira import JiraError

PAGE = 100
MAX_PAGES = 30


def search(jira, jql, fields):
    out, token = [], None
    for _ in range(MAX_PAGES):
        params = {"jql": jql, "fields": fields, "maxResults": PAGE, **({"nextPageToken": token} if token else {})}
        data = jira.get("/rest/api/3/search/jql", params)
        issues = data.get("issues") if isinstance(data, dict) else None
        if not isinstance(issues, list):
            raise JiraError("JM_DASH_JIRA_BAD_PAGE")
        out.extend(issues)
        token = data.get("nextPageToken")
        if data.get("isLast", not token) or not token:
            return out
    raise JiraError("JM_DASH_JIRA_TOO_MANY_PAGES")
