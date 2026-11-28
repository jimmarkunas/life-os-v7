"""The plain name of a workflow job as GitHub's jobs API reports it.

A job that calls a reusable workflow is reported as `<caller job> / <called job>` ("finish / finish"), and the domain jobs sit one level deeper under the `run` job of domains.yml ("run / jira / jira", "run / gate (tick 5) / gate", "run / report"). Every reader that matches a job by name goes through `label`, which gives the old plain name for all three shapes and for a plain name itself."""

TOP = "run"  # the one job of domains.yml; its called workflow holds the domain jobs


def label(name):
    """-> the job's own name: `jira` for "run / jira / jira", "jira / jira" and "jira"; `gate (tick 5)` for "run / gate (tick 5) / gate"."""
    parts = [part.strip() for part in str(name or "").split(" / ")]
    if len(parts) > 1 and parts[0] == TOP:
        return parts[1]
    return parts[0]
