"""Stage `accountability-review` (LIFE-671) is RETIRED by LIFE-840: the Quarterly Goals & Review callout of each person is written by `accountability-goals` alone. This stage writes nothing and keeps its
name so the workflow step and the stage registry stay valid; the retired review callouts and their summary pages are left exactly as they are until Jim authorises their retirement."""


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return {"retired": True, "writes": 0}
