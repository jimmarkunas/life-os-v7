"""LIFE-699: V7 settings from one Notion page (shadow rule), and no new GitHub value."""
import re
import unittest
from pathlib import Path

from lifeos.platform import settings

ROOT = Path(__file__).resolve().parents[2]
ID = "[removed]"

# Every GitHub secret or variable the workflows use (Nov 14, 2026). This list may only shrink: LIFE-699 moves each one to the Notion settings page or Bitwarden,
# and the end state is LIFEOS_BWS_RUNTIME_TOKEN alone (GITHUB_TOKEN is GitHub's own and never counted). Adding a name here needs Jim's typed approval.
FROZEN = frozenset({"ALERTS_ENABLED", "ALERTS_MUTED_UNTIL", "ALERT_SETTINGS", "AMAZON_CARD_BLOCK_ID", "ATTENTION_CARD_BLOCK_ID", "BILLS_CARD_BLOCK_ID", "BROWSER_CACHE_BUST", "BUZZ_STAGE_CHANNEL", "BUZZ_STAGE_HERMES_PUBKEY", "BUZZ_STAGE_KEY", "BUZZ_URL_LIFE_OS", "CALENDAR_CARD_BLOCK_ID", "FIT_PROFILE_SYNC", "GATE_MIN_GAP_MINUTES", "GCAL_CALENDAR_ID", "GMAIL_OAUTH_CLIENT_ID", "GRANOLA_AUTO", "HIRING_AUTO_CLOSE", "HIRING_CARD_BLOCK_ID", "HIRING_PIPELINE_PAGE_ID", "HIRING_STATUS_BLOCK_ID", "HIRING_TABLE_BLOCK_ID", "INTERVIEW_ACCEPTANCE_JSON", "INTERVIEW_ACCEPTANCE_NEGATIVE_JSON", "INTERVIEW_CONVERT", "INTERVIEW_CONVERT_CAP", "INTERVIEW_LEADS", "INTERVIEW_OS_WRITER", "INTERVIEW_TEST_COMPANIES", "JIRA_BASE_URL", "JIRA_BOARDS", "JIRA_CARD_BLOCK_ID", "JIRA_CARD_PROJECTS", "JIRA_GTV_CONTEXT_URL", "JIRA_GTV_EPIC", "JIRA_SITE_URL", "JOB_PAGE_OUTREACH", "LIFEOS_ACQ_SSH_PORT", "LIFEOS_BWS_RUNTIME_TOKEN", "LIFEOS_NOTION_IDS", "MAIL_ALERTS_CARD_BLOCK_ID", "MEGIBOW_BLOCK_ID", "MEGIBOW_EXCLUDED_DOMAINS", "MEGIBOW_REVIEW_DB_ID", "NETWORK_HANDOFF_PAGE_ID", "NETWORK_SURFACE_SKIP", "NOTION_AMAZON_DATA_SOURCE_ID", "NOTION_BILLS_DATA_SOURCE_ID", "NOTION_FIT_SYNC_PAGE_ID", "NOTION_FIT_SYNC_TOKEN", "NOTION_JOB_LEDGER_DATA_SOURCE_ID", "NOTION_RECRUITERS_DATA_SOURCE_ID", "OUTLOOK_CLIENT_ID", "PHYSICAL_MAIL_EXPECT", "RECRUITER_OUTREACH", "TICK_CAP_ATS", "TICK_CAP_DICE", "TICK_CAP_JOBRIGHT", "TICK_CAP_LINKEDIN", "TICK_CAP_TINYFISH", "TICK_MAX_PARALLEL", "V7_AI_CAPACITY_CARD", "V7_ARCHIVE_TOKEN", "V7_CLOSE_BASE_URL", "V7_CLOUD_FIT", "V7_CLOUD_FIT_BACKLOG_BUDGET", "V7_CLOUD_FIT_BACKLOG_MAX", "V7_CLOUD_FIT_BACKLOG_PAGE", "V7_CLOUD_FIT_PARALLEL", "V7_CLOUD_FIT_PROFILE_REF", "V7_CLOUD_FIT_SCHEDULED", "V7_FIT_GATE", "V7_IMESSAGE_ALERTS", "V7_LIVE", "V7_PIPELINE", "V7_STORE_JUMP_HOST", "V7_STORE_JUMP_KNOWN_HOSTS", "V7_STORE_VIA", "WEEKLY_REVIEW_PAGE_ID"})


def used():
    return {m.group(2) for path in (ROOT / ".github").rglob("*.yml") for m in re.finditer(r"\b(vars|secrets)\.([A-Z0-9_]+)", path.read_text())} - {"GITHUB_TOKEN"}


class NoNewGitHubValue(unittest.TestCase):
    def test_no_workflow_uses_a_github_value_outside_the_frozen_list(self):
        self.assertEqual(sorted(used() - FROZEN), [], "put a new setting on the Notion page 'V7 settings (machine read)' (lifeos/platform/settings.py) or in Bitwarden, never in GitHub")

    def test_the_frozen_list_only_shrinks(self):
        self.assertEqual(sorted(FROZEN - used()), [], "a name the workflows no longer use: delete it from FROZEN (and from GitHub, with Jim's yes)")

    def test_a_safety_value_is_never_movable(self):
        self.assertEqual(settings.SAFETY & settings.MOVABLE, frozenset())


class Page:
    def __init__(self, lines, title=settings.TITLE, broken=False):
        self.lines, self.title, self.broken = lines, title, broken

    def call(self, method, path, body=None):
        if self.broken:
            raise RuntimeError("down")
        if path == "/search":
            return {"results": [{"id": "p", "properties": {"title": {"type": "title", "title": [{"plain_text": self.title}]}}}]}
        if path.startswith("/blocks/p/children"):
            return {"results": [{"id": "c", "type": "code", "code": {"rich_text": [{"plain_text": "\n".join(self.lines)}]}}], "has_more": False}
        raise AssertionError(path)


class ShadowRule(unittest.TestCase):
    def test_notion_fills_only_what_the_environment_lacks_and_compares_the_rest(self):
        env = {"HIRING_AUTO_CLOSE": "on", "JIRA_CARD_BLOCK_ID": "x" * 32}
        page = Page(["# addresses", f"MEGIBOW_BLOCK_ID {ID}", f"JIRA_CARD_BLOCK_ID {ID}", "# switches", "HIRING_AUTO_CLOSE on", "INTERVIEW_TEST_COMPANIES Smartsheet, Walmart"])
        out = settings.apply(env, page)
        self.assertEqual(env["MEGIBOW_BLOCK_ID"], ID)
        self.assertEqual(env["INTERVIEW_TEST_COMPANIES"], "Smartsheet, Walmart")
        self.assertEqual(env["JIRA_CARD_BLOCK_ID"], "x" * 32)                   # GitHub still holds it: never overwritten, only compared
        self.assertEqual((out["status"], out["filled"], out["matched"], out["mismatched"]), ("ok", ["INTERVIEW_TEST_COMPANIES", "MEGIBOW_BLOCK_ID"], 1, ["JIRA_CARD_BLOCK_ID"]))

    def test_safety_values_credentials_unknown_and_bad_addresses_are_never_applied(self):
        env = {}
        out = settings.apply(env, Page(["V7_LIVE true", "ALERTS_ENABLED off", "CLOUDFLARE_API_TOKEN abc", "SOMETHING_NEW 1", "MEGIBOW_BLOCK_ID not-an-id", "MEGIBOW_BLOCK_ID " + ID, "MEGIBOW_BLOCK_ID " + ID]))
        self.assertEqual(env, {"MEGIBOW_BLOCK_ID": ID})
        self.assertEqual((out["refused"], out["unknown"], out["invalid"], out["repeated"]), (["V7_LIVE", "ALERTS_ENABLED", "CLOUDFLARE_API_TOKEN"], 1, ["MEGIBOW_BLOCK_ID"], ["MEGIBOW_BLOCK_ID"]))

    def test_an_unreadable_or_missing_page_changes_nothing(self):
        env = {"HIRING_AUTO_CLOSE": "on"}
        self.assertEqual(settings.apply(env, Page([], broken=True))["status"], "unreadable:RuntimeError")
        self.assertEqual(settings.apply(env, Page(["GRANOLA_AUTO on"], title="Something else"))["status"], "unreadable:SETTINGS_PAGE_NOT_FOUND")
        self.assertEqual(env, {"HIRING_AUTO_CLOSE": "on"})

    def test_off_outside_production_and_without_a_token(self):
        self.assertEqual(settings.apply({"LIFEOS_SETTINGS": "off"}, Page(["GRANOLA_AUTO on"]))["status"], "off")
        self.assertEqual(settings.apply({"NOTION_API_TOKEN": "t"})["status"], "off")             # not GitHub Actions, not switched on: no Notion call at all
        self.assertEqual(settings.apply({"GITHUB_ACTIONS": "true"})["status"], "no_token")


if __name__ == "__main__":
    unittest.main()
