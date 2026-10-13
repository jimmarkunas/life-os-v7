"""Record a production software release with GitHub Deployments, so Jira (GitHub for Atlassian) shows it under the delivery ticket. Stdlib only.

Three states are kept apart. EXECUTED: any tick runs whatever `main` is. DEPLOYED (recorded here): a verified tick runs a `main` whose runtime files (lifeos/, .github/
except pr-tests.yml, requirements*) changed since the last recorded deployment; docs-only and tests-only commits are never a release. ACCEPTED: Jim's alone, never set here.
Evidence is exactly the hourly run's own results: prep (it runs the unit tests), the four resolver lanes and finish, plus finish's failed-stage list, on a live tick of
`main`. It is NOT the Interview job, the domain jobs (domains.yml runs separately afterwards) or any step that only warns, so a recorded deployment means "this runtime
change ran a fully successful live Jobs tick", not "every feature works". Jira reads issue keys from the commits in the deployed range, so the delivery ticket's JFM key
must be in a commit message or PR title; a release with none is still recorded and loudly flagged. One success per commit. Observe-only unless V7_DEPLOY_REPORTING is "true"."""
import os
import re
import sys

from lifeos.platform.gate import _api

ENVIRONMENT = "production"
RUNTIME = re.compile(r"^(lifeos/|\.github/(?!workflows/pr-tests\.yml$)|requirements)")
JIRA_KEY = re.compile(r"\bJFM-\d+\b")           # the delivery project; roadmap ids such as JOBS-1.1A or EDGE-1.3 are not Jira keys


def verify(env):
    """None when this run is verified production evidence, else a fixed reason code."""
    jobs = dict(pair.split("=", 1) for pair in env.get("RELEASE_JOBS", "").split(",") if "=" in pair)
    for bad, reason in ((env.get("GITHUB_REF") != "refs/heads/main", "NOT_MAIN"), (env.get("RELEASE_TICK") != "true", "NOT_TICK"),
                        (env.get("RELEASE_LIVE") != "true", "NOT_LIVE"), (not jobs or set(jobs.values()) != {"success"}, "JOBS_NOT_SUCCESS"),
                        (env.get("RELEASE_FAILED", "") != "", "STAGES_FAILED")):
        if bad:
            return reason


def run(env=os.environ, api=_api):
    reason = verify(env)
    if reason:
        return f"UNVERIFIED:{reason}"
    head, base, pending = env["GITHUB_SHA"], None, None
    for d in api(f"deployments?environment={ENVIRONMENT}&per_page=30"):                        # newest first
        if any(s["state"] == "success" for s in api(f"deployments/{d['id']}/statuses?per_page=100")):
            if d["sha"] == head:
                return "ALREADY_RECORDED"
            base = d["sha"]                                                                    # the last recorded release
            break
        pending = pending or (d if d["sha"] == head else None)                                # left without a success status: complete it
    base = base or api(f"commits/{head}")["parents"][0]["sha"]                                # no release recorded yet: judge the head commit alone
    diff = api(f"compare/{base}...{head}")
    if not any(RUNTIME.match(f["filename"]) for f in diff["files"]):
        return "NO_RUNTIME_CHANGE"
    if env.get("RELEASE_PUBLISH") != "true":
        return "OBSERVED"
    url = f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
    keys = sorted(set(JIRA_KEY.findall("\n".join(c["commit"]["message"] for c in diff["commits"]))))
    deployment = pending or api("deployments", "POST", {
        "ref": head, "environment": ENVIRONMENT, "production_environment": True, "transient_environment": False, "auto_merge": False, "required_contexts": [],
        "description": "Runtime change executed by a fully successful live tick; not product acceptance", "payload": {"run_url": url, "jira": keys}})
    api(f"deployments/{deployment['id']}/statuses", "POST", {"state": "success", "environment": ENVIRONMENT, "log_url": url, "description": "Live tick succeeded on this runtime change"})
    return "RECORDED" if keys else "RECORDED:NO_JIRA_KEY"


def main(env=os.environ, api=_api):
    try:
        result = run(env, api)
    except Exception as error:                  # the reporting itself failed: DEGRADED, never silent; the workflow step cannot fail the tick
        print(f"release: DEGRADED:{type(error).__name__}:{getattr(error, 'code', '')}")
        return 1
    print("release:", result)
    if result.endswith("NO_JIRA_KEY"):
        print("::warning title=Release without a Jira key::the deployed commits carry no JFM key, so Jira cannot attribute this release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
