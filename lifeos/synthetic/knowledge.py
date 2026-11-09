"""Cited source reading with tenant isolation, revision checks and revoked-source refusal (LIFE-373). The earliest failing check wins:
writer/replay (4), wrong tenant (6), revoked (5), stale revision (15). Tenant comes before revoked on purpose (Jim, Nov 9 2026; LIFE-372 lists revoked first). A refusal has zero handler calls, zero commits and a receipt with no source text."""
from lifeos.synthetic import receipt as rcpt

CODE = {"replay_or_writer": "DENY_REPLAY_OR_WRITER", "revoked": "DENY_SOURCE_REVOKED", "tenant": "DENY_TENANT", "stale": "DENY_STALE_SOURCE"}


def failures(world, tenant, wanted):
    """-> the set of failing checks for [(source_id, expected_revision)]; an unknown id counts as a wrong tenant (no existence leak)."""
    bad = set()
    for sid, expected in wanted:
        src = world.sources.get(sid)
        if src is None or src["tenant"] != tenant:
            bad.add("tenant")                                  # first, so a stranger learns nothing about a foreign source (not even that it was revoked)
        elif sid in world.revoked:
            bad.add("revoked")
        elif src["revision"] != expected:
            bad.add("stale")
    return bad


def first_failure(bad):
    return next((name for name in rcpt.ORDER if name in bad), None)


def read(world, tenant, wanted, case_id="read"):
    """-> (records or None, receipt). A record cites its source id, tenant, kind and revision; the text is returned only on ALLOW."""
    before, r0, m0, c0 = world.state_hash(), world.read_calls, world.mutation_calls, world.commits
    name = first_failure(failures(world, tenant, wanted))
    if name:
        return None, rcpt.build(case_id, tenant, CODE[name], world, before, r0, m0, c0)
    out = []
    for sid, _ in wanted:
        world.read_calls += 1
        s = world.sources[sid]
        out.append({"source_id": sid, "tenant": s["tenant"], "kind": s["kind"], "revision": s["revision"], "text": s["text"]})
    return out, rcpt.build(case_id, tenant, "ALLOW", world, before, r0, m0, c0)
