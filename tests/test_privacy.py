"""CI guard for the public-repo privacy rule (docs/PRIVACY.md). Patterns only - no personal data."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# Vendor/synthetic domains that may appear in sender-matching rules and fixtures.
ALLOWED_EMAIL_DOMAINS = {"example.com", "lensa.com", "jobright.ai", "linkedin.com", "user.dice.com",
                         "lensa.com.evil.example", "gmail.com"}
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


def tracked_text_files():
    names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for name in names:
        path = ROOT / name
        if name != "tests/test_privacy.py" and path.is_file():
            try:
                yield name, path.read_text()
            except UnicodeDecodeError:
                continue


class PrivacyGuard(unittest.TestCase):
    def test_no_unapproved_emails(self):
        bad = [(n, m.group(0)) for n, t in tracked_text_files() for m in EMAIL.finditer(t)
               if m.group(1).lower() not in ALLOWED_EMAIL_DOMAINS]
        self.assertEqual(bad, [], "email address outside the allowlist (see docs/PRIVACY.md)")

    def test_no_secret_shaped_strings(self):
        bad = [(n, label) for n, t in tracked_text_files() for label, rx in SECRET_SHAPES.items() if rx.search(t)]
        self.assertEqual(bad, [], "secret-shaped string committed (see docs/PRIVACY.md)")


if __name__ == "__main__":
    unittest.main()
