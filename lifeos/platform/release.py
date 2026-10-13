"""Report a verified production tick to GitHub Deployments, so Jira (GitHub for Atlassian) can show it under the delivery ticket. Stdlib only.

Production here is `main`, run by the live hourly tick. A deployment is recorded for a commit only when THIS run is a live tick of `main` whose every job and
stage succeeded (the Unit tests and each stage's read-back are part of those jobs); anything else returns a fixed UNVERIFIED code and writes nothing.
One success per commit: a repeat tick of the same commit, or a retry, finds the record and writes nothing. Observe-only unless repo variable
V7_DEPLOY_REPORTING is "true". This is the machine release signal; Jim's product acceptance (Done) is separate and never set here."""
import os
import re

from lifeos.platform.gate import _api

ENVIRONMENT = "production"
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
    sha = env["GITHUB_SHA"]
    made = api(f"deployments?sha={sha}&environment={ENVIRONMENT}&per_page=100")
    if any(s["state"] == "success" for d in made for s in api(f"deployments/{d['id']}/statuses?per_page=100")):
        return "ALREADY_RECORDED"
    if env.get("RELEASE_PUBLISH") != "true":
        return "OBSERVED"
    url = f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}"
    keys = sorted(set(JIRA_KEY.findall(api(f"commits/{sha}")["commit"]["message"])))
    deployment = made[0] if made else api("deployments", "POST", {
        "ref": sha, "environment": ENVIRONMENT, "production_environment": True, "transient_environment": False, "auto_merge": False, "required_contexts": [],
        "description": "LIFE OS V7 live tick verified on main", "payload": {"run_url": url, "jira": keys}})
    api(f"deployments/{deployment['id']}/statuses", "POST", {"state": "success", "environment": ENVIRONMENT, "log_url": url, "description": "Live tick succeeded; all stages read back"})
    return "RECORDED"


if __name__ == "__main__":
    print("release:", run())
