"""CI guard for the public-repo privacy rule (docs/PRIVACY.md). Patterns only - no personal data."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# Vendor/synthetic domains that may appear in sender-matching rules and fixtures.
ALLOWED_EMAIL_DOMAINS = {"example.com", "lensa.com", "jobright.ai", "linkedin.com", "user.dice.com",
                         "lensa.com.evil.example",
                         "connect.dice.com", "recruiter.dice.com", "jobs.reed.co.uk"}
SECRET_SHAPES = {
    "google client secret": re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"),
    "google refresh token": re.compile(r"\b1//[A-Za-z0-9_-]{30,}"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "notion token": re.compile(r"\b(?:secret_|ntn_)[A-Za-z0-9]{30,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer literal": re.compile(r"Bearer\s+[A-Za-z0-9._-]{30,}"),
    "phone number": re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)"),
    "google client id": re.compile(r"\b\d{9,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com"),
}
# LIFE-338: live Notion page and data-source ids stay out of the repo; code reads them from LIFEOS_NOTION_<NAME> (lifeos/platform/notion_ids.py).
NOTION_URL = re.compile(r"(?:notion\.(?:so|com)/(?:[\w-]+/)*[\w-]*?|collection://)([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})\b", re.I)
ID_LITERAL = re.compile(r"""["']([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})["']""", re.I)
NOT_NOTION_IDS = {"lifeos/probe.py"}      # public job ids from vendor sites


def synthetic(value):
    digits = set(value.replace("-", "").lower())
    return len(digits) <= 2 or value.replace("-", "").lower() in ("1234567890abcdef1234567890abcdef",)


def tracked_text_files():
    names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for name in names:
        path = ROOT / name
        if name != "tests/test_privacy.py" and path.is_file():
            try:
                yield name, path.read_text()
            except UnicodeDecodeError:
                continue


def unapproved_email_counts(files):
    """-> {file name: how many addresses outside the allowlist}. NEVER the address itself: a failure here is printed into public CI logs (LIFE-519)."""
    counts = {}
    for name, text in files:
        n = sum(1 for m in EMAIL.finditer(text) if m.group(1).lower() not in ALLOWED_EMAIL_DOMAINS)
        if n:
            counts[name] = n
    return counts


class PrivacyGuard(unittest.TestCase):
    def test_no_unapproved_emails(self):
        # The failure message names only files and counts. Find the value yourself with: git grep -n -E '[A-Za-z0-9._%+-]+@' -- <file>
        self.assertEqual(unapproved_email_counts(tracked_text_files()), {}, "email address outside the allowlist in the files above (counts only; see docs/PRIVACY.md)")

    def test_negative_control_a_planted_address_is_caught_and_never_printed(self):
        """LIFE-519: prove the guard fires on a personal-looking address AND that its failure output holds none of the value. The address is built here, so this file never contains one."""
        local, domain = "planted" + "." + "person", "gmail" + "." + "com"
        planted = [("fake/file.txt", "contact: " + local + "@" + domain + " for details\n")]
        self.assertEqual(unapproved_email_counts(planted), {"fake/file.txt": 1})                    # caught (gmail is not on the allowlist)
        with self.assertRaises(AssertionError) as caught:
            self.assertEqual(unapproved_email_counts(planted), {}, "email address outside the allowlist in the files above (counts only; see docs/PRIVACY.md)")
        shown = str(caught.exception)
        self.assertIn("fake/file.txt", shown)                                                       # the file is named, so a human can find it
        for piece in (local, domain, local + "@" + domain, "@"):
            self.assertNotIn(piece, shown)                                                          # none of the matched value is in the failure text

    def test_allowed_domains_do_not_include_personal_mail(self):
        for personal in ("gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "icloud.com", "yahoo.com", "proton.me", "protonmail.com"):
            self.assertNotIn(personal, ALLOWED_EMAIL_DOMAINS)

    def test_no_secret_shaped_strings(self):
        bad = [(n, label) for n, t in tracked_text_files() for label, rx in SECRET_SHAPES.items() if rx.search(t)]
        self.assertEqual(bad, [], "secret-shaped string committed (see docs/PRIVACY.md)")

    def test_no_live_notion_ids(self):
        bad = []
        for n, t in tracked_text_files():
            if n.startswith("tests/") or n.endswith("manifest.json"):
                continue
            bad += [(n, "notion url") for m in NOTION_URL.finditer(t) if not synthetic(m.group(1))]
            if n.startswith("lifeos/") and n.endswith(".py") and n not in NOT_NOTION_IDS:
                bad += [(n, "id literal") for m in ID_LITERAL.finditer(t) if not synthetic(m.group(1))]
        self.assertEqual(bad, [], "live Notion id committed; use lifeos.platform.notion_ids (see docs/PRIVACY.md)")


if __name__ == "__main__":
    unittest.main()


class ClockFormatTests(unittest.TestCase):
    """Jim: always AM/PM ("1:30 PM CT"), never 24-hour time, in anything he reads. Machine timestamps (ISO, with a T) are not display."""

    def test_no_24_hour_display_time_in_the_code(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[2] / "lifeos"
        bad = []
        for path in root.rglob("*.py"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                for match in re.finditer(r"%[Hk]:%M", line):
                    if line[max(0, match.start() - 1):match.start()] != "T":
                        bad.append(f"{path.relative_to(root)}:{number}")
        self.assertEqual(bad, [])
