"""The `followup` stage: reconciles stored Followup threads with source deltas and publishes ONE payload.

`run(limit, live, wiring=None)` with no wiring does nothing. Per (workspace, account): read each source delta since
its accepted cursor, classify items into evidence (default: observation only), add explicit captures and stored open
threads, `followup.project()`, then (live only) `FollowupStore.accept` and ONE payload. A failed, denied or stale
source keeps the last accepted state and its cursor does not advance. A created thread has identity_key == outcome (a
label of at most 120 characters), so a stored row can be re-derived without a second identity column. TGP and Asana
are never read.
"""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from lifeos.inbox import followup
from lifeos.inbox.followup_store import FollowupRejected, FollowupStoreError, SOURCES, thread_id
from lifeos.inbox.ledger import digest
from lifeos.inbox.model import UNRELEASED_WORKSPACES, WORKSPACES
from lifeos.platform import followup_readers as readers
from lifeos.platform import isolate, switches

COMPONENT = "followup"
TGP_DOMAINS = ("tgpinternational.com",)
STATE_OF = {"needs_action_today": "needs_action", "waiting_aging": "waiting", "upcoming": "upcoming", "review_needed": "review"}
KLASS_OF = {"waiting_on": "waiting", "promise": "owed", "deliverable": "owed", "jim_capture": "owed", "implied_promise": "owed", "value_add": "value_add", "outcome_thread": "outcome_thread"}
KIND_OF = {"waiting": "waiting_on", "owed": "deliverable", "value_add": "value_add", "outcome_thread": "outcome_thread"}
KLASS_ORDER = ("waiting_on", "promise", "deliverable", "jim_capture", "implied_promise", "value_add", "outcome_thread")
OBSERVED = "observed"


def tgp_account(account):
    return any(marker in str(account).lower() for marker in TGP_DOMAINS)


def observe(item):
    return None


@dataclass(frozen=True)
class Scope:
    workspace: str
    account: str
    sources: tuple


@dataclass(frozen=True)
class Wiring:
    store: Any
    scopes: tuple
    grants: tuple
    fetchers: dict
    audit: Callable
    sink: Callable
    clock: Callable = lambda: datetime.now(timezone.utc)
    classify: Callable = observe
    captures: Callable = lambda workspace, account: []
    jira_base: str = ""
    env: Any = None
    production: bool = False


def production_wiring(environ=None, live=False):
    from lifeos.inbox import followup_wiring  # function-level import: followup_wiring imports this module
    return followup_wiring.build(os.environ if environ is None else environ, live)


def _utc(text):
    parsed = followup._dt(text)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if parsed else ""


def _stored_evidence(thread):
    override = thread["checkpoint_basis_effective"] == "override"
    return {"kind": KIND_OF[thread["klass"]], "outcome_key": thread["outcome"], "workspace": thread["workspace"], "owner": thread["owner"], "who": "", "title": thread["outcome"],
            "link": thread["source_refs"][0], "checkpoint": thread["effective_checkpoint"] or None,
            "checkpoint_source": "source" if thread["checkpoint_basis"] == "source" else "suggested", "override": thread["effective_checkpoint"] if override else None,
            "observed_at": thread["last_accepted"]}


def _group(evidence):
    out = {}
    for item in evidence:
        out.setdefault(item.get("outcome_key"), []).append(item)
    return out


def _row(key, group, state):
    kinds = {i["kind"] for i in group}
    kind = next(k for k in KLASS_ORDER if k in kinds)
    lead = next((i for i in group if i["kind"] in followup.CREATES_LOOP), group[0])
    dated = next((i for i in group if i["kind"] in followup.CREATES_LOOP and i.get("checkpoint") and i.get("checkpoint_source") == "source"), None)
    stamped = (dated or lead).get("checkpoint")
    basis = "source" if dated else "suggested" if stamped else ""
    links = sorted({i["link"] for i in group})[:5]
    owner = lead.get("owner") if lead.get("owner") in ("jim", "external", "ambiguous") else "ambiguous"
    confidence = "high" if dated else "medium" if kind != "implied_promise" else "low"
    return {"identity_key": key, "outcome": key, "klass": KLASS_OF[kind], "owner": owner, "state": STATE_OF[state], "checkpoint_at": _utc(stamped) if stamped else "", "checkpoint_basis": basis,
            "confidence": confidence, "source_refs": links, "evidence_fp": digest(json.dumps([links, _utc(stamped) if stamped else "", KLASS_OF[kind]], sort_keys=True))}


def _cursor(store, workspace, account, source):
    row = store.cursor_for(workspace, account, source)
    return datetime.fromisoformat(row["cursor"]) if row else None


_WORD = re.compile(r"[^A-Za-z0-9_-]+")


def source_lines(workspace, report):
    clean = lambda value, width: _WORD.sub("_", str(value or "-"))[:width]
    return [f"followup-source {clean(workspace, 20)} {clean(r.get('source'), 20)} {clean(r.get('status'), 12)} {clean(r.get('reason'), 60)}" for r in report]


def reconcile(wiring, scope, live, now):
    workspace, account = scope.workspace, scope.account
    counts = {"written": 0, "unchanged": 0, "suppressed": 0, "closed": 0, "sources_ok": 0, "sources_degraded": 0, "threads": 0, "review": 0}
    wanted = [s for s in scope.sources if s != "asana"]
    since = {(s, workspace, account): _cursor(wiring.store, workspace, account, s) for s in wanted if s in SOURCES}
    deltas = readers.read_all([(s, workspace, account) for s in wanted], wiring.grants, wiring.fetchers, wiring.audit, now, since, wiring.jira_base)
    evidence, report, degraded = list(wiring.captures(workspace, account)), [], False
    for (source, _, _), delta in deltas.items():
        ok = delta.status == "ok"
        degraded = degraded or not ok
        counts["sources_ok" if ok else "sources_degraded"] += 1
        report.append({"source": source, "status": delta.status, "reason": delta.reason, "last_accepted": delta.last_accepted.isoformat() if delta.last_accepted else None})
        for item in delta.items if ok else ():
            found = wiring.classify(item)
            if found:
                evidence.append({**found, "workspace": workspace, "link": found.get("link") or item["link"], "observed_at": found.get("observed_at") or item["observed_at"]})
    stored = [t for t in wiring.store.threads(workspace, account) if t["resolution"] == "open"]
    evidence += [_stored_evidence(t) for t in stored]
    evidence = [e for e in evidence if e.get("outcome_key") and len(e["outcome_key"]) <= 120]
    projection = followup.project(evidence, now, workspace)
    degraded = degraded or projection.degraded
    groups = _group(i for i in evidence if i.get("workspace") == workspace)
    rows = [_row(key, groups[key], state) for key, state in sorted(projection.states.items()) if key in groups]
    counts["threads"], counts["review"] = len(projection.native), len(projection.review)
    failed, closed = None, []
    if live:
        ok_sources = [r["source"] for r in report if r["status"] == "ok"]
        try:
            for key in sorted(k for k, g in groups.items() if {i["kind"] for i in g} & followup.OUTCOME_ENDED and k in {t["outcome"] for t in stored}):
                # Strong proof the OUTCOME ended: closed, and the same evidence never reopens it
                proof = digest(json.dumps(sorted({i["link"] for i in groups[key]}) + ["closed"]))
                shut = wiring.store.resolve(workspace, account, key, "closed", proof, now)
                counts["closed"] += shut
                if shut:
                    closed.append(thread_id(workspace, account, key))
            first = (ok_sources or [s for s in wanted if s in SOURCES])[:1]
            for source in first:
                cursor = (deltas[(source, workspace, account)].next_checkpoint or now).isoformat() if ok_sources else ""
                done = wiring.store.accept(workspace, account, source, rows, cursor, now, complete=bool(ok_sources))
                for k in ("written", "unchanged", "suppressed"):
                    counts[k] += done[k]
            for source in ok_sources[1:]:
                delta = deltas[(source, workspace, account)]
                wiring.store.accept(workspace, account, source, [], (delta.next_checkpoint or now).isoformat(), now)
        except (FollowupRejected, FollowupStoreError) as error:
            failed = str(error)[:40]
            degraded = True
    payload = {"component": COMPONENT, "state": "DEGRADED" if degraded else "PASS", "workspace": workspace, "account": account, "generated_at": now.isoformat(),
               "last_accepted": max((r["last_accepted"] for r in report if r["last_accepted"]), default=None),
               "counts": projection.counts, "threads": [{**t, "id": thread_id(workspace, account, t["id"]), "title": t["id"], "state": projection.states[t["id"]]} for t in projection.native], "review": projection.review, "closed": closed,
               "sources": report, "provenance": {"store": "v7_followup", "readers": "lifeos.platform.followup_readers", "derived": "lifeos.inbox.followup.project", "write_error": failed}}
    return payload, counts


def deliver_loops(payloads, now, environ=None, deliver=None):
    env = os.environ if environ is None else environ
    if not switches.state("ACTION_INBOX_VIEW", env):
        return None
    if deliver is None:
        from lifeos.inbox import followup_deliver                                  # noqa: PLC0415
        deliver = followup_deliver.deliver
    counts = deliver(payloads, now=now, environ=env)
    print("followup-inbox " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return counts


def refresh_tiles(environ=None, refresh=None):
    env = os.environ if environ is None else environ
    if not switches.state("ACTION_INBOX_VIEW", env):
        return None
    if refresh is None:
        from lifeos.inbox import agent_inbox_stage                                  # noqa: PLC0415
        refresh = agent_inbox_stage.refresh
    return refresh(env)


def run(limit, live, wiring=None):
    """Returns counts only. No wiring: a no-op. Dry run: write nothing. One scope's failure never stops the other."""
    wiring = wiring or production_wiring(live=live)
    totals = {"unwired": 0, "scopes": 0, "refused": 0, "published": 0, "written": 0, "unchanged": 0, "suppressed": 0, "closed": 0, "sources_ok": 0, "sources_degraded": 0, "threads": 0, "review": 0}
    if wiring is None:
        totals["unwired"] = 1
        return totals
    now = wiring.clock()
    allowed = [s for s in wiring.scopes if s.workspace in WORKSPACES and s.workspace not in UNRELEASED_WORKSPACES and not tgp_account(s.account)]
    totals["refused"] = len(wiring.scopes) - len(allowed)

    published = []

    def one(scope):
        payload, counts = reconcile(wiring, scope, live, now)
        for line in source_lines(scope.workspace, payload.get("sources") or ()):
            print(line)
        if live:
            wiring.sink(payload)
            counts["published"] = 1
            published.append(payload)
        return counts

    isolated = isolate.run_items(allowed, one)
    if live and published and wiring.production:
        env = wiring.env if wiring.env is not None else {}
        deliver_loops(published, now, env)
        refresh_tiles(env)
    for _, counts in isolated.results:
        totals["scopes"] += 1
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value
    return isolate.record(totals, isolated)


__all__ = ["Scope", "Wiring", "production_wiring", "deliver_loops", "refresh_tiles", "reconcile", "run", "observe", "tgp_account"]
