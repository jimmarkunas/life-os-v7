"""NEWSLETTER_GOLDEN_PATH_GATE (LIFE-59): one production-shaped Jobright alert through the REAL V7 stages to the Job Ledger and back to Gmail.

RUNTIME DECLARATION. ENTRY POINT: sweep, extract, resolve-jobright, enrich, fit, publish (with its read-back) and finalize, called as `python -m lifeos.run` calls them.
REQUIRED INPUTS: a labelled Gmail message, the Jobright parser's card shape, the private-profile projection (a synthetic one here), the Notion Job Ledger target check.
CALL CHAIN: lifeos.sources.newsletters.sweep -> ingest.extract -> lifeos.jobs.resolve.stage.run -> lifeos.jobs.enrich.run -> lifeos.jobs.fit.stage.run -> lifeos.jobs.publish.run
(fit_sync, readback) -> lifeos.sources.newsletters.finalize. MUTATION OWNER: the Jobs store (v7_*) for state, publish for the one Ledger page per qualifying vacancy, finalize for the Gmail Done label.
SUCCESS SIGNAL: every vacancy has exactly one terminal outcome, each published page was read back, and only then does the message get Done. FAIL-CLOSED SIGNAL: a pending, held, unverified or failed vacancy keeps the message open.
CANONICAL STATE: v7_jobs / v7_job_sources / v7_job_fit and the Ledger page. AUTHORITATIVE READ-BACK: readback.check against the page. LIVE ACCEPTANCE BOUNDARY: the real Gmail message, the real Ledger and Jim's acceptance (not claimed here).

What runs for real: the stage code and the production SQL (over the in-memory Jobs store). What is stood in for, because it is an external boundary: Gmail (a stateful mailbox that applies the real label queries), the provider's
link resolution and the employer pages (canned), and Notion (a synthetic Ledger). TinyFish is never reachable: the gate fails if the path touches it. Invented companies and a synthetic profile only (D16)."""
import json
import time
import unittest
from datetime import date
from unittest import mock

from lifeos.jobs import enrich, publish, store
from lifeos.jobs.fit import stage as fit_stage
from lifeos.jobs.resolve import stage as resolve_stage
from lifeos.platform.http import Fetched
from lifeos.sources.newsletters import config, finalize, ingest, sweep
from tests.jobs.fit.test_fit import PROFILE as _PROFILE_OBJECT  # noqa: F401 - the same synthetic profile shape
from tests.kit.jobsdb import JobsDB
from tests.kit.ledger_pages import SOURCE

PROFILE = {"years": 15, "scope": {"team_size": 10, "budget_usd": 20_000_000, "global": "direct", "enterprise": "direct", "multi_team": "direct", "executive": "adjacent", "people_leadership": "direct"},
           "capabilities": [{"id": "erp", "label": "ERP integrations", "class": "direct", "terms": ["netsuite", "erp"], "committed": True},
                            {"id": "cms", "label": "headless CMS", "class": "adjacent", "terms": ["contentful", "cms"]},
                            {"id": "pay", "label": "payments", "class": "direct", "terms": ["stripe", "payment"], "bucket": "skill"},
                            {"id": "pm", "label": "program delivery", "class": "direct", "terms": ["program management", "delivery"], "bucket": "skill"}],
           "functions": [{"terms": ["program manager", "technical program manager", "roadmap"], "class": "direct"}, {"terms": ["implementation", "integration"], "class": "adjacent"}],
           "advantage": ["ai", "automation"], "specialization": ["technical program manager"]}
ENV = {"FIT_PROFILE_JSON": json.dumps(PROFILE), "V7_FIT_GATE": "true"}

PM_BODY = ("<h2>Responsibilities</h2><ul><li>Own the program roadmap and drive delivery across multiple teams</li><li>Lead integration of NetSuite with the storefront and payment providers</li>"
           "<li>Report to executive stakeholders on a global enterprise program</li></ul><h2>Requirements</h2><ul><li>8+ years of experience in program management delivering enterprise software</li>"
           "<li>Experience with NetSuite, Stripe and Contentful</li><li>Strong communication skills</li></ul>") * 2
ENGINEER_BODY = ("<h2>Responsibilities</h2><ul><li>Write production Java and Go services that process card payments</li><li>Design database schemas and stay on call for the platform</li>"
                 "<li>Review code and mentor engineers</li></ul><h2>Requirements</h2><ul><li>8+ years of professional software engineering in Java or Go</li>"
                 "<li>Deep experience with distributed systems and Kubernetes</li></ul>") * 2
CARD = ('<a href="https://jobright.ai/jobs/info/{id}?utm_source=1&imp_id=SYNTH"><span>{company}</span><span>Software &amp; Services</span><span>· Mid Level</span><span>89</span><span>%</span>'
        '<span>{title}</span><span>$120K/yr - $150K/yr</span><span>Remote, US</span><span>5+ referrals</span><span>34 minutes ago</span></a>')

# id -> (company, title, employer page url, page body). V4 resolves to V1's employer page: the same vacancy seen twice. V5's resolution is rate limited at first.
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


def page(title, company, body):
    ld = {"@type": "JobPosting", "title": title, "hiringOrganization": {"@type": "Organization", "name": company}, "datePosted": date.today().isoformat(), "description": body.replace('"', "'")}
    return "<html><title>%s</title><script type='application/ld+json'>%s</script></html>" % (title, json.dumps(ld))


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


class JobLedger:
    """A synthetic Notion Job Ledger: it checks the target, creates pages, answers the URL query and the read-back, and takes the Fit patches. `break_url` makes the read-back disagree."""
    source = SOURCE

    def __init__(self):
        self.pages, self.blocks, self.calls, self.created, self.break_url = {}, {}, 0, 0, False

    @staticmethod
    def _read(props):
        out = {}
        for name, prop in props.items():
            kind = next(iter(prop))
            out[name] = {kind: [{"plain_text": r["text"]["content"]} for r in prop[kind]]} if kind in ("title", "rich_text") else prop
        return out

    def create(self, props, blocks):
        self.created += 1
        page_id = f"page{self.created}"
        self.pages[page_id] = {"props": self._read(props), "archived": False}
        self.blocks[page_id] = [{"type": b["type"], b["type"]: {"rich_text": [{"plain_text": "".join(r["text"]["content"] for r in b[b["type"]]["rich_text"])}]}} for b in blocks]
        return page_id

    def call(self, method, path, body=None):
        from lifeos.jobs import ledger
        self.calls += 1
        if method == "GET" and path.startswith("/data_sources/"):
            return {"properties": {n: {"type": t} for n, t in ledger.REQUIRED.items()}}
        if method == "POST" and path.endswith("/query"):
            wanted = (((body or {}).get("filter") or {}).get("rich_text") or {}).get("equals")                      # the Stable Job Key lookup; no filter = the URL read
            return {"results": [{"id": i, "properties": p["props"]} for i, p in self.pages.items()
                                if wanted is None or p["props"]["Stable Job Key"]["rich_text"][0]["plain_text"] == wanted], "has_more": False}
        if method == "GET" and path.startswith("/pages/"):
            found = self.pages[path.rsplit("/", 1)[1]]
            props = dict(found["props"])
            if self.break_url:
                props["Apply URL"] = {"url": "https://wrong.example/x"}
            return {"archived": found["archived"], "properties": props}
        if method == "GET" and "/children" in path:
            return {"results": self.blocks[path.split("/blocks/")[1].split("/children")[0]][:3]}
        if method == "PATCH" and path.startswith("/pages/"):
            self.pages[path.rsplit("/", 1)[1]]["props"].update(self._read(body["properties"]))
            return {}
        raise AssertionError((method, path))

    call_once = call


class Journey:
    """The chain a tick runs, in order, over one mailbox, one Jobs store and one Ledger."""
    def __init__(self, test, messages):
        self.mailbox, self.db, self.ledger = Mailbox(messages), JobsDB(), JobLedger()
        self.resolved = {}                                                         # jobright url -> the provider's resolution result
        patches = [mock.patch.object(store, "connect", lambda: self.db), mock.patch.object(store, "ensure_schema", lambda c: None),
                   mock.patch.object(enrich, "fetch", self._fetch), mock.patch.object(publish, "Client", lambda environ: self.ledger),
                   mock.patch.object(enrich.tinyfish, "fetch_many", side_effect=AssertionError("TinyFish must never be reached")),
                   mock.patch.object(enrich.usage, "reserve", side_effect=AssertionError("no paid browser usage in the golden path"))]
        for p in patches:
            p.start()
            test.addCleanup(p.stop)

    @staticmethod
    def _fetch(url, **kwargs):
        for company, title, final, body in VACANCIES.values():
            if url == final:
                return Fetched(url, 200, page(title, company, body))
        return Fetched(url, 404, "")

    def resolve_all(self, ok):
        for key in ok:
            company, title, final, _ = VACANCIES[key]
            self.resolved["https://jobright.ai/jobs/info/" + {"v1": "v1", "v2": "v2", "v3": "v3", "v4": "v4", "v5": "v5", "v6": "v6"}[key]] = {"outcome": "landed", "url": final, "kind": "ats", "via": "original_post"}

    def tick(self):
        """sweep and extract, then the resolver, enrich, fit, publish with its read-back, then finalize."""
        sweep.sweep(self.mailbox, True)
        ingest.extract(self.mailbox, True, 100, self.db)
        resolve_stage.run("jobright", 40, True, lambda urls: [self.resolved.get(u, {"outcome": "rate_limited"}) for u in urls])
        enrich.run(50, True)
        fit_stage.run(100, True, ENV)
        published = publish.run(60, True, ENV)
        return published, finalize.finalize(self.mailbox, True, 300, lambda: self.db)

    def table(self):
        """{company: (status, reason, verified, has a Ledger page)}: one row per vacancy, which is the accounting."""
        rows = self.db.raw.execute("SELECT company, status, unresolved_reason, verified_at IS NOT NULL, notion_page_id IS NOT NULL FROM v7_jobs ORDER BY id").fetchall()
        return {r[0]: (r[1], r[2], bool(r[3]), bool(r[4])) for r in rows}

    def labels(self, message_id):
        return {l for l in self.mailbox.labels[message_id] if l.startswith("j-newsletters")}


def mail(*ids, age=3600):
    return ("Jobright <alerts@jobright.ai>", alert(*ids), time.time() - age)


NEWSLETTER = {"j-newsletters", "j-newsletters-processed"}
DONE = NEWSLETTER | {"j-newsletters-done"}


class GoldenPath(unittest.TestCase):
    def journey(self, **messages):
        return Journey(self, messages)

    def test_one_alert_is_accounted_vacancy_by_vacancy_and_only_the_complete_mail_is_finalized(self):
        j = self.journey(m1=mail("v1", "v2", "v3", "v4", "v5"), m2=mail("v6"))
        j.resolve_all(["v1", "v2", "v3", "v4", "v6"])                              # v5's provider link stays rate limited: DEGRADED, not empty
        published, closed = j.tick()
        self.assertEqual(j.table(), {
            "Acme": ("PUBLISHED", None, True, True), "Globex": ("PUBLISHED", None, True, True), "Initech": ("EXCLUDED_FIT", "lane_exclude", False, False),
            "Umbrella": ("DUPLICATE", "dup_of_final_url", False, False), "Hooli": ("NEW", "rate_limited", False, False), "Wayne": ("PUBLISHED", None, True, True)})
        self.assertEqual((j.labels("m1"), j.labels("m2")), (NEWSLETTER, DONE))     # m1 still has a pending vacancy; m2 is complete and verified
        self.assertEqual((closed["closed"], closed["in_flight"]), (1, 1))
        self.assertEqual(j.ledger.created, 3)                                      # one canonical Ledger page per qualifying vacancy, no more
        self.assertEqual(published["readback"], {"checked": 3, "verified": 3, "failed": 0, "why": {}})

    def test_the_us_remote_view_carries_the_admitted_vacancy_with_its_employer_link_key_and_fit(self):
        j = self.journey(m1=mail("v1"))
        j.resolve_all(["v1"])
        j.tick()
        (page_props,) = [p["props"] for p in j.ledger.pages.values()]
        self.assertEqual(page_props["Apply URL"]["url"], VACANCIES["v1"][2])
        self.assertEqual(page_props["Visible Lane"]["select"]["name"], "US Remote")
        self.assertEqual(page_props["Admission Status"]["select"]["name"], "Admitted")
        self.assertEqual(page_props["Source Types"]["multi_select"][0]["name"], "Jobright")
        self.assertGreaterEqual(page_props["LIFE OS Fit"]["number"], 72)
        key = self.db_key(j)
        self.assertEqual(page_props["Stable Job Key"]["rich_text"][0]["plain_text"], key)

    @staticmethod
    def db_key(j):
        return j.db.raw.execute("SELECT dedupe_key FROM v7_jobs").fetchone()[0]

    def test_the_pending_vacancy_finalizes_its_mail_only_after_it_is_published_and_read_back(self):
        j = self.journey(m1=mail("v1", "v5"))
        j.resolve_all(["v1"])
        j.tick()
        self.assertEqual(j.labels("m1"), NEWSLETTER)
        j.resolve_all(["v5"])                                                       # the provider recovers on a later tick
        j.tick()
        self.assertEqual(j.table()["Hooli"], ("PUBLISHED", None, True, True))
        self.assertEqual(j.labels("m1"), DONE)
        self.assertEqual(j.ledger.created, 2)

    def test_a_full_replay_creates_no_second_job_and_no_second_ledger_page(self):
        j = self.journey(m1=mail("v1", "v2"))
        j.resolve_all(["v1", "v2"])
        j.tick()
        before = (j.table(), j.ledger.created, j.db.count("v7_jobs"), j.db.count("v7_job_sources"))
        for i in j.mailbox.labels:                                                  # the same message arrives again, as if it were never swept
            j.mailbox.labels[i] = {"INBOX"}
        j.tick()
        self.assertEqual((j.table(), j.ledger.created, j.db.count("v7_jobs"), j.db.count("v7_job_sources")), before)
        self.assertEqual(j.labels("m1"), DONE)

    def test_a_database_failure_halfway_through_a_message_leaves_it_pending_and_the_retry_completes_it_once(self):
        j = self.journey(m1=mail("v1", "v2"))
        j.resolve_all(["v1", "v2"])
        sweep.sweep(j.mailbox, True)
        j.db.fail_on = ("INSERT IGNORE INTO v7_jobs", 2)                            # the second card's insert fails
        with self.assertRaises(RuntimeError):
            ingest.extract(j.mailbox, True, 100, j.db)
        self.assertEqual((j.labels("m1") & {"j-newsletters-processed"}, j.db.count("v7_jobs")), (set(), 1))   # not Processed, and nothing is lost: one card is saved
        j.tick()
        self.assertEqual((j.db.count("v7_jobs"), j.labels("m1"), j.ledger.created), (2, DONE, 2))

    def test_a_ledger_page_that_does_not_read_back_keeps_the_mail_open_until_it_does(self):
        j = self.journey(m1=mail("v1"))
        j.resolve_all(["v1"])
        j.ledger.break_url = True
        published, closed = j.tick()
        self.assertEqual((published["readback"]["failed"], closed["unverified"], closed["closed"]), (1, 1, 0))
        self.assertEqual(j.labels("m1"), NEWSLETTER)
        j.ledger.break_url = False
        j.tick()
        self.assertEqual((j.table()["Acme"], j.labels("m1")), (("PUBLISHED", None, True, True), DONE))
        self.assertEqual(j.ledger.created, 1)

    def test_a_crash_after_the_ledger_page_is_created_but_before_it_is_recorded_does_not_duplicate_the_page(self):
        j = self.journey(m1=mail("v1"))
        j.resolve_all(["v1"])
        j.db.fail_on = ("UPDATE v7_jobs SET status='PUBLISHED'", 1)                  # the database write after the Notion create fails
        with self.assertRaises(RuntimeError):
            j.tick()
        self.assertEqual(j.ledger.created, 1)
        j.tick()                                                                    # the next tick must not create the same page again
        self.assertEqual(j.ledger.created, 1)
        self.assertEqual((j.table()["Acme"], j.labels("m1")), (("PUBLISHED", None, True, True), DONE))


if __name__ == "__main__":
    unittest.main()
