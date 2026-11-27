"""Stage `accountability-review`: RETIRED.

`accountability-goals` writes the callout; this stage writes nothing and keeps its name so the workflow step and
registry stay valid."""


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return {"retired": True, "writes": 0}
