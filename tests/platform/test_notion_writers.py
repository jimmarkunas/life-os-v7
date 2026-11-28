"""every writer of the Daily Report page is listed in docs/platform/notion-writers.md with its owner, job and guard, and
whole-page reads happen only.
"""

import pathlib
import re
import unittest
from tests import wftext  # noqa: E402

import yaml

from lifeos.platform import router

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/platform/notion-writers.md"
INFRA = {
    "lifeos/platform/report_region.py",
    "lifeos/platform/readback.py",
    "lifeos/platform/settings.py",
    "lifeos/platform/__init__.py",
    "lifeos/platform/router.py",
}
PROOFS = "lifeos/sources/proofs/"
GUARDS = ("ScopedClient", "replace_text", "replace_tree")
HOW_TO = "a module that writes a Notion page needs one OWNERS entry in lifeos/platform/router.py and one row in docs/platform/notion-writers.md (see its 'The rules')"
NOT_OWNED_HERE = {
    router.REPO_REGION: "the Weekly Review page, listed under other writers",
    router.HIRING_REGION: "LIFE-973: a live Job Ledger view; no table writer",
    router.RECRUITER_OUTREACH_REGION: "LIFE-973: a live Recruiters view; only its count line is written (other writers)",
}


def tables(text):
    """> {first header cell: [row cells]} for every markdown table in the text."""
    found, header, rows = {}, None, []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
            elif not set("".join(cells)) <= set("-: "):
                rows.append(cells)
        else:
            if header is not None:
                found.setdefault("|".join(header), rows)
            header, rows = None, []
    return found


def sources(root=ROOT):
    return {str(p.relative_to(root)): p.read_text() for p in (root / "lifeos").rglob("*.py")}


def writer_candidates(srcs):
    """Modules outside the shared infrastructure that use the page writer library or the scope guard."""
    return sorted(
        path
        for path, text in srcs.items()
        if path not in INFRA and not path.startswith(PROOFS) and re.search(r"\b(report_region|ScopedClient)\b", text)
    )


def digest_callers(srcs):
    return {
        path: len(re.findall(r"page_digest\(", text))
        for path, text in srcs.items()
        if path != "lifeos/platform/report_region.py" and "page_digest(" in text
    }


WRITERS_KEY, OTHERS_KEY, DIGESTS_KEY = (
    "module|region|owner|job.stage|how it finds its block|guard",
    "module|page|note",
    "module|page|use",
)


class NotionWriters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = tables(DOC.read_text())
        cls.writers, cls.others, cls.digests = cls.doc[WRITERS_KEY], cls.doc[OTHERS_KEY], cls.doc[DIGESTS_KEY]
        cls.srcs = sources()

    def listed_modules(self):
        listed = {row[0] for row in self.writers}
        for row in self.others:
            listed.update(part.strip() for part in row[0].split(","))
        return listed

    def test_every_module_that_writes_a_notion_page_is_listed(self):
        listed = self.listed_modules()
        missing = [path for path in writer_candidates(self.srcs) if path not in listed]
        self.assertEqual(missing, [], HOW_TO)

    def test_every_listed_module_exists_and_each_region_has_exactly_one_writer(self):
        for module in self.listed_modules():
            self.assertIn(module, self.srcs, module)
        regions = [row[1] for row in self.writers if row[1] != "-"]
        self.assertEqual(len(regions), len(set(regions)), "a region has two writers")
        for name in regions:
            self.assertEqual(
                self.writers[[r[1] for r in self.writers].index(name)][2], router.OWNERS[getattr(router, name)], name
            )
        owned = {getattr(router, name) for name in regions} | set(NOT_OWNED_HERE)
        self.assertEqual(sorted(set(router.OWNERS) - owned), [], "an OWNERS region has no writer row: " + HOW_TO)

    def test_each_job_and_stage_is_real(self):
        jobs = yaml.safe_load(wftext.logical("domains"))["jobs"]
        for row in self.writers:
            job, stage = row[3].split(".")
            runs = " ".join(str(step.get("run", "")) for step in jobs[job]["steps"])
            self.assertRegex(runs, rf"lifeos\.run {re.escape(stage)}\b", row[3])

    def test_a_region_writer_goes_through_the_scope_guard(self):
        for row in self.writers:
            if row[1] != "-":
                self.assertTrue(
                    any(g in self.srcs[row[0]] for g in GUARDS), f"{row[0]} must use ScopedClient or replace_text"
                )

    def test_whole_page_digests_happen_only_where_the_doc_lists_them_and_never_twice_on_the_daily_report(self):
        listed = {row[0] for row in self.digests}
        callers = digest_callers(self.srcs)
        self.assertEqual(sorted(callers), sorted(listed), "page_digest is allowed only in the listed places")

    def test_the_detector_catches_a_new_unlisted_writer_and_a_second_digest(self):
        fake = {
            "lifeos/newcard/card.py": "from lifeos.platform import report_region\nclient = report_region.ScopedClient(c, b, f)\n",
            "lifeos/other/job.py": "digest = R.page_digest(c, p, [], f)\nagain = R.page_digest(c, p, [], f)\n",
        }
        self.assertEqual(writer_candidates(fake), ["lifeos/newcard/card.py"])
        self.assertEqual(digest_callers(fake), {"lifeos/other/job.py": 2})
        listed = self.listed_modules()
        self.assertNotIn("lifeos/newcard/card.py", listed)


if __name__ == "__main__":
    unittest.main()
