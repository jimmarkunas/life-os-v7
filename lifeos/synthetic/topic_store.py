"""The fake Notion topic update (LIFE-373): one version-checked update per request, cited, with lineage and relations. Fake data only."""
from lifeos.synthetic import knowledge, receipt as rcpt


def read_topic(world, tenant, topic_id):
    """-> (topic copy or None, code). A quarantined or foreign topic is refused with a fixed code and no content."""
    topic = world.topics.get(topic_id)
    if topic is None or topic["tenant"] != tenant:
        return None, "DENY_TENANT"
    if topic_id in world.quarantine:
        return None, "DENY_SOURCE_REVOKED"
    return dict(topic), "ALLOW"


def update(world, tenant, topic_id, wanted, base_version, writer, nonce, case_id="update"):
    """-> receipt. wanted = [(source_id, expected_revision)]. Writer conflict = replayed nonce, another writer holding the topic, or a version that moved (step 4). A topic that is missing or belongs to another tenant is refused first. A successful update lifts a quarantine."""
    before, r0, m0, c0 = world.state_hash(), world.read_calls, world.mutation_calls, world.commits
    topic = world.topics.get(topic_id)
    if topic is None or topic["tenant"] != tenant:      # first of all: a missing topic and another tenant's topic look the same, so a stranger learns neither that it exists nor its version
        return rcpt.build(case_id, tenant, knowledge.CODE["tenant"], world, before, r0, m0, c0)
    bad = knowledge.failures(world, tenant, wanted)
    if nonce in world.seen_nonces or topic["writer"] not in (None, writer) or topic["version"] != base_version:
        bad.add("replay_or_writer")
    name = knowledge.first_failure(bad)
    if name:
        return rcpt.build(case_id, tenant, knowledge.CODE[name], world, before, r0, m0, c0)
    world.seen_nonces.add(nonce)
    world.mutation_calls += 1
    topic["citations"] = [{"source_id": s, "revision": world.sources[s]["revision"], "kind": world.sources[s]["kind"]} for s, _ in wanted]
    topic["lineage"] = [(s, world.sources[s]["revision"]) for s, _ in wanted]
    topic["relations"] = [(topic_id, "cites", s) for s, _ in wanted]
    topic["version"] += 1
    if topic_id in world.quarantine:                    # rebuilt only from sources that passed every check (none is revoked), so the revoked source is out of the lineage
        world.quarantine.remove(topic_id)
    world.commits += 1
    return rcpt.build(case_id, tenant, "ALLOW", world, before, r0, m0, c0)
