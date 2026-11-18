"""Stage `accountability-review`: RETIRED by LIFE-840; the Quarterly Goals & Review callout is written by `accountability-goals` alone.

This stage writes nothing and keeps its name so the workflow step and the stage registry stay valid. The retired review callouts and their summary pages stay as they are until Jim authorises their retirement.
"""


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return {"retired": True, "writes": 0}
