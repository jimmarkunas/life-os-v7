"""Retired no-op stage: the AI Capacity callout no longer exists. Its readings are rows of the System table (written
by lifeos/sources/cards/oracle_card.py, stage `oracle-card`). The stage stays only so a workflow or tick dispatch
that still names it succeeds; it reads and writes nothing.
"""


def run(limit, live, environ=None, **_):
    return {"status": "retired", "rows_written": 0}
