"""The V7 in-process policy gate for synthetic tools (LIFE-374, order and cases from LIFE-372). One pure function, `preflight`, answers a tool request with exactly one
final code by running the 21 steps in order; `dispatch` is the only way a registered fake tool runs. NOT a gateway: no server, no scheduler, no network.

Order: 1 input, 2 principal, 3 task, 4 replay or writer, 5 revoked source, 6 tenant, 7 account, 8 untrusted authority, 9 unsupported, 10 management, 11 unknown risk,
12 batch, 13 owner re-consent, 14 grant, 15 stale source, 16 protected approval, 17 approval match, 18 secret in the request, 19 timeout after a read, 20 secret in the
output, 21 allow. A wait for Jim (delete without approval, batch over the limit) is HELD and returned as PENDING only if no deny comes up in steps 13 to 18.
The tenant check (step 6) runs before the revoked check (step 5), as decided for LIFE-373 (Jim, Nov 9 2026), so a caller from another tenant is never told a foreign source was revoked. The step numbers in the receipt stay LIFE-372's.

The grant and kind decision reuses lifeos/platform/access_policy.decide() for the V7 services (jira, notion, github). The synthetic services (gmail, drive, calendar,
...) are not in that module, so `mirror_decide` applies the same rules to the grant level; a test pins the two together. access_policy.py itself is not edited."""
from lifeos.platform import access_policy
from lifeos.synthetic import tools as T
from lifeos.synthetic.authz_fixture import BULK_LIMIT, CANARY, NOW, V7_SERVICES, approval_hash, ref_hash

REQUIRED = ("case", "tenant", "agent_id", "host_id", "session_id", "task_id", "tool", "nonce", "channel")
AMBIGUOUS_ACCOUNTS = (None, "", "latest", "default", "any")


def _r(code, step, reason=None, kind=None):
    return {"code": code, "step": step, "reason": reason or code, "kind": kind}


def mirror_decide(level, kind, count, limit=BULK_LIMIT):
    """access_policy.decide() for a service that module does not know: the same rules on the grant level (off, read, write)."""
    if level == "off":
        return "deny", "service_off"
    if kind == "read":
        return "allow", "read_allowed"
    if level != "write":
        return "deny", "write_not_granted"
    if kind == "delete":
        return "pending", "delete_needs_approval"
    if kind == "bulk" or count > limit:
        return "pending", "bulk_needs_approval"
    return "allow", "write_allowed"


def grant_decision(world, agent, service, level, kind, count):
    if service in V7_SERVICES:
        return access_policy.decide(world.policy, agent["key"], service, kind, count)
    return mirror_decide(level, kind, count)


def preflight(world, req, now=NOW):
    """-> {code, step, reason, kind}. code is ALLOW (cleared for the handler), PENDING (held for Jim), NEEDS_OWNER_RECONSENT or a DENY_* code. Never touches the world."""
    if not isinstance(req, dict) or any(not (isinstance(req.get(k), str) and req[k]) for k in REQUIRED):
        return _r("DENY_INPUT", 1)
    names = req.get("batch") or [req["tool"]]
    if not (isinstance(names, list) and names and all(isinstance(n, str) for n in names)):
        return _r("DENY_INPUT", 1)
    agent, sess = world.agents.get(req["agent_id"]), world.sessions.get(req["session_id"])
    if (req["channel"] != "v7_adapter" or agent is None or req["host_id"] not in world.hosts or sess is None            # the default Hermes connector path is not the adapter
            or sess["agent"] != req["agent_id"] or sess["host"] != req["host_id"] or sess["expires"] <= now):
        return _r("DENY_PRINCIPAL", 2)
    task = world.tasks.get(req["task_id"])
    if task is None or task["agent"] != req["agent_id"] or task["blocked"]:
        return _r("DENY_TASK_SCOPE", 3)
    approval, resource, claim = req.get("approval"), req.get("resource"), req.get("claim_id")
    if (req["nonce"] in world.req_nonces or (isinstance(approval, dict) and approval.get("nonce") in world.approval_nonces)
            or (resource and world.writer_claims.get(resource, claim) != claim)):
        return _r("DENY_REPLAY_OR_WRITER", 4)
    sid, acct_id = req.get("source_id"), req.get("account_id")
    acct, src = world.accounts.get(acct_id), (world.sources.get(sid) if sid else None)
    if req["tenant"] != agent["tenant"] or (acct and acct[0] != req["tenant"]) or (sid and (src is None or src["tenant"] != req["tenant"])):
        return _r("DENY_TENANT", 6)                                  # evaluated before the revoked check on purpose (Jim, Nov 9 2026): a stranger never learns a foreign source was revoked
    if sid and sid in world.revoked:
        return _r("DENY_SOURCE_REVOKED", 5)
    regs = [T.TOOLS.get(n) for n in names]
    if acct_id in AMBIGUOUS_ACCOUNTS or acct is None or any(t and acct[1] != t["service"] for t in regs):
        return _r("DENY_ACCOUNT_AMBIGUOUS", 7)
    if req.get("authority_claim"):
        return _r("DENY_UNTRUSTED", 8)
    if any(t is None or t["unsupported"] for t in regs):
        return _r("DENY_UNSUPPORTED_ACTION", 9)
    if any(a in T.MANAGEMENT for t in regs for a in t["actions"]) and req["agent_id"] not in world.management_agents:         # a trusted world fact, never a request field
        return _r("DENY_MANAGEMENT_SCOPE", 10)
    if any(len(t["actions"]) != 1 or t["actions"][0] not in T.KNOWN_RISKS for t in regs):
        return _r("DENY_PROTECTED", 11, "unknown_or_multi_risk")
    actions = {t["actions"][0] for t in regs}
    if len(actions) > 1:
        return _r("DENY_BATCH_POLICY", 12, "mixed_batch")
    action, hold = next(iter(actions)), None
    if len(names) > BULK_LIMIT:
        hold = ("bulk_needs_approval", 12)
    if acct_id in world.consent_needed:
        return _r("NEEDS_OWNER_RECONSENT", 13)
    grant = world.grants.get(req.get("grant_id"))
    if (grant is None or grant["revoked"] or grant["agent"] != req["agent_id"] or grant["account"] != acct_id or grant["tenant"] != req["tenant"]
            or grant["service"] != acct[1] or grant["expires"] <= now or grant["review_due"] <= now):
        return _r("DENY_GRANT", 14, "grant_missing_revoked_expired_or_overdue")
    decision, reason = grant_decision(world, agent, acct[1], grant["level"], action if action in T.ROUTINE else "read", len(names))
    if decision == "deny":
        return _r("DENY_GRANT", 14, reason)
    if decision == "pending" and hold is None:
        hold = (reason, 14)
    if sid and req.get("source_revision") != src["revision"]:
        return _r("DENY_STALE_SOURCE", 15, kind="SOURCE_STALE")
    if action in T.PROTECTED + T.MANAGEMENT and (grant["level"] != "write" or not approval):
        return _r("DENY_PROTECTED", 16, "protected_needs_write_grant_and_exact_approval")
    if action == T.DELETE and not approval and hold is None:
        hold = ("delete_needs_approval", 16)
    if approval is not None:
        if (not isinstance(approval, dict) or approval.get("issuer") != "jim" or approval.get("host") != req["host_id"] or approval.get("task") != req["task_id"]
                or approval.get("account") != acct_id or approval.get("action_hash") != approval_hash(names, acct_id, req["tenant"], req["host_id"], req["task_id"])):
            return _r("DENY_APPROVAL_MISMATCH", 17)
        hold = None                                                  # Jim's exact, attributable answer ends the wait
    if CANARY in str(req.get("input_text", "")):
        return _r("DENY_SECRET_EXPOSURE", 18)
    if hold:
        return _r("PENDING", hold[1], hold[0])
    return _r("ALLOW", 21)


def _execute(world, req):
    """Run the registered handlers once. Reads first (steps 19 and 20 can still refuse); mutations are staged and committed only after every read passed."""
    names = req.get("batch") or [req["tool"]]
    regs = [T.TOOLS[n] for n in names]
    results = []
    if regs[0]["actions"][0] == T.READ:
        for t in regs:
            try:
                out = t["handler"](world, req)
            except TimeoutError:
                return _r("DENY_STALE_SOURCE", 19, kind="SOURCE_TIMEOUT"), None, "post_read"
            if T.secret_shaped(out):
                return _r("DENY_SECRET_EXPOSURE", 20), None, "post_read"       # the output is suppressed, never returned
            results.append(out)
    else:
        for n, t in zip(names, regs):
            results.append(t["handler"](world, req))
        for n in names:
            world.outbox.append((n, ref_hash(req["account_id"])))
            world.commits += 1
    world.req_nonces.add(req["nonce"])
    if isinstance(req.get("approval"), dict):
        world.approval_nonces.add(req["approval"].get("nonce"))
    return _r("ALLOW", 21), results, "allow"


def dispatch(world, req, now=NOW):
    """-> (receipt, results). The only way a fake tool runs: preflight first, handlers only on ALLOW, one redacted receipt always."""
    before = (world.provider_hash(), world.ledger_hash(), world.read_calls, world.mutation_calls, world.commits)
    d, results, stage = preflight(world, req, now), None, "preflight"
    if d["code"] == "ALLOW":
        d, results, stage = _execute(world, req)
    if d["code"] == "PENDING":
        stage = "held"
    return build_receipt(world, req, now, d, stage, before), results


def build_receipt(world, req, now, d, stage, before):
    """The fixed receipt (LIFE-372 field names). It repeats only the caller's own ids; no source text, no approval body, no raw nonce, no foreign tenant."""
    req = req if isinstance(req, dict) else {}
    names = req.get("batch") or [req.get("tool")]
    regs = [T.TOOLS.get(n) for n in names if isinstance(n, str)]
    acct = world.accounts.get(req.get("account_id"))
    code = d["code"]
    outcome = "allow" if code == "ALLOW" else "pending" if code == "PENDING" else "deny"
    ok = lambda v: v if isinstance(v, str) else None                                         # noqa: E731
    appr = req.get("approval") if isinstance(req.get("approval"), dict) else {}
    return {"fixture_case_id": ok(req.get("case")), "policy_revision": "access_policy.v1+synthetic-t05", "fake_actor_id": ok(req.get("agent_id")),
            "fake_host_id": ok(req.get("host_id")), "fake_session_id": ok(req.get("session_id")), "fake_task_id": ok(req.get("task_id")), "tenant_id": ok(req.get("tenant")),
            "provider": acct[1] if acct else None, "exact_account_id": ok(req.get("account_id")),
            "action_class": "+".join(sorted({t["actions"][0] for t in regs if t})) or None,
            "grant_or_approval_reference_hash": ref_hash(req.get("grant_id"), appr.get("action_hash")), "request_nonce_hash": ref_hash(req.get("nonce")),
            "decision_code": code, "reason": d["reason"], "decision_step": d["step"], "decision_stage": stage, "source_failure_kind": d["kind"],
            "checked_at_utc": now.isoformat(), "read_handler_calls": world.read_calls - before[2], "mutation_calls": world.mutation_calls - before[3], "commits": world.commits - before[4],
            "provider_state_hash_before": before[0], "provider_state_hash_after": world.provider_hash(),
            "policy_ledger_hash_before": before[1], "policy_ledger_hash_after": world.ledger_hash(), "outcome_label": outcome,
            "audit": access_policy.audit_line(ok(req.get("agent_id")) or "unknown", acct[1] if acct else "unknown", ok(req.get("tool")) or "unknown", outcome, d["reason"],
                                               "jim" if appr.get("issuer") == "jim" and code == "ALLOW" else None, now)}
