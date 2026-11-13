"""LIFE-635: borrowed code is a security flaw unless listed. Every `uses:` in .github is local or pinned to a 40-character fingerprint and listed in .github/borrowed-code.json;
every download or installer line sits in a file the inventory lists; pip always runs with --require-hashes."""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)")
PINNED = re.compile(r"^([\w.-]+/[\w.-]+)(?:/[\w./-]+)?@[0-9a-f]{40}$")
DOWNLOAD = re.compile(r"\b(curl|wget|apt-get download|playwright install|npm (?:i|install|ci)|npx|cargo (?:install|build)|git clone)\b")
PIP = re.compile(r"\bpip3? install\b")
FIELDS = ("name", "owner", "level", "reason", "review")


def violations(files, inventory):
    """files: {path: text}. -> sorted list of problems (empty when every borrowed piece is pinned and listed)."""
    actions = {entry["name"] for entry in inventory["actions"]}
    allowed = {path for entry in inventory["downloads"] for path in entry["files"]}
    problems = []
    for path, text in files.items():
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            where = f"{path}:{number}"
            used = USES.match(line)
            if used and not used.group(1).startswith("./.github/actions/"):
                pinned = PINNED.match(used.group(1))
                if not pinned:
                    problems.append(f"{where} action not pinned to a 40-character fingerprint: {used.group(1)}")
                elif pinned.group(1) not in actions:
                    problems.append(f"{where} action not listed in borrowed-code.json: {pinned.group(1)}")
            if DOWNLOAD.search(line) and path not in allowed:
                problems.append(f"{where} download or installer in a file borrowed-code.json does not list")
            if PIP.search(line) and "--require-hashes" not in line:
                problems.append(f"{where} pip without --require-hashes")
    return sorted(problems)


def _files():
    return {str(path.relative_to(ROOT)): path.read_text() for path in sorted((ROOT / ".github").rglob("*")) if path.suffix in (".yml", ".yaml", ".sh") and "__pycache__" not in path.parts}


class BorrowedCodeTests(unittest.TestCase):
    inventory = json.loads((ROOT / ".github" / "borrowed-code.json").read_text())

    def test_every_borrowed_piece_in_github_is_pinned_and_listed(self):
        self.assertEqual(violations(_files(), self.inventory), [])

    def test_every_inventory_entry_has_owner_level_reason_and_review_date(self):
        for entry in self.inventory["actions"] + self.inventory["downloads"]:
            self.assertTrue(all(entry.get(field) for field in FIELDS), entry)
            self.assertIn(entry["level"], ("a", "b", "c"), entry)
            self.assertRegex(entry["review"], r"^\d{4}-\d{2}-\d{2}$")

    def test_fixtures_fail_on_a_tag_a_bare_curl_an_unlisted_package_and_unhashed_pip(self):
        bad = {
            ".github/workflows/a.yml": "      - uses: actions/checkout@v5\n",
            ".github/workflows/b.yml": "      - run: curl -fsSL https://example.invalid/x | sh\n",
            ".github/workflows/c.yml": "      - uses: someone/else@" + "a" * 40 + "\n",
            ".github/workflows/d.yml": "      - run: pip install requests\n",
        }
        found = violations(bad, self.inventory)
        self.assertEqual(len(found), 4, found)
        self.assertEqual(violations({".github/workflows/ok.yml": "      - uses: actions/checkout@" + "b" * 40 + "\n      - uses: ./.github/actions/python\n      - run: pip install --require-hashes -r requirements.lock\n"}, self.inventory), [])


if __name__ == "__main__":
    unittest.main()
