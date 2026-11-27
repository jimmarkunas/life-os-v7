"""CI guard for the public-repo privacy rule (docs/PRIVACY.md)."""

import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# Vendor/synthetic domains that may appear in sender-matching rules and fixtures.
ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "life-os-gmail-508407.iam.gserviceaccount.com",
    "lensa.com",
    "jobright.ai",
    "linkedin.com",
    "user.dice.com",
    "lensa.com.evil.example",
    "connect.dice.com",
    "recruiter.dice.com",
    "jobs.reed.co.uk",
}
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
# live Notion page and data-source ids stay out of the repo
NOTION_URL = re.compile(
    r"(?:notion\.(?:so|com)/(?:[\w-]+/)*[\w-]*?|collection://)([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})\b",
    re.I,
)
ID_LITERAL = re.compile(r"""["']([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})["']""", re.I)
NOT_NOTION_IDS = {"lifeos/probe.py", "lifeos/platform/alert_store.py"}  # public job ids from vendor sites


def synthetic(value):
    digits = set(value.replace("-", "").lower())
    return len(digits) <= 2 or value.replace("-", "").lower() in ("1234567890abcdef1234567890abcdef",)


# Bare ids (no URL around them) and names: LIFE-1066. A real Notion id from this workspace has "5926" at characters 9 to 12
# of its 32 hex digits, and is refused anywhere. Any other bare id must be synthetic, or sit in a file listed with a reason.
BARE_ID = re.compile(
    r"(?<![0-9A-Za-z])([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})(?![0-9A-Za-z])"
)
WORKSPACE_MARK = "5926"
BITWARDEN_ID_PATHS = (
    ".github/actions/bws-load/sets.yml",
    ".github/actions/bws-plain/",
    ".github/workflows/",
    "docs/NOTION_INTEGRATIONS.md",
)  # Bitwarden secret ids by design
RANDOM_ID_FILES = {
    "docs/MASTER_ROADMAP.md": "OpenClaw run ids quoted as evidence",
    "docs/platform/shared-read-oracle-runbook.md": "the Jira cloud id inside an Atlassian gateway URL",
    "lifeos/platform/alert_store.py": "public vendor job ids used as sample values",
    "lifeos/probe.py": "public vendor job ids used as sample values",
    "tests/contracts/test_settings_workflows.py": "random fake source id",
    "tests/contracts/test_workflow.py": "random fake secret values that prove masking",
    "tests/edge/buzzguard/test_room_context.py": "ascending fake channel id",
    "tests/edge/secrets/test_lifeos_secret_use.py": "the Jira cloud id inside an Atlassian gateway URL",
    "tests/interview/support.py": "fake meeting id",
    "tests/interview/test_granola_bridge.py": "fake meeting id",
    "tests/jobs/test_ats_detail.py": "vendor job ids in sample URLs",
    "tests/jobs/test_enrich.py": "vendor job ids in sample URLs",
    "tests/jobs/test_publish.py": "vendor job ids in sample URLs",
    "tests/jobs/test_quality.py": "vendor job ids in sample URLs",
    "tests/platform/settings_fixture.py": "random fake source id",
    "tests/platform/test_alert_store.py": "public vendor job ids used as sample values",
    "tests/platform/test_runtime_config.py": "random fake source id",
    "tests/sources/test_granola_auto.py": "fake meeting id",
    "tests/sources/test_granola_clients.py": "fake meeting id",
    "tests/sources/web/test_titlewatch.py": "vendor job ids in sample URLs",
    "tests/contracts/test_privacy.py": "this check's own random sample",
    "tools/orca-concord-room/test/worker.test.mjs": "random fake terminal ids",
}
NAMES = re.compile("Mark" + "unas|Matt Jas" + "per|Fusion" + "92|Austin Con" + "sulting")
NAME_FILES = {
    "widgets/LICENSE": "the copyright holder line of the license text",
    "lifeos/sources/apps/resume_app.py": "the resume file name pattern the app matches; renaming it is a code change",
    "tests/hiring/test_resume_app.py": "the resume file name the app is tested against",
    "tests/contracts/test_privacy.py": "this check's own patterns",
}


def bare_synthetic(value):
    """True for a made-up id: two or fewer distinct digits, mostly zeros, one repeated digit per group (the version and
    variant digit of a UUID aside), or a counting run."""
    digits = value.replace("-", "").lower()
    core = digits[:12] + digits[13:16] + digits[17:]  # without the UUID version and variant digits
    groups = [digits[:8], digits[8:12], digits[12:16][1:], digits[16:20][1:], digits[20:]]
    return (
        len(set(core)) <= 2
        or digits.count("0") >= 18
        or all(len(set(g)) == 1 for g in groups)
        or digits.startswith(("1234", "abcd", "fedc"))
    )


def bare_id_findings(files):
    """-> sorted [(file name, why)] for every bare id or banned name that is not allowed; counts only, never the value."""
    found = []
    for name, text in files:
        if not name.startswith(BITWARDEN_ID_PATHS):
            for match in BARE_ID.finditer(text):
                digits = match.group(1).replace("-", "").lower()
                if bare_synthetic(match.group(1)):
                    continue
                if digits[8:12] == WORKSPACE_MARK:
                    found.append((name, "real Notion id"))
                elif name not in RANDOM_ID_FILES:
                    found.append((name, "unlisted bare id"))
        if NAMES.search(text) and name not in NAME_FILES:
            found.append((name, "personal or employer name"))
    return sorted(found)


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
    """> {file name: how many addresses outside the allowlist}."""
    counts = {}
    for name, text in files:
        n = sum(1 for m in EMAIL.finditer(text) if m.group(1).lower() not in ALLOWED_EMAIL_DOMAINS)
        if n:
            counts[name] = n
    return counts


class PrivacyGuard(unittest.TestCase):
    def test_no_unapproved_emails(self):
        # The failure message names only files and counts.
        self.assertEqual(
            unapproved_email_counts(tracked_text_files()),
            {},
            "email address outside the allowlist in the files above (counts only; see docs/PRIVACY.md)",
        )

    def test_negative_control_a_planted_address_is_caught_and_never_printed(self):
        """prove the guard fires on a personal-looking address AND that its failure output holds none of the value."""
        local, domain = "planted" + "." + "person", "gmail" + "." + "com"
        planted = [("fake/file.txt", "contact: " + local + "@" + domain + " for details\n")]
        self.assertEqual(
            unapproved_email_counts(planted), {"fake/file.txt": 1}
        )  # caught (gmail is not on the allowlist)
        with self.assertRaises(AssertionError) as caught:
            self.assertEqual(
                unapproved_email_counts(planted),
                {},
                "email address outside the allowlist in the files above (counts only; see docs/PRIVACY.md)",
            )
        shown = str(caught.exception)
        self.assertIn("fake/file.txt", shown)  # the file is named, so a human can find it
        for piece in (local, domain, local + "@" + domain, "@"):
            self.assertNotIn(piece, shown)  # none of the matched value is in the failure text

    def test_allowed_domains_do_not_include_personal_mail(self):
        for personal in (
            "gmail.com",
            "googlemail.com",
            "outlook.com",
            "hotmail.com",
            "live.com",
            "icloud.com",
            "yahoo.com",
            "proton.me",
            "protonmail.com",
        ):
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


class BareIdsAndNames(unittest.TestCase):
    """LIFE-1066: the sweep for ids with no URL around them and for personal and employer names"""

    def test_the_repository_has_no_real_notion_id_unlisted_bare_id_or_listed_name(self):
        self.assertEqual(
            bare_id_findings(tracked_text_files()), [], "see docs/PRIVACY.md; use a name token or a synthetic id"
        )

    def test_every_listed_file_still_needs_its_listing(self):
        files = dict(tracked_text_files())
        for table in (RANDOM_ID_FILES, NAME_FILES):
            for name, reason in table.items():
                self.assertIn(name, files, name)
                self.assertTrue(reason.strip(), name)
        for name in RANDOM_ID_FILES:
            self.assertTrue(
                any(not bare_synthetic(m.group(1)) for m in BARE_ID.finditer(files[name])),
                f"{name} lists no bare id any more: remove it from RANDOM_ID_FILES",
            )

    def test_a_real_shaped_notion_id_fails_dashed_or_plain_wherever_it_is(self):
        real = "1a2b3c4d" + "-5926-4e5f-9a8b-7c6d5e4f3a2b"  # made up, with the workspace marker
        for name in ("docs/x.md", "tests/x.py", "tests/contracts/test_workflow.py"):
            for value in (real, real.replace("-", "")):
                self.assertEqual(bare_id_findings([(name, f'ID = "{value}"')]), [(name, "real Notion id")])

    def test_a_random_id_in_a_new_file_fails_but_listed_and_bitwarden_files_pass(self):
        random_id = "9f8e7d6c-5b4a-4938-8271-605f4e3d2c1b"
        self.assertEqual(bare_id_findings([("tests/new.py", random_id)]), [("tests/new.py", "unlisted bare id")])
        self.assertEqual(bare_id_findings([("tests/contracts/test_workflow.py", random_id)]), [])
        self.assertEqual(bare_id_findings([(".github/workflows/domains.yml", random_id)]), [])

    def test_synthetic_ids_pass(self):
        for value in (
            "00000000-0000-0000-0000-000000000001",
            "00000000000000000000000000000abc",
            "11111111111111111111111111111111",
            "11111111-2222-3333-4444-555555555555",
            "1234567890abcdef1234567890abcdef",
        ):
            self.assertEqual(bare_id_findings([("tests/new.py", value)]), [], value)

    def test_a_listed_name_fails_in_a_new_file_and_passes_in_the_listed_ones(self):
        for name_text in ("Jim " + "Mark" + "unas", "Matt " + "Jas" + "per", "Fusion" + "92", "Austin " + "Consulting"):
            self.assertEqual(
                bare_id_findings([("docs/new.md", f"Owner: {name_text}.")]),
                [("docs/new.md", "personal or employer name")],
            )
        self.assertEqual(bare_id_findings([("widgets/LICENSE", "Copyright Mark" + "unas")]), [])
        self.assertEqual(bare_id_findings([("docs/new.md", "Owner: Jim. Employer: a prior employer.")]), [])

    def test_a_finding_never_prints_the_value(self):
        shown = str(bare_id_findings([("docs/x.md", "1a2b3c4d" + "-5926-4e5f-9a8b-7c6d5e4f3a2b Mark" + "unas")]))
        self.assertNotIn("1a2b3c4d", shown)
        self.assertNotIn("Mark" + "unas", shown)


if __name__ == "__main__":
    unittest.main()


class ClockFormatTests(unittest.TestCase):
    """Jim: always AM/PM ('1:30 PM CT'), never 24-hour time, in anything he reads."""

    def test_no_24_hour_display_time_in_the_code(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[2] / "lifeos"
        bad = []
        for path in root.rglob("*.py"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                for match in re.finditer(r"%[Hk]:%M", line):
                    if line[max(0, match.start() - 1) : match.start()] != "T":
                        bad.append(f"{path.relative_to(root)}:{number}")
        self.assertEqual(bad, [])
