"""and (synthetic): the auto-release rule moves only what is ready, and the stall check names only tickets idle without a
reason.
"""

import unittest
from datetime import datetime, timezone

from lifeos.jira import release, stalled
from lifeos.platform.jira import JiraError

DONE_WHEN = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Jim must do first: None"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Done when"}]},
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Given X, then Y"}]}],
                }
            ],
        },
    ],
}
OPEN_JIM_FIRST = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Jim must do first"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "[ ] Approve the spend"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Done when"}]},
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Given X, then Y"}]}],
                }
            ],
        },
    ],
}
NEEDS_API_KEY = {
    "type": "doc",
    "content": DONE_WHEN["content"]
    + [{"type": "paragraph", "content": [{"type": "text", "text": "Needs a read-only API key for Jira."}]}],
}
NO_DONE_WHEN = {
    "type": "doc",
    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Just do the thing."}]}],
}


def issue(key, description=DONE_WHEN, labels=("ready",), blockers=(), parent="LIFE-10", epic="Ready"):
    links = [
        {
            "type": {"name": "Blocks"},
            "inwardIssue": {"key": b, "fields": {"status": {"statusCategory": {"key": "done" if done else "new"}}}},
        }
        for b, done in blockers
    ]
    return {
        "key": key,
        "fields": {
            "summary": key,
            "description": description,
            "labels": list(labels),
            "issuelinks": links,
            "parent": {"key": parent, "fields": {"status": {"name": epic}}} if epic else {"key": parent},
        },
    }


class FakeJira:
    def __init__(self, epics, children, fail_post=False, comments=None, found=None):
        self.epics, self.children, self.fail_post, self.posts, self.comments, self.found = (
            epics,
            children,
            fail_post,
            [],
            comments or {},
            found or [],
        )

    def get(self, path, params=None):
        if path.endswith("/search/jql"):
            jql = params["jql"]
            if "issuetype = Epic AND labels" in jql:
                return {"issues": [{"key": k} for k in self.epics], "isLast": True}
            if "parent in" in jql:
                return {"issues": self.children, "isLast": True}
            return {"issues": self.found, "isLast": True}
        key = path.split("/")[-2]
        return (
            {
                "comments": [
                    {
                        "body": {
                            "type": "doc",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": self.comments[key]}]}
                            ],
                        }
                    }
                ]
            }
            if key in self.comments
            else {"comments": []}
        )

    def put(self, path, payload, retry=False):
        self.posts.append((path, payload))
        return {}

    def post(self, path, payload, retry=False):
        if self.fail_post:
            raise JiraError("JIRA_HTTP_500")
        self.posts.append((path, payload))
        return {}


class Release(unittest.TestCase):
    def test_a_wait_only_build_ticket_is_refused_with_the_uat_message_once(self):
        kid = issue("LIFE-12")
        kid["fields"]["summary"] = "Watch the nightly runs for 7 days"
        fake = FakeJira(["LIFE-10"], [kid])
        dry = release.run(0, False, jira=fake)
        self.assertEqual((dry["skipped"], fake.posts), ({"wait_only_not_uat": 1}, []))  # a dry run says nothing
        out = release.run(0, True, jira=fake)
        self.assertEqual((out["released"], out["messaged"]), (0, 1))
        ((path, payload),) = fake.posts
        self.assertTrue(path.endswith("/issue/LIFE-12/comment"))
        said = payload["body"]["content"][0]["content"][0]["text"]
        self.assertEqual(said, "[agent-v7] File as UAT for LIFE-12 under the Bug Epic.")
        again = FakeJira(["LIFE-10"], [kid], comments={"LIFE-12": said})
        out = release.run(0, True, jira=again)
        self.assertEqual((out["messaged"], again.posts), (0, []))  # said once, never twice

    def test_a_ready_child_of_an_approved_epic_is_moved_once_with_one_comment(self):
        fake = FakeJira(["LIFE-10"], [issue("LIFE-11")])
        out = release.run(0, True, jira=fake)
        self.assertEqual((out["released"], out["epics"]), (1, 1))
        self.assertEqual(
            [p for p, _ in fake.posts], ["/rest/api/3/issue/LIFE-11/transitions", "/rest/api/3/issue/LIFE-11/comment"]
        )
        self.assertEqual(fake.posts[0][1], {"transition": {"id": "17"}})

    def test_a_ticket_naming_a_protected_step_is_held_for_jim_not_released_and_a_dry_run_changes_nothing(self):
        fake = FakeJira(["LIFE-10"], [issue("LIFE-11", description=NEEDS_API_KEY)])
        dry = release.run(0, False, jira=fake)
        self.assertEqual((dry["skipped"], dry["would_release"], fake.posts), ({"protected": 1}, 0, []))
        out = release.run(0, True, jira=fake)
        self.assertEqual((out["released"], out["skipped"]), (0, {"protected": 1}))
        self.assertEqual([p for p, _ in fake.posts], ["/rest/api/3/issue/LIFE-11", "/rest/api/3/issue/LIFE-11/comment"])
        self.assertEqual(fake.posts[0][1], {"update": {"labels": [{"remove": "ready"}, {"add": "needs-jim"}]}})
        self.assertIn("[waiting on] Jim: approve credential", str(fake.posts[1][1]))

    def test_nothing_is_released_for_an_open_blocker_a_missing_done_when_or_a_skip_label(self):
        children = [
            issue("LIFE-11", blockers=[("LIFE-5", False)]),
            issue("LIFE-12", description=NO_DONE_WHEN),
            issue("LIFE-13", labels=["needs-jim"]),
            issue("LIFE-14", description=OPEN_JIM_FIRST),
            issue("LIFE-15", blockers=[("LIFE-5", True)]),
        ]
        fake = FakeJira(["LIFE-10"], children)
        out = release.run(0, True, jira=fake)
        self.assertEqual(
            out["skipped"], {"blocked_by_open": 1, "no_done_when": 1, "not_ready_label": 1, "jim_first_open": 1}
        )
        self.assertEqual((out["released"], [p for p, _ in fake.posts][0]), (1, "/rest/api/3/issue/LIFE-15/transitions"))

    def test_only_a_child_with_exactly_one_ready_label_is_released(self):
        """(policy section 7): needs-jim, needs-prep, no readiness label and two readiness labels each stop a release
        with their own fixed reason
        """
        for labels, reason in (
            (["needs-jim", "lane-platform"], "not_ready_label"),
            (["needs-prep"], "not_ready_label"),
            (["lane-platform", "agent-claude-code"], "no_readiness_label"),
            ([], "no_readiness_label"),
            (["ready", "needs-jim"], "two_readiness_labels"),
            (["ready", "needs-prep"], "two_readiness_labels"),
        ):
            out = release.run(0, True, jira=FakeJira(["LIFE-10"], [issue("LIFE-11", labels=labels)]))
            self.assertEqual((out["released"], out["skipped"]), (0, {reason: 1}), labels)
        out = release.run(
            0,
            True,
            jira=FakeJira(["LIFE-10"], [issue("LIFE-12", labels=["ready", "lane-platform", "agent-claude-code"])]),
        )
        self.assertEqual(out["released"], 1)

    def test_an_epic_in_scoping_releases_only_a_research_or_verdict_child_and_an_unread_epic_status_releases_nothing(
        self,
    ):
        """epic_scoping for build work"""
        kids = [
            issue("LIFE-11", epic="Scoping"),
            issue("LIFE-12", labels=["ready", "research"], epic="Scoping"),
            issue("LIFE-13", labels=["ready", "verdict"], epic="Scoping"),
            issue("LIFE-14", epic=None),
            issue("LIFE-15", epic="Ready"),
            issue("LIFE-16", epic="In Progress"),
        ]
        out = release.run(0, False, jira=FakeJira(["LIFE-10"], kids))
        self.assertEqual(
            (out["would_release"], out["skipped"]), (3, {"epic_scoping": 1, "epic_status_unread": 1, "over_limit": 1})
        )  # four are eligible, RELEASE_LIMIT is three
        for key, labels, epic, why in (
            ("A", ["ready"], "Scoping", "epic_scoping"),
            ("B", ["ready", "research"], "Scoping", None),
            ("C", ["ready", "verdict"], "Scoping", None),
            ("D", ["ready"], "Ready", None),
            ("E", ["ready", "research"], "Ready", None),
        ):
            self.assertEqual(release.eligible(issue(key, labels=labels, epic=epic)), why, key)
        self.assertEqual(
            release.eligible(issue("F", labels=["research"], epic="Scoping")), "no_readiness_label"
        )  # the exception does not waive the readiness label
        self.assertEqual(
            release.eligible(issue("G", labels=["ready", "research"], epic="Scoping", blockers=[("LIFE-5", False)])),
            "blocked_by_open",
        )  # nor an open blocker

    def test_an_unfinished_blocking_link_always_stops_a_release_even_without_any_label(self):
        """blocking is by Blocks links only."""
        fake = FakeJira(
            ["LIFE-10"],
            [
                issue("LIFE-13", blockers=[("LIFE-5", False)]),
                issue("LIFE-14", blockers=[("LIFE-5", True), ("LIFE-6", False)]),
            ],
        )
        out = release.run(0, True, jira=fake)
        self.assertEqual((out["released"], out["skipped"], fake.posts), (0, {"blocked_by_open": 2}, []))

    def test_at_most_three_a_run_in_rank_order_and_a_dry_run_writes_nothing(self):
        children = [issue(f"LIFE-{n}") for n in range(20, 26)]
        live = FakeJira(["LIFE-10"], children)
        out = release.run(0, True, jira=live)
        self.assertEqual((out["released"], out["skipped"]), (3, {"over_limit": 3}))
        self.assertEqual(
            [p.split("/")[-2] for p, _ in live.posts if p.endswith("transitions")], ["LIFE-20", "LIFE-21", "LIFE-22"]
        )
        dry = FakeJira(["LIFE-10"], children)
        out = release.run(0, False, jira=dry)
        self.assertEqual((out["released"], out["would_release"], dry.posts), (0, 3, []))

    def test_no_approved_epic_means_nothing_happens_and_a_write_failure_fails_the_step(self):
        self.assertEqual(release.run(0, True, jira=FakeJira([], [issue("LIFE-11")]))["epics"], 0)
        with self.assertRaises(JiraError):
            release.run(0, True, jira=FakeJira(["LIFE-10"], [issue("LIFE-11")], fail_post=True))

    def test_a_done_when_line_is_recognised_in_a_label_style_or_a_heading_style_body(self):
        inline = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Done when: Given a, then b."}]}],
        }
        self.assertTrue(release.has_done_when(inline))
        self.assertTrue(release.has_done_when(DONE_WHEN))
        self.assertFalse(release.has_done_when(NO_DONE_WHEN))
        self.assertFalse(release.has_done_when(None))


class Stalled(unittest.TestCase):
    NOW = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)

    def issue(self, key, status, minutes_ago, labels=()):
        moment = self.NOW.timestamp() - minutes_ago * 60
        text = datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        return {"key": key, "fields": {"status": {"name": status}, "updated": text, "labels": list(labels)}}

    def test_idle_in_progress_tickets_without_a_waiting_note_are_named_and_the_rest_are_not(self):
        found = [
            self.issue("LIFE-1", "In Progress", 45),
            self.issue("LIFE-2", "In Progress", 10),
            self.issue("LIFE-3", "In Progress", 90),
            self.issue("LIFE-4", "Agent Go", 60),
            self.issue("LIFE-5", "Agent Go", 200),
            self.issue("LIFE-6", "In Progress", 300, labels=["needs-jim"]),
        ]
        fake = FakeJira([], [], comments={"LIFE-3": "[waiting on] Jim: approve the label"}, found=found)
        out = stalled.run(0, False, jira=fake, now=self.NOW)
        self.assertEqual((out["stalled"], out["keys"]), (2, ["LIFE-1", "LIFE-5"]))

    def test_an_unreadable_jira_is_an_error_not_an_empty_list(self):
        class Down:
            def get(self, path, params=None):
                raise JiraError("JIRA_NETWORK")

        with self.assertRaises(JiraError):
            stalled.run(0, False, jira=Down(), now=self.NOW)


if __name__ == "__main__":
    unittest.main()
