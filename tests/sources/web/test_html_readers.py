import json
import unittest

from lifeos.sources.web import html_readers as hr, lister


class Page:
    def __init__(self, html, status=200):
        self.html, self.status = html, status


def fetcher(pages):
    return lambda url, **kw: pages.get(url, Page("", 404))


def src(kind, url="https://acme.example/careers", **extra):
    return {"id": "x", "kind": kind, "url": url, "company": "Acme", **extra}


def listing(kind, html, url="https://acme.example/careers", more=None, **extra):
    pages = {url: Page(html), **(more or {})}
    return lister.list_source(src(kind, url, **extra), fetcher(pages))


class FirstPartyReaders(unittest.TestCase):
    def test_static_page_lists_job_links_and_ignores_chrome(self):
        html = '<a href="/careers/data-engineer">Data Engineer</a><a href="/privacy">Privacy</a><a href="/careers">Careers</a>'
        got = listing("static_complete_html", html)
        self.assertEqual((got.status, [j["title"] for j in got.jobs]), ("COMPLETE", ["Data Engineer"]))

    def test_an_empty_page_is_failed_unless_the_registry_zero_marker_is_on_it(self):
        self.assertEqual(listing("static_complete_html", "<p>Welcome</p>").status, "FAILED")
        got = listing("static_complete_html", "<p>There are no open positions at Acme at the moment.</p>", zero_marker="no open positions at Acme")
        self.assertEqual((got.status, got.jobs), ("COMPLETE", []))

    def test_json_ld_job_postings_are_read(self):
        ld = json.dumps({"@type": "JobPosting", "title": "Platform Engineer", "url": "https://acme.example/j/1", "identifier": {"value": "J1"}})
        got = listing("static_complete_html", f'<script type="application/ld+json">{ld}</script>')
        self.assertEqual([(j["id"], j["title"]) for j in got.jobs], [("J1", "Platform Engineer")])

    def test_path_readers_need_the_exact_job_path(self):
        html = '<a href="/jobs/abc-123">Senior PM</a><a href="/jobs">All jobs</a><a href="/about">About</a>'
        got = listing("wttj_html", html, url="https://app.welcometothejungle.com/companies/Acme")
        self.assertEqual([j["title"] for j in got.jobs], ["Senior PM"])

    def test_join_lists_only_the_companys_own_jobs(self):
        html = '<a href="/companies/acme/123-pm">Program Manager</a><a href="/companies/other/9-x">Other Co</a>'
        got = listing("join_html", html, url="https://join.com/companies/acme")
        self.assertEqual([j["title"] for j in got.jobs], ["Program Manager"])

    def test_bluestonex_card_label(self):
        got = listing("bluestonex_html", '<a href="/careers/qa">QA Analyst Full-time More information</a><a href="/x">Other</a>')
        self.assertEqual(len(got.jobs), 1)

    def test_sixflow_explicit_zero_is_complete_and_ambiguous_is_failed(self):
        zero = listing("sixflow_html", "<p>We don't have any live vacancies right now</p>")
        self.assertEqual((zero.status, zero.jobs), ("COMPLETE", []))
        self.assertEqual(listing("sixflow_html", "<p>Welcome</p>").status, "FAILED")
        live = listing("sixflow_html", '<a href="/careers/analyst">Analyst</a>')
        self.assertEqual([j["title"] for j in live.jobs], ["Analyst"])

    def test_futuristic_portal_items(self):
        item = ('<div class="rjjobportal-job-item"><span class="rjjobportal-job-title">1. Engineer</span>'
                '<span class="rjjobportal-meta-label">Job Reference Number</span> <span class="rjjobportal-meta-val">FT-9</span>'
                '<span class="rjjobportal-meta-label">Location</span> <span class="rjjobportal-meta-val">London</span>'
                '<span class="rjjobportal-meta-label">Annual Salary</span> <span class="rjjobportal-meta-val">£70,000</span></div>')
        got = listing("futuristic_html", f'<div class="rjjobportal-careers-wrapper">{item}</div>')
        self.assertEqual([(j["id"], j["title"], j["location"]) for j in got.jobs], [("FT-9", "Engineer", "London")])
        self.assertIn("£70,000", got.jobs[0]["content"])
        self.assertEqual(listing("futuristic_html", "<p>no wrapper</p>").status, "FAILED")

    def test_doubleword_array_in_the_app_bundle(self):
        js = ('x=[{title:"ML Engineer",slug:"ml-engineer",department:"Eng",type:"Full",seniority:"Senior",location:"London",compensation:"£90k",applyEmail:"a@b.c"}];'
              'x.map(r=>r);"Open Positions"')
        page = '<script src="/assets/index-abc.js"></script>'
        got = listing("doubleword_bundle", page, url="https://dw.example/careers/", more={"https://dw.example/assets/index-abc.js": Page(js)})
        self.assertEqual([(j["id"], j["location"]) for j in got.jobs], [("ml-engineer", "London")])
        self.assertEqual(listing("doubleword_bundle", "<p>nothing</p>", url="https://dw.example/careers/").status, "FAILED")

    def test_revolut_exhaustive_count_or_failed(self):
        data = {"props": {"pageProps": {"positions": [{"id": "1", "text": "PM", "locations": [{"name": "London", "type": "Hybrid", "country": "UK"}]}], "openPositionsCount": 1}}}
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        got = listing("revolut_html", html)
        self.assertEqual([(j["id"], j["title"]) for j in got.jobs], [("1", "PM")])
        data["props"]["pageProps"]["openPositionsCount"] = 2
        bad = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        self.assertEqual(listing("revolut_html", bad).status, "FAILED")

    def test_tg0_roles_under_join_us(self):
        html = "<h2>JOIN US</h2><h3>Product Manager</h3><h3>LONDON HQ</h3>"
        self.assertEqual([j["title"] for j in listing("tg0_html", html).jobs], ["Product Manager"])

    def test_veramed_cards_need_title_and_gh_jid(self):
        html = '<section data-role="x"><h2>Biostatistician</h2><a href="/job-openings/?gh_jid=77">Apply</a></section>'
        self.assertEqual([j["id"] for j in listing("veramed_html", html).jobs], ["77"])
        self.assertEqual(listing("veramed_html", '<section data-role="x"><h2>No link</h2></section>').status, "FAILED")

    def test_http_errors_are_failed_never_zero(self):
        for status, reason in ((403, "rate_limited"), (404, "not_found"), (500, "http_500")):
            got = lister.list_source(src("static_complete_html"), fetcher({"https://acme.example/careers": Page("", status)}))
            self.assertEqual((got.status, got.reason), ("FAILED", reason))

    def test_every_scale_up_source_marked_ready_has_a_reader(self):
        from lifeos.sources.web import registry
        for row in registry.for_lane("Scale-Up"):
            self.assertIn(row["kind"], lister.READERS, row["id"])


if __name__ == "__main__":
    unittest.main()
