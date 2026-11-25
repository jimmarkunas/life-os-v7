"""RETIRED (LIFE-975 step 4d, Jim, Nov 25 2026: "system ok"). The AI Capacity callout is gone from the Daily Report and nothing writes it any more.

Its readings (Codex, OpenRouter, the Cloud Fit guard, the sign-ins, the two Anthropic rows) are rows of the System table now, written by the one System writer (lifeos/sources/cards/oracle_card.py, stage `oracle-card`),
which reads Codex once. This stage is kept only so a workflow that still names it (capacity-card.yml, which stays disabled; the tick's dispatch of it) succeeds as a no-op instead of failing: it reads nothing, writes nothing
and never finds, installs or retires a block. Deleting the workflow file and the tick's dispatch step is a separate workflow change that needs Jim's approval."""


def run(limit, live, environ=None, **_):
    return {"status": "retired", "rows_written": 0}
