"""The fixed, redacted receipt (field names from LIFE-372). It never holds source text, a foreign source id or a foreign tenant."""

ORDER = ("replay_or_writer", "tenant", "revoked", "stale")          # LIFE-372 steps 4, 6, 5, 15. Jim chose (Nov 9 2026) to check the tenant BEFORE revoked, so a caller from another tenant is never told that a foreign source exists or was revoked
STEP = {"replay_or_writer": 4, "tenant": 6, "revoked": 5, "stale": 15}


def build(case_id, tenant, code, world, before, reads_before, mutations_before, commits_before):
    allowed = code == "ALLOW"
    return {"fixture_case_id": case_id, "tenant_id": tenant, "decision_code": code, "reason": code,
            "decision_step": 21 if allowed else None, "decision_stage": "allow" if allowed else "preflight",
            "read_handler_calls": world.read_calls - reads_before, "mutation_calls": world.mutation_calls - mutations_before,
            "commits": world.commits - commits_before, "provider_state_hash_before": before, "provider_state_hash_after": world.state_hash(),
            "source_failure_kind": "SOURCE_STALE" if code == "DENY_STALE_SOURCE" else None, "outcome_label": "allow" if allowed else "deny"}
