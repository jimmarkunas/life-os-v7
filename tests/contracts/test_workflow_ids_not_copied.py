"""LIFE-528 (Jim, Nov 10 2026, comment 12557): a Bitwarden secret id that appears in the workflow files must not be copied anywhere else in the repo.

The ids are identifiers, not secret values, and they are already public in the workflow files. A real one pasted into a test file once, so this rule makes the next copy fail the build.
It reads the ids FROM the workflow files at test time (the bws `secrets:` mapping lines), never lists them, and prints only a file name and a count.
Files that already pin the workflow mapping on purpose are allow-listed by path with a ceiling: a new copy in them, or in any other file, fails."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
MAPPING_LINE = re.compile(r">|secrets?:")
ALLOWED = {                                                          # path: (most copies it may hold, why)
    "tests/contracts/test_workflow.py": (45, "pins the exact bws mappings each workflow loads (existing contract; the ids are the thing under test)"),
    "docs/NOTION_INTEGRATIONS.md": (6, "the integrations table names the Bitwarden id each Notion token is loaded from (identifiers only, already public in the workflows)"),
}


def workflow_ids(root):
    ids = set()
    for path in sorted((Path(root) / ".github" / "workflows").glob("*.y*ml")):
        for line in path.read_text(errors="ignore").splitlines():
            if MAPPING_LINE.search(line):
                ids.update(m.lower() for m in UUID.findall(line))
    return ids


def tracked(root):
    done = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True)
    if done.returncode == 0 and done.stdout.strip():
        return [p for p in done.stdout.splitlines()]
    return [str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file() and ".git" not in p.parts]


def copies(root, files=None, ids=None):
    """{path: how many workflow ids it holds}, for every file outside .github/workflows that holds at least one."""
    ids = workflow_ids(root) if ids is None else ids
    found = {}
    for rel in (tracked(root) if files is None else files):
        if rel.startswith(".github/workflows/"):
            continue
        try:
            text = (Path(root) / rel).read_text(errors="ignore").lower()
        except OSError:
            continue
        n = sum(text.count(i) for i in ids)
        if n:
            found[rel] = n
    return found


def problems(found, allowed=ALLOWED):
    out = []
    for rel, n in sorted(found.items()):
        if rel not in allowed:
            out.append(f"{rel}: holds {n} secret id(s) copied from the workflow files")
        elif n > allowed[rel][0]:
            out.append(f"{rel}: holds {n} workflow id(s), more than the {allowed[rel][0]} it is allowed")
    for rel in sorted(set(allowed) - set(found)):
        out.append(f"{rel}: is allow-listed but holds no workflow id any more; remove it from ALLOWED")
    return out


class WorkflowIdsAreNotCopied(unittest.TestCase):
    def test_the_repo_copies_no_workflow_secret_id_outside_the_allow_list(self):
        self.assertEqual(problems(copies(ROOT)), [])

    def test_the_workflows_do_carry_ids_so_the_rule_is_not_vacuous(self):
        self.assertGreater(len(workflow_ids(ROOT)), 5)

    def test_every_allow_listed_file_has_a_reason(self):
        for rel, (ceiling, reason) in ALLOWED.items():
            self.assertTrue(ceiling > 0 and len(reason) > 20, rel)

    def test_negative_control_a_made_up_id_copied_from_a_made_up_workflow_is_caught(self):
        fake = "11111111-2222-4333-8444-555555555555"                      # made up: exists only in this temp tree
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / ".github" / "workflows").mkdir(parents=True)
            (tree / ".github" / "workflows" / "x.yml").write_text(f"secrets: |\n  {fake} > SOME_NAME\n")
            (tree / "lifeos").mkdir()
            (tree / "lifeos" / "clean.py").write_text("x = 1\n")
            (tree / "lifeos" / "leak.py").write_text(f'ID = "{fake}"\n')
            (tree / "docs").mkdir()
            (tree / "docs" / "UPPER.md").write_text(f"see {fake.upper()}\n")
            files = ["lifeos/clean.py", "lifeos/leak.py", "docs/UPPER.md", ".github/workflows/x.yml"]
            found = copies(tree, files)
            self.assertEqual(found, {"lifeos/leak.py": 1, "docs/UPPER.md": 1})        # case does not hide a copy; the workflow file itself is exempt
            flagged = problems(found, allowed={})
            self.assertEqual(len(flagged), 2)
            self.assertFalse([f for f in flagged if fake in f.lower()])             # the message names the file and a count, never the id

    def test_negative_control_an_allow_listed_file_may_not_grow_and_a_stale_entry_is_flagged(self):
        allowed = {"a.py": (2, "pins a mapping on purpose for a test of the rule"), "b.py": (1, "stale entry used to prove staleness is flagged")}
        self.assertTrue([p for p in problems({"a.py": 3}, allowed) if "more than" in p])
        self.assertTrue([p for p in problems({"a.py": 2}, allowed) if "b.py" in p and "remove it" in p])


if __name__ == "__main__":
    unittest.main()
