"""A fake Notion block store for region writers (Jira card, and any later Bills/Calendar region): GET lists the children, DELETE removes one,
PATCH of a block replaces its paragraph. Anything else is a test failure."""


class FakeBlocks:
    def __init__(self, kids):
        self.kids, self.log, self.n = list(kids), [], 100

    def call(self, method, path, body=None):
        self.log.append(method)
        if method == "GET":
            return {"results": [dict(k) for k in self.kids], "has_more": False}
        if method == "DELETE":
            bid = path.rsplit("/", 1)[1]
            self.kids = [k for k in self.kids if k["id"] != bid]
            return {}
        if method == "PATCH" and path.startswith("/blocks/") and "children" not in path:
            for k in self.kids:
                if k["id"] == path.rsplit("/", 1)[1]:
                    k["paragraph"] = body["paragraph"]
            return {}
        raise AssertionError(path)

    def call_once(self, method, path, body=None):
        self.log.append("APPEND")
        if path != "/blocks/card-1/children":
            return {"results": [self._made(b) for b in body["children"]]}
        made = []
        for b in body["children"]:
            made.append(self._made(b))
            self.kids.append(made[-1])
        return {"results": made}

    def _made(self, b):
        self.n += 1
        t = b["type"]
        return {"id": f"b{self.n}", "type": t, t: {"rich_text": [{"plain_text": r["text"]["content"]} for r in b[t]["rich_text"]]}}


class DataSourcePages:
    """Scripted synthetic Notion data-source pages for complete-read and failure tests."""

    def __init__(self, *pages):
        self.pages, self.requests = list(pages), []

    def query_data_source(self, source_id=None, body=None):
        self.requests.append((source_id, dict(body or {})))
        if not self.pages:
            raise AssertionError("unexpected extra Notion query")
        page = self.pages.pop(0)
        if isinstance(page, BaseException):
            raise page
        return page
