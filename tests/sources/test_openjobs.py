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
        self.assertEqual(repo.committed[0][1:], (["greenhouse/acme#1", "lever/beta#4"], ["greenhouse/acme#9"]))
        self.assertEqual((counts["events"], counts["admit"], counts["status"]), (6, 2, "OK"))
        self.assertEqual(counts["why"], {"off_family": 1, "non_us": 1, "stale": 1})

    def test_dry_run_commits_nothing(self):
        repo = MemRepo()
        counts = oj.run(10, False, fetcher=Site(generation(EVENTS)), repo=repo)
        self.assertEqual((repo.committed, counts["admit"]), ([], 2))

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

    def test_company_from_slug(self):
        self.assertEqual(oj.company_from("acme-labs"), "Acme Labs")
