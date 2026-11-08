import json
import unittest
from datetime import datetime, timedelta, timezone

from lifeos.platform import access_policy as A

AUD, CHAL, STRANGER = "73b7" + "0" * 60, "b1ad" + "0" * 60, "ee" * 32
DOC = {"version": 1, "bulk_limit": 10,
       "default": {"jira": "off", "notion": "off", "github": "off"},
       "agents": {AUD: {"name": "Council Auditor", "jira": "read", "notion": "write", "github": "read"},
                  CHAL: {"name": "Council Challenger", "jira": "read", "notion": "read", "github": "off"}}}


def policy(**patch):
    return A.load(json.dumps({**DOC, **patch}))


class Decide(unittest.TestCase):
    def test_levels_decide_reads_and_writes(self):
        p = policy()
        self.assertEqual(A.decide(p, AUD, "notion", "read"), ("allow", "read_allowed"))
        self.assertEqual(A.decide(p, AUD, "notion", "write"), ("allow", "write_allowed"))
        self.assertEqual(A.decide(p, CHAL, "notion", "read")[0], "allow")
        self.assertEqual(A.decide(p, CHAL, "notion", "write"), ("deny", "write_not_granted"))
        self.assertEqual(A.decide(p, CHAL, "github", "read"), ("deny", "service_off"))

    def test_jira_and_github_are_read_only_in_v1_even_when_the_file_says_write(self):
        p = policy(agents={AUD: {"jira": "write", "notion": "write", "github": "write"}})
        for service in ("jira", "github"):
            self.assertEqual(A.decide(p, AUD, service, "read")[0], "allow")
            self.assertEqual(A.decide(p, AUD, service, "write"), ("deny", "service_read_only_in_v1"))
        self.assertEqual(A.level_of(p, AUD, "jira"), "read")

    def test_deletes_and_bulk_changes_wait_for_approval(self):
        p = policy()
        self.assertEqual(A.decide(p, AUD, "notion", "delete"), ("pending", "delete_needs_approval"))
        self.assertEqual(A.decide(p, AUD, "notion", "bulk"), ("pending", "bulk_needs_approval"))
        self.assertEqual(A.decide(p, AUD, "notion", "write", count=10)[0], "allow")
        self.assertEqual(A.decide(p, AUD, "notion", "write", count=11), ("pending", "bulk_needs_approval"))
        self.assertEqual(A.decide(p, CHAL, "notion", "delete"), ("deny", "write_not_granted"))     # no write level, no approval path

    def test_unknown_agents_get_the_default_and_the_default_is_off(self):
        p = policy()
        self.assertEqual(A.decide(p, STRANGER, "notion", "read"), ("deny", "service_off"))
        loose = policy(default={"jira": "read", "notion": "off", "github": "off"})
        self.assertEqual(A.decide(loose, STRANGER, "jira", "read")[0], "allow")

    def test_everything_malformed_is_a_deny(self):
        p = policy()
        self.assertEqual(A.decide(None, AUD, "notion", "read"), ("deny", "policy_unreadable"))
        for args in ((AUD, "hostinger", "read"), (AUD, "notion", "erase"), ("not-a-key", "notion", "read"), (None, "notion", "read"), (AUD, "notion", "write", 0), (AUD, "notion", "write", True)):
            self.assertEqual(A.decide(p, *args)[0], "deny", args)


class Load(unittest.TestCase):
    def test_bad_documents_raise(self):
        bad = ["", "[]", "{", json.dumps({**DOC, "version": 2}), json.dumps({**DOC, "bulk_limit": 0}), json.dumps({**DOC, "bulk_limit": "10"}),
               json.dumps({**DOC, "agents": {"short": {}}}), json.dumps({**DOC, "agents": {AUD: {"notion": "admin"}}}),
               json.dumps({**DOC, "agents": {AUD: {"hostinger": "write"}}}), json.dumps({**DOC, "default": []})]
        for text in bad:
            with self.assertRaises(A.PolicyError, msg=text):
                A.load(text)

    def test_a_missing_service_means_off(self):
        p = A.load(json.dumps({"version": 1, "agents": {AUD: {"notion": "read"}}}))
        self.assertEqual(A.level_of(p, AUD, "jira"), "off")
        self.assertEqual(p["bulk_limit"], 10)


class Audit(unittest.TestCase):
    def test_a_line_has_central_time_and_no_free_text(self):
        now = datetime(2026, 11, 8, 16, 30, tzinfo=timezone.utc)
        line = A.audit_line(AUD, "notion", "notion-update-page", "pending", "bulk_needs_approval", approver="jim", now=now)
        self.assertEqual(line["t"], "2026-11-08T10:30:00-06:00")
        self.assertEqual((line["service"], line["op"], line["decision"], line["approver"]), ("notion", "notion-update-page", "pending", "jim"))
        dirty = A.audit_line(AUD, "notion", "page: secret text\nwith breaks", "deny", "x" * 200)
        self.assertNotIn("\n", json.dumps(dirty).replace("\\n", ""))
        self.assertLessEqual(len(dirty["reason"]), 40)
        self.assertNotIn(" ", dirty["op"])


if __name__ == "__main__":
    unittest.main()
