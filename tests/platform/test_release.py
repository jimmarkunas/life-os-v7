import unittest

from lifeos.platform import release

SHA = "a" * 40
GOOD = {"GITHUB_REF": "refs/heads/main", "GITHUB_SHA": SHA, "GITHUB_RUN_ID": "77", "GITHUB_REPOSITORY": "o/r", "GITHUB_SERVER_URL": "https://github.com",
        "RELEASE_TICK": "true", "RELEASE_LIVE": "true", "RELEASE_FAILED": "", "RELEASE_PUBLISH": "true",
        "RELEASE_JOBS": "prep=success,jobright=success,linkedin=success,lensa=success,dice=success,finish=success"}
RUN_URL = "https://github.com/o/r/actions/runs/77"


class GitHub:
    """A tiny in-memory GitHub Deployments API: records every call and keeps the deployments it is asked to create."""

    def __init__(self, message="JFM-199 Add deployment reporting (#251)", deployments=None, statuses=None):
        self.calls, self.deployments, self.statuses, self.message = [], list(deployments or []), dict(statuses or {}), message

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path, body))
        if path.startswith("deployments?") and method == "GET":
            return [d for d in self.deployments if d["sha"] == path.split("sha=")[1].split("&")[0]]
        if path.startswith("commits/"):
            return {"commit": {"message": self.message}}
        if path == "deployments" and method == "POST":
            made = {"id": 900 + len(self.deployments), "sha": body["ref"]}
            self.deployments.append(made)
            return made
        if path.endswith("/statuses?per_page=100"):
            return self.statuses.get(int(path.split("/")[1]), [])
        if path.endswith("/statuses") and method == "POST":
            self.statuses.setdefault(int(path.split("/")[1]), []).append(body)
            return body
        raise AssertionError(path)

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m == "POST"]


class Release(unittest.TestCase):
    def test_a_verified_live_tick_records_one_production_deployment_for_that_commit_and_run(self):
        api = GitHub()
        self.assertEqual(release.run(GOOD, api), "RECORDED")
        (_, _, deployment), (_, _, status) = [c for c in api.calls if c[0] == "POST"]
        self.assertEqual((deployment["ref"], deployment["environment"], deployment["production_environment"], deployment["transient_environment"]), (SHA, "production", True, False))
        self.assertEqual((deployment["auto_merge"], deployment["required_contexts"]), (False, []))
        self.assertEqual(deployment["payload"], {"run_url": RUN_URL, "jira": ["JFM-199"]})
        self.assertEqual((status["state"], status["environment"], status["log_url"]), ("success", "production", RUN_URL))

    def test_roadmap_ids_are_not_jira_keys(self):
        api = GitHub("JOBS-1.1A and EDGE-1.3 with JFM-12, JFM-7 and JFM-12 (#9)")
        release.run(GOOD, api)
        self.assertEqual(api.calls[-2][2]["payload"]["jira"], ["JFM-12", "JFM-7"])

    def test_the_next_hourly_tick_of_the_same_commit_writes_nothing(self):
        api = GitHub()
        release.run(GOOD, api)
        before = len(api.writes())
        self.assertEqual([release.run(GOOD, api) for _ in range(3)], ["ALREADY_RECORDED"] * 3)
        self.assertEqual(len(api.writes()), before)
        self.assertEqual(len(api.deployments), 1)

    def test_a_newer_commit_is_a_new_release_and_the_old_one_stays_recorded_once(self):
        api = GitHub()
        release.run(GOOD, api)
        self.assertEqual(release.run({**GOOD, "GITHUB_SHA": "b" * 40}, api), "RECORDED")
        self.assertEqual([d["sha"] for d in api.deployments], [SHA, "b" * 40])

    def test_a_deployment_left_without_a_success_status_is_completed_not_duplicated(self):
        api = GitHub(deployments=[{"id": 5, "sha": SHA}], statuses={5: [{"state": "in_progress"}]})
        self.assertEqual(release.run(GOOD, api), "RECORDED")
        self.assertEqual(api.writes(), [("POST", "deployments/5/statuses")])

    def test_an_older_success_that_was_marked_inactive_still_counts_as_recorded(self):
        api = GitHub(deployments=[{"id": 5, "sha": SHA}], statuses={5: [{"state": "inactive"}, {"state": "success"}]})
        self.assertEqual(release.run(GOOD, api), "ALREADY_RECORDED")
        self.assertEqual(api.writes(), [])

    def test_publication_is_off_until_the_repo_variable_is_true_and_then_nothing_is_written(self):
        for value in (None, "", "false", "yes"):
            api = GitHub()
            self.assertEqual(release.run({**GOOD, "RELEASE_PUBLISH": value} if value is not None else {k: v for k, v in GOOD.items() if k != "RELEASE_PUBLISH"}, api), "OBSERVED")
            self.assertEqual(api.writes(), [])

    def test_anything_short_of_a_verified_live_tick_of_main_is_unverified_and_touches_nothing(self):
        cases = {
            "pull request CI alone": ({"GITHUB_REF": "refs/pull/251/merge", "RELEASE_TICK": "false"}, "NOT_MAIN"),
            "a branch dispatch": ({"GITHUB_REF": "refs/heads/claude/x"}, "NOT_MAIN"),
            "a manual run": ({"RELEASE_TICK": "false"}, "NOT_TICK"),
            "a dry run": ({"RELEASE_LIVE": "false"}, "NOT_LIVE"),
            "no live flag": ({"RELEASE_LIVE": ""}, "NOT_LIVE"),
            "a failed lane": ({"RELEASE_JOBS": GOOD["RELEASE_JOBS"].replace("dice=success", "dice=failure")}, "JOBS_NOT_SUCCESS"),
            "a skipped finish": ({"RELEASE_JOBS": GOOD["RELEASE_JOBS"].replace("finish=success", "finish=skipped")}, "JOBS_NOT_SUCCESS"),
            "a cancelled run": ({"RELEASE_JOBS": GOOD["RELEASE_JOBS"].replace("prep=success", "prep=cancelled")}, "JOBS_NOT_SUCCESS"),
            "no job results": ({"RELEASE_JOBS": ""}, "JOBS_NOT_SUCCESS"),
            "a failed stage": ({"RELEASE_FAILED": "publish"}, "STAGES_FAILED"),
        }
        for name, (change, code) in cases.items():
            api = GitHub()
            self.assertEqual(release.run({**GOOD, **change}, api), f"UNVERIFIED:{code}", name)
            self.assertEqual(api.calls, [], name)

    def test_a_missing_variable_fails_closed_instead_of_guessing(self):
        for key in ("GITHUB_REF", "RELEASE_TICK", "RELEASE_LIVE", "RELEASE_JOBS"):
            api = GitHub()
            self.assertTrue(release.run({k: v for k, v in GOOD.items() if k != key}, api).startswith("UNVERIFIED:"), key)
            self.assertEqual(api.calls, [], key)

    def test_an_api_failure_raises_and_leaves_the_known_good_state(self):
        class Refused(GitHub):
            def __call__(self, path, method="GET", body=None):
                if method == "POST" and path.endswith("/statuses"):
                    raise OSError("HTTP 403")
                return super().__call__(path, method, body)
        api = Refused()
        with self.assertRaises(OSError):
            release.run(GOOD, api)
        self.assertNotIn("success", [s["state"] for v in api.statuses.values() for s in v])          # no success was recorded, so the next tick retries the same commit
        self.assertEqual(release.run(GOOD, GitHub(deployments=api.deployments)), "RECORDED")


if __name__ == "__main__":
    unittest.main()
