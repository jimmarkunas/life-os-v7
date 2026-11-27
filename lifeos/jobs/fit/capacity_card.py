"""Retired no-op stage: the AI Capacity callout is gone and this reads and writes nothing.

Its readings are rows of the System table (lifeos/sources/cards/oracle_card.py, stage `oracle-card`). The stage
stays only so a workflow or tick dispatch that still names it succeeds.
"""


def run(limit, live, environ=None, **_):
    return {"status": "retired", "rows_written": 0}
