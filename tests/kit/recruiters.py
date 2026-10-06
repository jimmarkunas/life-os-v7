"""Synthetic Recruiters fakes: an in-memory Recruiters data source, and Gmail/Outlook clients that raise on any mutation."""
from lifeos.platform.notion_client import NotionError
from lifeos.recruiters import notion as store

CELL = lambda t: [{"type": "text", "plain_text": t, "text": {"content": t}}]


def page(person, company="", role="", week="2026-10-11", last="2026-10-07T15:00:00+00:00", done=False, what="", client="", review=False, active=True, pid=None):
    return {"id": pid or f"page-{person}-{company}-{role}".replace(" ", "-"), "properties": {
        "Recruiter / Person": {"type": "title", "title": CELL(person)}, "Recruiter Company": {"type": "rich_text", "rich_text": CELL(company)},
        "Client": {"type": "rich_text", "rich_text": CELL(client)}, "Role": {"type": "rich_text", "rich_text": CELL(role)}, "Medium": {"type": "rich_text", "rich_text": CELL("Email")},
        "What they want": {"type": "rich_text", "rich_text": CELL(what)}, "Last Contact": {"type": "date", "date": {"start": last}}, "Week Ending": {"type": "date", "date": {"start": week}},
        "Done": {"type": "checkbox", "checkbox": done}, "Active": {"type": "checkbox", "checkbox": active}, "Needs review": {"type": "checkbox", "checkbox": review},
        "Source URL": {"type": "url", "url": None}}}


class FakeRecruiters:
    source = "source-example"

    def __init__(self, pages=None, fail_query=False, drop_writes=False):
        self.pages, self.writes, self.queries, self.fail_query, self.drop_writes = list(pages or []), [], 0, fail_query, drop_writes

    def call(self, method, path, body=None):
        if method == "GET" and path.startswith("/data_sources/"):
            return {"properties": {n: {"type": t} for n, t in store.SCHEMA.items()}}
        raise AssertionError((method, path))

    def query_data_source(self, source_id, body):
        self.queries += 1
        if self.fail_query:
            raise NotionError("NOTION_HTTP_500")
        week = body["filter"]["date"]["equals"]
        return {"results": [p for p in self.pages if p["properties"]["Week Ending"]["date"]["start"] == week], "has_more": False}

    def call_once(self, method, path, body=None):
        assert (method, path) == ("POST", "/pages")
        self.writes.append(("create", body["properties"]))
        if self.drop_writes:
            return {}
        props = {}
        for name, value in body["properties"].items():
            kind = next(iter(value))
            props[name] = {"type": kind, **value}
            if kind in ("title", "rich_text"):
                props[name][kind] = [{"type": "text", "plain_text": value[kind][0]["text"]["content"], "text": value[kind][0]["text"]}]
        for name in store.SCHEMA:
            props.setdefault(name, {"type": store.SCHEMA[name], store.SCHEMA[name]: [] if store.SCHEMA[name] in ("title", "rich_text") else None})
        self.pages.append({"id": f"page-new-{len(self.pages)}", "properties": props})
        return {}

    def update_page_properties(self, page_id, properties):
        self.writes.append(("update", properties))
        if self.drop_writes:
            return {}
        target = next(p for p in self.pages if p["id"] == page_id)
        for name, value in properties.items():
            kind = next(iter(value))
            target["properties"][name] = {"type": kind, **value}
            if kind in ("title", "rich_text"):
                target["properties"][name][kind] = [{"type": "text", "plain_text": value[kind][0]["text"]["content"], "text": value[kind][0]["text"]}]
        return {}


def mail(sender_name="Pat Example", address="pat@example.com", subject="Opportunity", body="", received="2026-10-07T15:00:00+00:00", provider="gmail", folder="INBOX",
         bulk=False, auto=False, noreply=False, url="https://mail.example.com/m/1", ident="m1"):
    return {"provider": provider, "id": ident, "received": received, "sender_name": sender_name, "sender_address": address, "subject": subject, "body": body, "folder": folder,
            "markers": {"bulk": bulk, "auto": auto, "noreply": noreply}, "url": url}


class ReadOnlyGmail:
    def __init__(self, records):
        self.records, self.calls = records, []

    def list_ids_complete(self, query, limit, include_spam=False):
        self.calls.append((query, include_spam))
        return [r["id"] for r in self.records]

    def mail_record(self, message_id):
        r = next(x for x in self.records if x["id"] == message_id)
        h = {"from": f"{r['sender_name']} <{r['sender_address']}>", "subject": r["subject"]}
        if r["markers"]["bulk"]:
            h["list-unsubscribe"] = "<mailto:x@example.com>"
        if r["markers"]["auto"]:
            h["auto-submitted"] = "auto-generated"
        return {"id": r["id"], "received_at": r["received"], "headers": h, "body_text": r["body"], "label_ids": ["SPAM"] if r["folder"] == "JUNK" else ["INBOX"], "thread": "t1"}

    def __getattr__(self, name):
        if name in ("trash", "relabel", "apply_amazon", "label_id", "message_labels"):
            raise AssertionError("mail mutation: " + name)
        raise AttributeError(name)


class ReadOnlyOutlook:
    def __init__(self, inbox=(), junk=()):
        self.inbox, self.junk, self.calls = list(inbox), list(junk), []

    def messages(self, folder="inbox", since=None, limit=5000, fields=None, time_field="receivedDateTime", prefer=""):
        self.calls.append(folder)
        return self.inbox if folder == "inbox" else self.junk

    def move(self, *a):
        raise AssertionError("mail mutation: move")

    def folder_id(self, *a, **k):
        raise AssertionError("mail mutation: folder")
