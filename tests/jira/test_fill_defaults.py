"""(synthetic, fake Jira): the fill sets only what is missing, by the written rules, once, and readiness fills before it
checks.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lifeos.jira import fill_defaults as fd, readiness_record as rr
from lifeos.platform.jira import JiraError
from lifeos.sources.devtools import readiness
from tests.jira.test_readiness_record import DESC


def epic(key, labels=(), components=("Platform / Core OS",), status="new", summary=None):
    return {
        "key": key,
        "fields": {
            "summary": summary or key,
            "issuetype": {"name": "Epic"},
            "labels": list(labels),
            "components": [{"name": c} for c in components],
            "fixVersions": [{"name": f"{c} 1.0.0"} for c in components],
            "status": {"statusCategory": {"key": status}},
        },
    }


def ticket(key, kind="Task", summary="A ticket", labels=(), parent=None, components=(), versions=()):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "issuetype": {"name": kind},
            "labels": list(labels),
            "parent": {"key": parent} if parent else None,
            "components": [{"name": c} for c in components],
            "fixVersions": [{"name": v} for v in versions],
            "status": {"statusCategory": {"key": "new"}},
        },
    }


class FakeJira:
    """Epics and tickets by key"""

    def __init__(self, epics, tickets, fail_put=False):
        self.items = {x["key"]: x for x in [*epics, *tickets]}
        self.fail_put, self.log, self.next = fail_put, [], 900

    def missing(self, f):
        epicish = (f["issuetype"] or {}).get("name") == "Epic"
        return not f["components"] or not f["fixVersions"] or (not epicish and not f["parent"])

    def get(self, path, params=None):
        if path.endswith("/search/jql"):
            jql = params["jql"]
            epics = jql == "project = LIFE AND issuetype = Epic"
            pick = (
                [x for x in self.items.values() if x["fields"]["issuetype"]["name"] == "Epic"]
                if epics
                else [x for x in self.items.values() if self.missing(x["fields"])]
            )
            return {"issues": [dict(x) for x in pick], "isLast": True}
        return dict(self.items[path.rsplit("/", 1)[-1]])

    def request(self, method, path, params=None, payload=None, retry=None):
        if self.fail_put:
            raise JiraError("JIRA_HTTP_500")
        key = path.rsplit("/", 1)[-1]
        self.log.append(("PUT", key, payload))
        f = self.items[key]["fields"]
        for name, value in payload["fields"].items():
            f[name] = value if name != "parent" else value
        return {}

    def post(self, path, payload, retry=False):
        if path.endswith("/comment"):
            self.log.append(("COMMENT", path.split("/")[-2], payload["body"]["content"][0]["content"][0]["text"]))
            return {}
        self.next += 1
        made = f"LIFE-{self.next}"
        self.log.append(("CREATE", made, payload["fields"]["summary"]))
        self.items[made] = {
            "key": made,
            "fields": {
                **payload["fields"],
                "issuetype": {"name": "Epic"},
                "parent": None,
                "status": {"statusCategory": {"key": "new"}},
            },
        }
        return {"key": made}

    def writes(self):
        return [x for x in self.log if x[0] != "COMMENT"]


def world():
    return [
        epic("LIFE-10", components=("Interview OS",)),
        epic("LIFE-615", labels=["bugs"]),
        epic("LIFE-696", labels=["action-inbox"]),
        epic("LIFE-686"),
        epic("LIFE-585", labels=["los-118"]),
        epic("LIFE-590", labels=["los-118"]),
        epic("LIFE-580", labels=["los-118"], status="done"),
        epic("LIFE-700", labels=["epic-container"], summary="LIFE one-off tasks"),
    ]


class Creation(unittest.TestCase):
    """a new ticket must name its parent epic and takes its Component and Fix Version from it."""

    def test_component_and_version_come_from_the_parent_epic(self):
        jira, sent = FakeJira(world(), []), []
        jira.post = lambda path, payload, retry=False: sent.append(payload["fields"]) or {"key": "LIFE-901"}
        fd.create_issue(jira, "New work", "Task", "LIFE-10")
        fields = sent[0]
        self.assertEqual(fields["parent"], {"key": "LIFE-10"})
        self.assertEqual(fields["components"], [{"name": "Interview OS"}])
        self.assertEqual(fields["fixVersions"], [{"name": "Interview OS 1.0.0"}])

    def test_no_parent_is_a_one_line_refusal_and_nothing_is_written(self):
        jira = FakeJira(world(), [])
        for parent in (None, "", "  "):
            with self.assertRaisesRegex(fd.ParentRequired, "^JIRA_PARENT_REQUIRED$"):
                fd.create_issue(jira, "New work", "Task", parent)
        self.assertEqual(jira.log, [])

    def test_a_parent_that_is_not_an_epic_is_refused(self):
        jira = FakeJira(world(), [ticket("LIFE-50")])
        with self.assertRaisesRegex(fd.ParentRequired, "^JIRA_PARENT_NOT_AN_EPIC$"):
            fd.create_issue(jira, "New work", "Task", "LIFE-50")

    def test_an_epic_needs_no_parent(self):
        jira = FakeJira(world(), [])
        key = fd.create_issue(jira, "New epic", "Epic", None)
        self.assertEqual(jira.items[key]["fields"]["components"], [{"name": fd.DEFAULT_COMPONENT}])


class Rules(unittest.TestCase):
    def test_a_complete_ticket_is_left_alone(self):
        fake = FakeJira(
            world(),
            [ticket("LIFE-901", parent="LIFE-10", components=["Interview OS"], versions=["Interview OS 1.0.0"])],
        )
        out = fd.run(0, True, jira=fake)
        self.assertEqual((out["changed"], fake.writes()), (0, []))

    def test_component_and_fix_version_come_from_the_parent_and_the_sweep_comments_once(self):
        fake = FakeJira(world(), [ticket("LIFE-901", parent="LIFE-10")])
        out = fd.run(0, True, jira=fake)
        self.assertEqual(out["changed"], 1)
        fields = fake.items["LIFE-901"]["fields"]
        self.assertEqual(
            (fields["components"], fields["fixVersions"]),
            ([{"name": "Interview OS"}], [{"name": "Interview OS 1.0.0"}]),
        )
        self.assertEqual(
            [x for x in fake.log if x[0] == "COMMENT"],
            [("COMMENT", "LIFE-901", "[sweep] set Component, Fix Version by rule")],
        )

    def test_a_bug_with_no_parent_goes_under_life_615_and_gets_its_component(self):
        fake = FakeJira(world(), [ticket("LIFE-902", kind="Bug")])
        fd.run(0, True, jira=fake)
        fields = fake.items["LIFE-902"]["fields"]
        self.assertEqual(fields["parent"], {"key": "LIFE-615"})
        self.assertEqual(
            (fields["components"], fields["fixVersions"]),
            ([{"name": "Platform / Core OS"}], [{"name": "Platform / Core OS 1.0.0"}]),
        )
        self.assertIn(
            "[sweep] set parent, Component, Fix Version by rule", [x[2] for x in fake.log if x[0] == "COMMENT"]
        )

    def test_titles_and_labels_pick_the_epic_and_the_retired_los_label_no_longer_routes(self):
        fake = FakeJira(
            world(),
            [
                ticket("LIFE-903", labels=["los-118"]),
                ticket("LIFE-904", labels=["action-inbox"]),
                ticket("LIFE-905", summary="Stability 3/7 clean days"),
            ],
        )
        fd.run(0, True, jira=fake)
        got = {k: fake.items[k]["fields"]["parent"]["key"] for k in ("LIFE-903", "LIFE-904", "LIFE-905")}
        self.assertEqual(
            got, {"LIFE-903": "LIFE-700", "LIFE-904": "LIFE-696", "LIFE-905": "LIFE-686"}
        )  # it falls to the container epic

    def test_no_match_goes_to_the_container_epic_and_it_is_created_once_when_absent(self):
        fake = FakeJira(world(), [ticket("LIFE-906")])
        fd.run(0, True, jira=fake)
        self.assertEqual(fake.items["LIFE-906"]["fields"]["parent"], {"key": "LIFE-700"})
        bare = FakeJira([e for e in world() if e["key"] != "LIFE-700"], [ticket("LIFE-907"), ticket("LIFE-908")])
        fd.run(0, True, jira=bare)
        self.assertEqual([x for x in bare.log if x[0] == "CREATE"], [("CREATE", "LIFE-901", "LIFE one-off tasks")])
        self.assertEqual({bare.items[k]["fields"]["parent"]["key"] for k in ("LIFE-907", "LIFE-908")}, {"LIFE-901"})

    def test_set_values_are_never_overwritten(self):
        fake = FakeJira(
            world(),
            [
                ticket("LIFE-909", parent="LIFE-10", components=["Jobs OS"]),
                ticket("LIFE-910", parent="LIFE-10", components=["Jobs OS"], versions=["Custom 9.9"]),
            ],
        )
        fd.run(0, True, jira=fake)
        one, two = fake.items["LIFE-909"]["fields"], fake.items["LIFE-910"]["fields"]
        self.assertEqual(
            (one["components"], one["fixVersions"], one["parent"]),
            ([{"name": "Jobs OS"}], [{"name": "Jobs OS 1.0.0"}], {"key": "LIFE-10"}),
        )
        self.assertEqual(two["fixVersions"], [{"name": "Custom 9.9"}])

    def test_an_epic_gets_only_component_and_fix_version(self):
        bare = epic("LIFE-911", components=())
        bare["fields"]["fixVersions"] = []
        fake = FakeJira(world(), [])
        fake.items["LIFE-911"] = bare
        fd.run(0, True, jira=fake)
        fields = fake.items["LIFE-911"]["fields"]
        self.assertEqual(
            (fields["components"], fields["fixVersions"], "parent" in fields and fields["parent"]),
            ([{"name": "Platform / Core OS"}], [{"name": "Platform / Core OS 1.0.0"}], False),
        )

    def test_a_second_run_changes_nothing_and_a_dry_run_writes_nothing(self):
        data = [ticket("LIFE-912", parent="LIFE-10"), ticket("LIFE-913", kind="Bug"), ticket("LIFE-914")]
        dry = FakeJira(world(), data)
        out = fd.run(0, False, jira=dry)
        self.assertEqual((out["would_change"], out["changed"], dry.log), (3, 0, []))
        live = FakeJira(world(), data)
        self.assertEqual(fd.run(0, True, jira=live)["changed"], 3)
        before = len(live.log)
        again = fd.run(0, True, jira=live)
        self.assertEqual((again["changed"], len(live.log)), (0, before))

    def test_a_write_failure_fails_the_stage_only_when_nothing_could_be_written(self):
        with self.assertRaises(fd.FillFailed):
            fd.run(0, True, jira=FakeJira(world(), [ticket("LIFE-915", parent="LIFE-10")], fail_put=True))

    def test_backfill_reports_the_gap_counts_before_and_after(self):
        fake = FakeJira(world(), [ticket(f"LIFE-{n}", parent="LIFE-10") for n in range(920, 925)])
        out = fd.backfill(fake, True, sleep=lambda s: None)
        self.assertEqual((out["gaps_before"], out["changed"], out["gaps_after"]), (5, 5, 0))
        self.assertEqual([x for x in fake.log if x[0] == "COMMENT"], [])

    def test_a_saved_read_plans_without_any_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "read.json"
            path.write_text(
                json.dumps(
                    {
                        "issues": [
                            ticket("LIFE-930", kind="Bug"),
                            ticket(
                                "LIFE-931",
                                parent="LIFE-10",
                                components=["Interview OS"],
                                versions=["Interview OS 1.0.0"],
                            ),
                        ],
                        "epics": world(),
                    }
                )
            )
            self.assertEqual(
                fd.plan_file(path),
                {
                    "LIFE-930": {
                        "parent": "LIFE-615",
                        "components": "Platform / Core OS",
                        "fixVersions": "Platform / Core OS 1.0.0",
                    }
                },
            )


class Container(unittest.TestCase):
    """and: only the open epic titled `LIFE one-off tasks` is the container"""

    def labelled(self):
        return {
            e["key"]: e
            for e in [
                epic("LIFE-581", labels=["epic-container"], status="done", summary="V7 scalability"),
                epic("LIFE-615", labels=["epic-container"], summary="Bugs"),
                epic("LIFE-668", labels=["epic-container"], status="done", summary="LIFE one-off tasks"),
            ]
        }

    def test_a_done_or_other_titled_epic_with_the_label_is_never_the_container(self):
        epics = self.labelled()
        self.assertIsNone(fd.find_container(epics))
        self.assertEqual(
            fd.plan(ticket("LIFE-950")["fields"], epics, fd.find_container(epics) or "(new container)")["parent"],
            "(new container)",
        )

    def test_the_open_epic_with_the_title_is_chosen_case_insensitively_and_the_lowest_key_wins(self):
        epics = self.labelled()
        epics["LIFE-720"] = epic("LIFE-720", labels=["epic-container"], summary="  life ONE-OFF tasks ")
        epics["LIFE-721"] = epic("LIFE-721", labels=["epic-container"], summary="LIFE one-off tasks")
        self.assertEqual(fd.find_container(epics), "LIFE-720")
        self.assertEqual(fd.plan(ticket("LIFE-951")["fields"], epics, "LIFE-720")["parent"], "LIFE-720")

    def test_the_title_alone_is_enough_and_a_created_container_carries_no_label(self):
        self.assertEqual(fd.find_container({"LIFE-730": epic("LIFE-730", summary="LIFE one-off tasks")}), "LIFE-730")
        fake = FakeJira([epic("LIFE-615", summary="Bugs")], [ticket("LIFE-960")])
        fd.run(0, True, jira=fake)
        self.assertEqual([x for x in fake.log if x[0] == "CREATE"], [("CREATE", "LIFE-901", "LIFE one-off tasks")])
        self.assertEqual(fake.items["LIFE-901"]["fields"].get("labels") or [], [])

    def test_a_dry_run_names_a_new_container_and_a_live_run_creates_it_once(self):
        fake = FakeJira(
            [
                epic("LIFE-581", labels=["epic-container"], status="done", summary="V7 scalability"),
                epic("LIFE-615", labels=["epic-container"], summary="Bugs"),
            ],
            [ticket("LIFE-952"), ticket("LIFE-953")],
        )
        fd.run(0, False, jira=fake)
        self.assertEqual(fake.writes(), [])
        fd.run(0, True, jira=fake)
        self.assertEqual([x for x in fake.log if x[0] == "CREATE"], [("CREATE", "LIFE-901", "LIFE one-off tasks")])
        self.assertEqual({fake.items[k]["fields"]["parent"]["key"] for k in ("LIFE-952", "LIFE-953")}, {"LIFE-901"})


class ReadinessFill(unittest.TestCase):
    def client(self, fail=False):
        fake = FakeJira(world(), [ticket("LIFE-901", parent="LIFE-10")], fail_put=fail)
        original = fake.get

        def get(path, params=None):
            got = original(path, params)
            if path.endswith("/issue/LIFE-901"):
                got["fields"] = {
                    **got["fields"],
                    "description": DESC,
                    "labels": ["lane-career-v7", "ready"],
                    "issuelinks": [],
                    "parent": {"key": "LIFE-10", "fields": {"status": {"name": "Ready"}}},
                }
            return got

        fake.get = get
        return fake

    def test_readiness_fills_before_it_checks_and_is_then_ready(self):
        fake = self.client()
        with patch.object(rr.Jira, "from_env", return_value=fake):
            refusals, warnings, gap = readiness.jira_issue("issue-key", "LIFE-901")
        self.assertEqual((refusals, gap), ([], None))
        self.assertNotIn("component_missing", [w["code"] for w in warnings])
        self.assertEqual(fake.items["LIFE-901"]["fields"]["components"], [{"name": "Interview OS"}])

    def test_a_failed_jira_write_is_the_one_fill_failed_refusal(self):
        with patch.object(rr.Jira, "from_env", return_value=self.client(fail=True)):
            refusals, _, gap = readiness.jira_issue("issue-key", "LIFE-901")
        self.assertEqual(([r["code"] for r in refusals], gap), (["fill_failed"], None))


if __name__ == "__main__":
    unittest.main()
