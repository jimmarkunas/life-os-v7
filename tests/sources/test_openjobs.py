import json
import unittest

from lifeos.platform.http import Fetched
from lifeos.sources import openjobs as oj


def event(key, title="Technical Program Manager", location="Remote - United States", published="2026-09-30T00:00:00Z", op="upsert"):
    if op == "remove":
        return {"op": "remove", "key": key, "removal": "closed", "removed_at_crawler": None}
    ats, rest = key.split("/")
    slug, ident = rest.split("#")
    return {"op": "upsert", "key": key, "job": {"ats": ats, "slug": slug, "id": ident, "title": title, "location": location,
            "url": f"https://x.example/{ident}", "content": "<p>Lead delivery</p>", "embed_status": "done",
            "published_at": published, "first_seen_at": published}}


def generation(events, previous=None, kind="delta", cursor="2026-09-30"):
    page = b"".join(oj._canonical(e) + b"\n" for e in events)
    header = {"version": 2, "scope": "crawler-export-v1", "kind": kind, "cursor": cursor, "previous": previous, "counts": {},
              "pages": [{"file": "000000.ndjson", "rows": len(events), "bytes": len(page), "sha256": oj._digest(page)}]}
    header["generation"] = oj._digest(oj._canonical(header))
    return header, page


class Site:
    def __init__(self, *gens, corrupt=False):
        self.files = {"latest.json": gens[-1][0]}
        for header, page in gens:
            self.files[f"{header['generation']}/manifest.json"] = header
            self.files[f"{header['generation']}/000000.ndjson"] = page if not corrupt else page + b"x"

    def __call__(self, url, **kw):
        path = url.split("/changes/")[1].split("?")[0]
        body = self.files.get(path)
        if body is None:
            return Fetched(url, 404, "")
        return Fetched(url, 200, body.decode() if isinstance(body, bytes) else oj._canonical(body).decode())


class MemRepo:
    def __init__(self, checkpoint=None):
        self.cp, self.committed, self.degraded = checkpoint, [], False

    def checkpoint(self):
        return self.cp

    def held(self):
        return {"greenhouse/acme#9"}

    def commit(self, header, admits, removes, status, now):
        self.cp = header["generation"]
        self.committed.append((header["generation"], [k for k, _ in admits], removes))

    def degrade(self, now):
        self.degraded = True


EVENTS = [event("greenhouse/acme#1"), event("greenhouse/acme#2", title="Software Engineer"),
          event("lever/beta#3", location="Berlin, Germany"), event("lever/beta#4", location="Austin, TX"),
          event("greenhouse/acme#5", published="2026-08-01T00:00:00Z"), event("greenhouse/acme#9", op="remove")]


class FeedTests(unittest.TestCase):
    def test_first_run_applies_the_head_delta_and_filters(self):
        site = Site(generation(EVENTS))
        repo = MemRepo()
        counts = oj.run(10, True, fetcher=site, repo=repo)
        self.assertEqual(repo.committed[0][1:], (["greenhouse/acme#1"], ["greenhouse/acme#9"]))
        self.assertEqual((counts["events"], counts["admit"], counts["status"]), (6, 1, "OK"))
        self.assertEqual(counts["why"], {"off_family": 1, "non_us": 1, "no_remote_evidence": 1, "stale": 1})

    def test_dry_run_commits_nothing(self):
        repo = MemRepo()
        counts = oj.run(10, False, fetcher=Site(generation(EVENTS)), repo=repo)
        self.assertEqual((repo.committed, counts["admit"]), ([], 1))

    def test_applies_every_generation_after_the_checkpoint_in_order(self):
        g1 = generation([event("greenhouse/acme#1")], cursor="2026-09-28")
        g2 = generation([event("greenhouse/acme#2", title="Program Manager")], previous=g1[0]["generation"], cursor="2026-09-29")
        g3 = generation([event("greenhouse/acme#3", title="Product Manager")], previous=g2[0]["generation"])
        repo = MemRepo(checkpoint=g1[0]["generation"])
        oj.run(10, True, fetcher=Site(g1, g2, g3), repo=repo)
        self.assertEqual([c[1] for c in repo.committed], [["greenhouse/acme#2"], ["greenhouse/acme#3"]])
        self.assertEqual(repo.cp, g3[0]["generation"])

    def test_a_gap_is_degraded_and_nothing_is_applied(self):
        g2 = generation([event("greenhouse/acme#2")], previous="a" * 64)
        repo = MemRepo(checkpoint="b" * 64)
        counts = oj.run(10, True, fetcher=Site(g2), repo=repo)
        self.assertTrue(counts["status"].startswith("DEGRADED"))
        self.assertEqual((repo.committed, repo.degraded, repo.cp), ([], True, "b" * 64))

    def test_a_corrupt_page_is_degraded(self):
        repo = MemRepo()
        counts = oj.run(10, True, fetcher=Site(generation(EVENTS), corrupt=True), repo=repo)
        self.assertEqual((counts["status"], repo.committed), ("DEGRADED:page_corrupt", []))

    def test_up_to_date_is_a_no_op(self):
        g = generation(EVENTS)
        repo = MemRepo(checkpoint=g[0]["generation"])
        self.assertEqual(oj.run(10, True, fetcher=Site(g), repo=repo)["generations"], 0)

    def test_removes_with_long_keys_and_the_cheap_title_prefilter_count_correctly(self):
        events = [event("greenhouse/a-company-with-a-very-long-board-slug#" + "9" * 12, op="remove"),
                  event("greenhouse/acme#1", title="Software Engineer"), event("greenhouse/acme#2")]
        repo = MemRepo()
        counts = oj.run(10, True, fetcher=Site(generation(events)), repo=repo)
        self.assertEqual((counts["events"], counts["removes"], counts["upserts"], counts["admit"]), (3, 1, 2, 1))
        self.assertEqual(counts["why"], {"off_family": 1})

    def test_many_pages_are_applied_in_order(self):
        pages = [[event(f"greenhouse/acme#{i}", title="Program Manager")] for i in range(40)]
        files = {}
        header = {"version": 2, "scope": "crawler-export-v1", "kind": "delta", "cursor": "2026-09-30", "previous": None, "counts": {}, "pages": []}
        for n, page in enumerate(pages):
            data = b"".join(oj._canonical(e) + b"\n" for e in page)
            name = f"{n:06d}.ndjson"
            files[name] = data
            header["pages"].append({"file": name, "rows": 1, "bytes": len(data), "sha256": oj._digest(data)})
        header["generation"] = oj._digest(oj._canonical(header))

        def fetcher(url, **kw):
            path = url.split("/changes/")[1].split("?")[0]
            if path == "latest.json":
                return Fetched(url, 200, oj._canonical(header).decode())
            return Fetched(url, 200, files[path.split("/")[1]].decode())
        admits, removes, counts = oj.process(header, fetcher)
        self.assertEqual([k for k, _ in admits], [f"greenhouse/acme#{i}" for i in range(40)])

    def test_company_from_slug(self):
        self.assertEqual(oj.company_from("acme-labs"), "Acme Labs")


class CompanyNameTests(unittest.TestCase):
    def test_a_workday_tenant_is_the_employer_and_a_job_board_keeps_its_host_words_for_enrich(self):
        self.assertEqual(oj.company_from("foundationccc.wd1.myworkdayjobs.com"), "Foundationccc")
        self.assertEqual(oj.company_from("cohesity.wd5.myworkdayjobs.com"), "Cohesity")
        self.assertEqual(oj.company_from("edtech.com"), "Edtech Com")
        self.assertEqual(oj.company_from("acme-labs"), "Acme Labs")
