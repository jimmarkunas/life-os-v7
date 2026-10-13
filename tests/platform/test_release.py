import contextlib
import io
import re
import unittest

from lifeos.platform import release

HEAD = "c" * 40
GOOD = {"GITHUB_REF": "refs/heads/main", "GITHUB_SHA": HEAD, "GITHUB_RUN_ID": "77", "GITHUB_REPOSITORY": "o/r", "GITHUB_SERVER_URL": "https://github.com",
        "RELEASE_TICK": "true", "RELEASE_LIVE": "true", "RELEASE_FAILED": "", "RELEASE_PUBLISH": "true",
        "RELEASE_JOBS": "prep=success,jobright=success,linkedin=success,lensa=success,dice=success,finish=success"}
RUN_URL = "https://github.com/o/r/actions/runs/77"


def commit(sha, message, *files):
    return {"sha": sha, "commit": {"message": message}, "files": [{"filename": f} for f in files]}


# oldest -> newest; "a" is the last recorded release, "b" a docs commit, HEAD the tick's commit
HISTORY = [commit("a" * 40, "JFM-100 earlier release (#1)", "lifeos/x.py"), commit("b" * 40, "docs: note", "docs/NOTES.md"), commit(HEAD, "docs: more", "docs/MORE.md")]


def page_of(path, items):
    page = int((re.search(r"[?&]page=(\d+)", path) or [0, 1])[1])
    return items[(page - 1) * 100:page * 100]


class GitHub:
    """An in-memory GitHub API for one linear history: deployments, their statuses, a commit's parents, and compare."""

    def __init__(self, history=HISTORY, deployments=(), statuses=None):
        self.history, self.deployments, self.statuses, self.calls = list(history), list(deployments), dict(statuses or {}), []

    def at(self, sha):
        return [c["sha"] for c in self.history].index(sha)

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path, body))
        if path.startswith("deployments?"):
            return page_of(path, sorted(self.deployments, key=lambda d: -d["id"]))
        if path == "deployments" and method == "POST":
            made = {"id": 900 + len(self.deployments), "sha": body["ref"]}
            self.deployments.append(made)
            return made
        if "/statuses?" in path:
            return page_of(path, self.statuses.get(int(path.split("/")[1]), []))
        if path.endswith("/statuses") and method == "POST":
            self.statuses.setdefault(int(path.split("/")[1]), []).append(body)
            return body
        if path.startswith("commits/"):
            return {"parents": [{"sha": self.history[self.at(path[8:]) - 1]["sha"]}]}
        if path.startswith("compare/"):
            base, head = path[8:].split("?")[0].split("...")
            span = self.history[self.at(base) + 1:self.at(head) + 1]
            return {"commits": page_of(path, span), "total_commits": len(span), "files": [f for c in span for f in c["files"]][:300]}      # GitHub: 300 files at most
        raise AssertionError(path)

    def writes(self):
        return [(m, p) for m, p, _ in self.calls if m == "POST"]


def released(history=HISTORY, sha="a" * 40):
    """A GitHub where commit `sha` is already recorded as a production release."""
    return GitHub(history, [{"id": 1, "sha": sha}], {1: [{"state": "success"}]})


def changed(*files, message="JFM-199 add feature (#251)"):
    return HISTORY[:2] + [commit(HEAD, message, *files)]


class Release(unittest.TestCase):
    def test_a_runtime_change_since_the_last_release_is_recorded_for_the_executed_commit_with_its_run(self):
        api = released(changed("lifeos/jobs/fit/__init__.py"))
        self.assertEqual(release.run(GOOD, api), "RECORDED")
        (_, _, deployment), (_, status_path, status) = [c for c in api.calls if c[0] == "POST"]
        self.assertEqual((deployment["ref"], deployment["environment"], deployment["production_environment"], deployment["transient_environment"]), (HEAD, "production", True, False))
        self.assertEqual((deployment["auto_merge"], deployment["required_contexts"], deployment["payload"]["run_url"]), (False, [], RUN_URL))
        self.assertEqual((status["state"], status["environment"], status["log_url"]), ("success", "production", RUN_URL))
        self.assertIn("not product acceptance", deployment["description"])

    def test_documentation_and_test_only_changes_are_never_a_release(self):
        for files in (("docs/MASTER_ROADMAP.md",), ("tests/jobs/test_x.py", "docs/a.md"), (".github/workflows/pr-tests.yml",), ("README.md",)):
            api = released(changed(*files))
            self.assertEqual(release.run(GOOD, api), "NO_RUNTIME_CHANGE", files)
            self.assertEqual(api.writes(), [], files)

    def test_each_kind_of_runtime_file_is_a_release(self):
        for name in ("lifeos/platform/gate.py", ".github/workflows/hourly.yml", ".github/actions/python/action.yml", "requirements.txt", "requirements-fit.txt"):
            self.assertEqual(release.run(GOOD, released(changed(name))), "RECORDED", name)

    def test_a_docs_commit_after_a_code_commit_still_records_the_code_once_and_only_once(self):
        history = [HISTORY[0], commit("b" * 40, "JFM-7 fix (#3)", "lifeos/y.py"), commit(HEAD, "docs: after", "docs/z.md")]
        api = released(history)
        self.assertEqual(release.run(GOOD, api), "RECORDED")
        self.assertEqual([release.run(GOOD, api) for _ in range(3)], ["ALREADY_RECORDED"] * 3)           # the next hourly ticks of the same commit
        self.assertEqual(len(api.deployments), 2)

    def test_after_a_release_a_docs_only_tick_on_a_newer_commit_records_nothing(self):
        api = released(HISTORY + [commit("d" * 40, "docs: later", "docs/q.md")])
        self.assertEqual(release.run({**GOOD, "GITHUB_SHA": "d" * 40}, api), "NO_RUNTIME_CHANGE")
        self.assertEqual(api.writes(), [])

    def test_with_no_release_recorded_yet_only_the_head_commit_is_judged(self):
        first = GitHub(changed("lifeos/a.py", message="JFM-77 D192: release tracking (#252)"))                    # the squash-merge title carries the release-tracking ticket
        self.assertEqual(release.run(GOOD, first), "RECORDED")
        self.assertEqual(first.calls[-2][2]["payload"]["jira"], ["JFM-77"])
        self.assertEqual(release.run(GOOD, GitHub(changed("docs/a.md"))), "NO_RUNTIME_CHANGE")

    def test_jira_keys_come_from_the_commits_in_the_deployed_range_not_from_a_custom_field(self):
        history = [HISTORY[0], commit("b" * 40, "JFM-12 first, and EDGE-1.3 and JOBS-1.1A (#4)", "lifeos/a.py"), commit(HEAD, "JFM-7 second; JFM-12 again (#5)", "lifeos/b.py")]
        api = released(history)
        self.assertEqual(release.run(GOOD, api), "RECORDED")
        self.assertEqual(api.calls[-2][2]["payload"]["jira"], ["JFM-12", "JFM-7"])                  # the earlier commit's key (not the head's only) is in the range; roadmap ids are not keys
        self.assertNotIn("JFM-100", str(api.calls[-2][2]))                                          # the previous release's own commit is outside the range

    def test_a_release_with_no_jira_key_is_recorded_but_flagged(self):
        api = released(changed("lifeos/a.py", message="D192: no ticket key (#9)"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(release.main(GOOD, api), 0)
        self.assertIn("release: RECORDED:NO_JIRA_KEY", out.getvalue())
        self.assertIn("::warning title=Release without a Jira key", out.getvalue())

    def test_observe_only_classifies_but_never_writes(self):
        for value in (None, "", "false", "yes"):
            env = {k: v for k, v in GOOD.items() if k != "RELEASE_PUBLISH"} | ({"RELEASE_PUBLISH": value} if value is not None else {})
            api = released(changed("lifeos/a.py"))
            self.assertEqual(release.run(env, api), "OBSERVED")
            self.assertEqual(api.writes(), [])
            self.assertEqual(release.run(env, released(changed("docs/a.md"))), "NO_RUNTIME_CHANGE")

    def test_a_deployment_left_without_a_success_status_is_completed_not_duplicated(self):
        api = GitHub(changed("lifeos/a.py"), [{"id": 1, "sha": "a" * 40}, {"id": 5, "sha": HEAD}], {1: [{"state": "success"}], 5: [{"state": "in_progress"}]})
        self.assertEqual(release.run(GOOD, api), "RECORDED")
        self.assertEqual(api.writes(), [("POST", "deployments/5/statuses")])

    def test_an_older_success_that_was_marked_inactive_still_counts_as_recorded(self):
        api = GitHub(changed("lifeos/a.py"), [{"id": 5, "sha": HEAD}], {5: [{"state": "inactive"}, {"state": "success"}]})
        self.assertEqual(release.run(GOOD, api), "ALREADY_RECORDED")
        self.assertEqual(api.writes(), [])

    def test_anything_short_of_a_verified_live_tick_of_main_is_unverified_and_touches_nothing(self):
        cases = {
            "pull request CI alone": ({"GITHUB_REF": "refs/pull/251/merge", "RELEASE_TICK": "false"}, "NOT_MAIN"),
            "a branch dispatch": ({"GITHUB_REF": "refs/heads/claude/x"}, "NOT_MAIN"),
            "a manual run": ({"RELEASE_TICK": "false"}, "NOT_TICK"),
            "a dry run": ({"RELEASE_LIVE": "false"}, "NOT_LIVE"),
            "a failed lane": ({"RELEASE_JOBS": GOOD["RELEASE_JOBS"].replace("dice=success", "dice=failure")}, "JOBS_NOT_SUCCESS"),
            "a skipped finish": ({"RELEASE_JOBS": GOOD["RELEASE_JOBS"].replace("finish=success", "finish=skipped")}, "JOBS_NOT_SUCCESS"),
            "a cancelled run": ({"RELEASE_JOBS": GOOD["RELEASE_JOBS"].replace("prep=success", "prep=cancelled")}, "JOBS_NOT_SUCCESS"),
            "no job results": ({"RELEASE_JOBS": ""}, "JOBS_NOT_SUCCESS"),
            "a failed stage": ({"RELEASE_FAILED": "publish"}, "STAGES_FAILED"),
        }
        for name, (change, code) in cases.items():
            api = released(changed("lifeos/a.py"))
            self.assertEqual(release.run({**GOOD, **change}, api), f"UNVERIFIED:{code}", name)
            self.assertEqual(api.calls, [], name)
        for key in ("GITHUB_REF", "RELEASE_TICK", "RELEASE_LIVE", "RELEASE_JOBS"):                 # missing evidence fails closed
            api = released(changed("lifeos/a.py"))
            self.assertTrue(release.run({k: v for k, v in GOOD.items() if k != key}, api).startswith("UNVERIFIED:"), key)
            self.assertEqual(api.calls, [], key)


class Degraded(unittest.TestCase):
    def failing(self, fail_on):
        class Refused(GitHub):
            def __call__(self, path, method="GET", body=None):
                if fail_on(path, method):
                    error = OSError("HTTP Error 403: Forbidden SECRET-BODY")
                    error.code = 403
                    raise error
                return super().__call__(path, method, body)
        return Refused(changed("lifeos/a.py"), [{"id": 1, "sha": "a" * 40}], {1: [{"state": "success"}]})

    def test_an_api_failure_is_a_visible_degraded_result_that_records_no_success_and_leaks_nothing(self):
        for name, fail_on in (("create", lambda p, m: p == "deployments" and m == "POST"), ("status", lambda p, m: p.endswith("/statuses") and m == "POST"),
                              ("read", lambda p, m: p.startswith("deployments?")), ("compare", lambda p, m: p.startswith("compare/"))):
            api, out = self.failing(fail_on), io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(release.main(GOOD, api), 1, name)                                 # exit 1 -> the step outcome is "failure", which the DEGRADED alert step keys on
            self.assertEqual(out.getvalue().strip(), "release: DEGRADED:OSError:403", name)       # a fixed code; no body, token or message text
            self.assertEqual([s for v in api.statuses.values() for s in v if s["state"] == "success" and "log_url" in s], [], name)

    def test_the_next_tick_retries_a_commit_whose_record_failed(self):
        api = self.failing(lambda p, m: p.endswith("/statuses") and m == "POST")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(release.main(GOOD, api), 1)
        again = GitHub(api.history, api.deployments, api.statuses)                                 # the deployment was created, the success status was not
        self.assertEqual(release.run(GOOD, again), "RECORDED")
        self.assertEqual(len(again.deployments), 2)

    def test_unverified_and_not_a_release_are_not_degraded(self):
        for env, api in (({**GOOD, "RELEASE_LIVE": "false"}, released(changed("lifeos/a.py"))), (GOOD, released(changed("docs/a.md")))):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(release.main(env, api), 0)


class Limits(unittest.TestCase):
    """GitHub's page and file limits must never turn into a short, wrong answer: more pages are read, or the result is DEGRADED."""

    def degraded(self, api):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(release.main(GOOD, api), 1)
        self.assertEqual(api.writes(), [])
        return out.getvalue().strip()

    def test_a_recorded_release_beyond_the_first_page_of_deployments_is_still_the_baseline(self):
        api = GitHub(changed("lifeos/a.py"), [{"id": 1, "sha": "a" * 40}] + [{"id": n, "sha": "f" * 40} for n in range(2, 131)], {1: [{"state": "success"}]})
        self.assertEqual(release.run(GOOD, api), "RECORDED")
        self.assertTrue(any("page=2" in p and p.startswith("deployments?") for _, p, _ in api.calls))

    def test_a_deployment_list_or_status_list_cut_off_by_the_page_limit_is_degraded_not_assumed_empty(self):
        self.assertEqual(self.degraded(GitHub(changed("lifeos/a.py"), [{"id": n, "sha": "f" * 40} for n in range(1, 601)])), "release: DEGRADED:Incomplete:DEPLOYMENTS")
        self.assertEqual(self.degraded(GitHub(changed("lifeos/a.py"), [{"id": 1, "sha": "a" * 40}], {1: [{"state": "in_progress"}] * 600})), "release: DEGRADED:Incomplete:STATUSES")

    def test_jira_keys_beyond_the_first_100_commits_of_the_range_are_read(self):
        history = [HISTORY[0]] + [commit(f"{n:040x}", "docs: x", "docs/x.md") for n in range(1, 120)] + [commit(HEAD, "JFM-5 late key (#8)", "lifeos/a.py")]
        api = released(history)
        self.assertEqual(release.run(GOOD, api), "RECORDED")
        self.assertEqual(api.calls[-2][2]["payload"]["jira"], ["JFM-5"])

    def test_a_range_longer_than_the_pages_read_is_degraded_not_judged_on_part_of_it(self):
        history = [HISTORY[0]] + [commit(f"{n:040x}", "docs: x", "docs/x.md") for n in range(1, 600)] + [commit(HEAD, "JFM-5 (#8)", "lifeos/a.py")]
        self.assertEqual(self.degraded(released(history)), "release: DEGRADED:Incomplete:COMPARE_COMMITS")

    def test_a_300_file_list_with_no_runtime_file_is_degraded_but_one_runtime_file_is_enough(self):
        docs = [f"docs/{n}.md" for n in range(300)]
        self.assertEqual(self.degraded(released(changed(*docs))), "release: DEGRADED:Incomplete:COMPARE_FILES")
        self.assertEqual(release.run(GOOD, released(changed("lifeos/a.py", *docs[:290]))), "RECORDED")
        self.assertEqual(release.run(GOOD, released(changed(*docs[:298]))), "NO_RUNTIME_CHANGE")           # under the cap the list is complete


if __name__ == "__main__":
    unittest.main()
