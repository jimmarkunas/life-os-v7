"""LIFE-544: test helpers that more than one test module (often in another domain) uses. Moved here out of test_*.py files, unchanged."""
import time
from lifeos.jobs import private_report as pr
from contextlib import contextmanager
from lifeos.platform.notion_client import NotionError


PARENT = pr.REPORTS_PAGE


class FakeStore:
    """A dict standing in for the private snapshot store (report key -> snapshot)."""
    def __init__(self):
        self.rows = {}

    def ensure(self, connection):
        pass

    def save(self, connection, key, snapshot):
        self.rows[key] = snapshot

    def load(self, connection, key):
        return self.rows.get(key)


@contextmanager
def connect():
    yield object()


class FakeClient:
    """Just enough Notion: the ledger's data source -> database -> page chain, search, page create (with its table children), and block children."""
    source = "s" * 32

    def __init__(self, parent=PARENT, existing=None, fail=False, lose_rows=0):
        self.parent, self.existing, self.fail, self.lose_rows, self.writes, self.blocks = parent, existing, fail, lose_rows, [], {}

    def call(self, method, path, body=None):
        if self.fail:
            raise NotionError("NOTION_HTTP_500")
        if path.startswith("/data_sources/"):
            return {"parent": {"type": "database_id", "database_id": "d" * 32}}
        if path.startswith("/databases/"):
            return {"parent": {"type": "page_id", "page_id": self.parent} if self.parent else {"type": "workspace"}}
        if path == "/search":
            raise AssertionError("D250: a private report is never found by a Notion search")
        if path.startswith("/pages/"):
            return self.existing.get(path.split("/")[2], {"archived": False}) if isinstance(self.existing, dict) else {"archived": False}
        base = path.split("?")[0]
        return {"results": self.blocks.get(base.split("/")[2], []), "has_more": False}

    def call_once(self, method, path, body=None, version=None):
        self.writes.append((method, path, body))
        tables = []
        for index, table in enumerate(body["children"]):
            if table.get("type") != "table":                       # LIFE-212: an intro paragraph above the tables
                continue
            rows = table["table"]["children"]
            self.blocks[f"t{index}"] = [{"id": f"r{n}"} for n in range(len(rows) - self.lose_rows)]
            tables.append({"id": f"t{index}", "type": "table"})
        self.blocks["page1"] = tables
        return {"id": "page1"}


PM_BODY = ("<h2>Responsibilities</h2><ul><li>Own the program roadmap and drive delivery across multiple teams</li><li>Lead integration of NetSuite with the storefront and payment providers</li>"
           "<li>Report to executive stakeholders on a global enterprise program</li></ul><h2>Requirements</h2><ul><li>8+ years of experience in program management delivering enterprise software</li>"
           "<li>Experience with NetSuite, Stripe and Contentful</li><li>Strong communication skills</li></ul>") * 2


ENGINEER_BODY = ("<h2>Responsibilities</h2><ul><li>Write production Java and Go services that process card payments</li><li>Design database schemas and stay on call for the platform</li>"
                 "<li>Review code and mentor engineers</li></ul><h2>Requirements</h2><ul><li>8+ years of professional software engineering in Java or Go</li>"
                 "<li>Deep experience with distributed systems and Kubernetes</li></ul>") * 2


CARD = ('<a href="https://jobright.ai/jobs/info/{id}?utm_source=1&imp_id=SYNTH"><span>{company}</span><span>Software &amp; Services</span><span>· Mid Level</span><span>89</span><span>%</span>'
        '<span>{title}</span><span>$120K/yr - $150K/yr</span><span>Remote, US</span><span>5+ referrals</span><span>34 minutes ago</span></a>')


VACANCIES = {
    "v1": ("Acme", "Senior Program Manager", "https://careers.acme.example/jobs/senior-program-manager-1001", PM_BODY),
    "v2": ("Globex", "Technical Program Manager", "https://careers.globex.example/jobs/technical-program-manager-2002", PM_BODY),
    "v3": ("Initech", "Software Engineer", "https://careers.initech.example/jobs/software-engineer-3003", ENGINEER_BODY),
    "v4": ("Umbrella", "Senior Program Manager", "https://careers.acme.example/jobs/senior-program-manager-1001", PM_BODY),
    "v5": ("Hooli", "Delivery Lead Program Manager", "https://careers.hooli.example/jobs/delivery-lead-5005", PM_BODY),
    "v6": ("Wayne", "Technical Program Manager", "https://careers.wayne.example/jobs/technical-program-manager-6006", PM_BODY),
}


def alert(*ids):
    return "".join(CARD.format(id=i, company=VACANCIES[i][0], title=VACANCIES[i][1]) for i in ids)


class Mailbox:
    """A stateful Gmail: labels per message, and the three search shapes the stages use (label:, -label:, in:inbox, from:, newer_than:). Gmail flattens nested label names, so the slug is the query token."""
    def __init__(self, messages):
        self.messages, self.labels, self.log = messages, {i: {"INBOX"} for i in messages}, []

    def label_id(self, name, create=False):
        return name.lower().replace("/", "-").replace(" ", "-")

    def list_ids(self, query, limit=5000):
        tokens = query.replace("{", " ").replace("}", " ").split()
        senders = [t[5:] for t in tokens if t.startswith("from:")]
        out = []
        for i, (sender, _, received) in self.messages.items():
            have = self.labels[i]
            if any(t.startswith("label:") and t[6:] not in have for t in tokens) or any(t.startswith("-label:") and t[7:] in have for t in tokens):
                continue
            if "in:inbox" in tokens and "INBOX" not in have:
                continue
            if any(t.startswith("newer_than:") and (time.time() - received) / 86400 > int(t[11:-1]) for t in tokens):
                continue
            if senders and not any(s in sender for s in senders):
                continue
            out.append(i)
        return out[:limit]

    def message(self, message_id):
        return self.messages[message_id]

    def relabel(self, ids, add=(), remove=()):
        for i in ids:
            self.labels[i] = (self.labels[i] | set(add)) - set(remove)
        self.log.append((sorted(ids), list(add), list(remove)))
