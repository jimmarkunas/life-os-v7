"""LIFE-749: the `followup` stage reconciles stored Followup threads with source deltas and publishes ONE component-state payload. Unwired in production: fixtures only."""
# python -m lifeos.run followup [--live]. `run(limit, live, wiring=None)`: with no wiring (production today) it does nothing and returns counts only. The hourly workflow step, the live store, the grants,
# the fetchers and the payload sink are NOT part of this change (Jim's call: workflow step, table creation, credentials). Nothing here reads a provider, creates a table or schedules anything.
# Reconcile, per (workspace, account): read each source delta since its accepted cursor (LIFE-748 readers) -> classify delta items into evidence (default: observation only, which never makes a loop) ->
# add Jim's explicit captures and the stored open threads as evidence -> `followup.project()` (the MVP rules) -> live only: `FollowupStore.accept` (one write path, cursor advances only after an accepted
# write) -> ONE payload {component, state PASS|DEGRADED, counts, threads, review (outside counts), sources, provenance}. A failed, denied or stale source keeps the last accepted state and never advances.
# A thread created here has identity_key == outcome (a label of at most 120 characters), so a stored row can be re-derived without a second identity column. The payload's thread id is the store's
# thread_id (64 hex), which is a safe Inbox id part; the label is the title. TGP and Asana are never read.
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from lifeos.inbox import followup
from lifeos.inbox.followup_store import FollowupRejected, FollowupStoreError, SOURCES, thread_id
from lifeos.inbox.ledger import digest
from lifeos.inbox.model import UNRELEASED_WORKSPACES, WORKSPACES, InboxError
from lifeos.platform import followup_readers as readers
from lifeos.platform import isolate, switches

COMPONENT = "followup"
TGP_DOMAINS = ("tgpinternational.com",)                    # TGP is excluded everywhere: an account label that names its domain is never a scope
STATE_OF = {"needs_action_today": "needs_action", "waiting_aging": "waiting", "upcoming": "upcoming", "review_needed": "review"}
KLASS_OF = {"waiting_on": "waiting", "promise": "owed", "deliverable": "owed", "jim_capture": "owed", "implied_promise": "owed", "value_add": "value_add", "outcome_thread": "outcome_thread"}
KIND_OF = {"waiting": "waiting_on", "owed": "deliverable", "value_add": "value_add", "outcome_thread": "outcome_thread"}       # a stored thread's class back to an explicit evidence kind
KLASS_ORDER = ("waiting_on", "promise", "deliverable", "jim_capture", "implied_promise", "value_add", "outcome_thread")
OBSERVED = "observed"                       # not an explicit kind: it can never create, close or reopen a loop by itself


def tgp_account(account):
    return any(marker in str(account).lower() for marker in TGP_DOMAINS)


def observe(item):
    """The default classifier: a delta item is an observation only. Turning items into explicit promises or closings is a classifier decision for the live-wiring step, not mined from text here."""
    return None


@dataclass(frozen=True)
class Scope:
    workspace: str
    account: str
    sources: tuple


@dataclass(frozen=True)
class Wiring:
    store: Any                                       # FollowupStore
    scopes: tuple                                    # Scope, one per (workspace, account)
    grants: tuple                                    # readers.Grant
    fetchers: dict                                   # {source: fetch(credential_ref, since)}
    audit: Callable                                  # audit(receipt); raises when it cannot write
    sink: Callable                                   # sink(payload): the one place the payload is published (live only)
    clock: Callable = lambda: datetime.now(timezone.utc)
    classify: Callable = observe                     # item -> evidence dict or None
    captures: Callable = lambda workspace, account: []      # Jim's explicit captures: evidence dicts
    jira_base: str = ""
    env: Any = None                                  # the environment the wiring was built from (the page writer reads its Notion token from it, never from the process)
    production: bool = False                         # True only for the wiring followup_wiring.build makes: the one that may write the Action Inbox page; a fixture wiring never does


def production_wiring(environ=None, live=False):
    """The live wiring (lifeos/inbox/followup_wiring.py) while the switch FOLLOWUP_WIRING is on and a source is configured; otherwise None and the stage is a counts-only no-op."""
    from lifeos.inbox import followup_wiring                                        # a function-level import: followup_wiring imports this module
    return followup_wiring.build(os.environ if environ is None else environ, live)


def _utc(text):
    parsed = followup._dt(text)                                                     # a naive time is the loop's own zone, America/Chicago
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if parsed else ""


def _stored_evidence(thread):
    """An open stored thread back as explicit evidence: same outcome key, same checkpoint (a Jim override wins), its source link, its last accepted time."""
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
    """The store row (see FollowupStore.normalize) for one derived thread, built from its evidence group; the override is NOT written back, it lives in its own table."""
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
            "confidence": confidence, "source_refs": links, "evidence_fp": digest(json.dumps([links, stamped, KLASS_OF[kind]], sort_keys=True))}


def _cursor(store, workspace, account, source):
    row = store.cursor_for(workspace, account, source)
    return datetime.fromisoformat(row["cursor"]) if row else None


_WORD = re.compile(r"[^A-Za-z0-9_-]+")


def source_lines(workspace, report):
    """What the log may show per source: `followup-source <workspace> <source> <status> <reason>`, every part reduced to letters, digits, underscores and hyphens (the reason is a fixed word already)."""
    clean = lambda value, width: _WORD.sub("_", str(value or "-"))[:width]
    return [f"followup-source {clean(workspace, 20)} {clean(r.get('source'), 20)} {clean(r.get('status'), 12)} {clean(r.get('reason'), 60)}" for r in report]


def reconcile(wiring, scope, live, now):
    """-> (payload, counts). `live` False computes everything and writes nothing (no store write, no sink)."""
    workspace, account = scope.workspace, scope.account
    counts = {"written": 0, "unchanged": 0, "suppressed": 0, "closed": 0, "sources_ok": 0, "sources_degraded": 0, "threads": 0, "review": 0}
    wanted = [s for s in scope.sources if s != "asana"]                              # Asana is never read, never degrades the stage
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
    evidence = [e for e in evidence if e.get("outcome_key") and len(e["outcome_key"]) <= 120]            # an over-long key cannot be a store identity: dropped, and the stage says so below
    projection = followup.project(evidence, now, workspace)
    degraded = degraded or projection.degraded
    groups = _group(i for i in evidence if i.get("workspace") == workspace)
    rows = [_row(key, groups[key], state) for key, state in sorted(projection.states.items()) if key in groups]
    counts["threads"], counts["review"] = len(projection.native), len(projection.review)
    failed = None
    if live:
        ok_sources = [r["source"] for r in report if r["status"] == "ok"]
        try:
            for key in sorted(k for k, g in groups.items() if {i["kind"] for i in g} & followup.OUTCOME_ENDED and k in {t["outcome"] for t in stored}):
                proof = digest(json.dumps(sorted({i["link"] for i in groups[key]}) + ["closed"]))               # strong proof the OUTCOME ended: closed, and the same evidence never reopens it
                counts["closed"] += wiring.store.resolve(workspace, account, key, "closed", proof, now)
            first = (ok_sources or [s for s in wanted if s in SOURCES])[:1]
            for source in first:
                cursor = (deltas[(source, workspace, account)].next_checkpoint or now).isoformat() if ok_sources else ""
                done = wiring.store.accept(workspace, account, source, rows, cursor, now, complete=bool(ok_sources))
                for k in ("written", "unchanged", "suppressed"):
                    counts[k] += done[k]
            for source in ok_sources[1:]:                                               # the other sources advance only their own cursor, after the threads were accepted
                delta = deltas[(source, workspace, account)]
                wiring.store.accept(workspace, account, source, [], (delta.next_checkpoint or now).isoformat(), now)
        except (FollowupRejected, FollowupStoreError) as error:
            failed = str(error)[:40]
            degraded = True
    payload = {"component": COMPONENT, "state": "DEGRADED" if degraded else "PASS", "workspace": workspace, "account": account, "generated_at": now.isoformat(),
               "last_accepted": max((r["last_accepted"] for r in report if r["last_accepted"]), default=None),
               "counts": projection.counts, "threads": [{**t, "id": thread_id(workspace, account, t["id"]), "title": t["id"], "state": projection.states[t["id"]]} for t in projection.native], "review": projection.review,
               "sources": report, "provenance": {"store": "v7_followup", "readers": "lifeos.platform.followup_readers", "derived": "lifeos.inbox.followup.project", "write_error": failed}}
    return payload, counts


def publish_page(payloads, now, environ=None, writer=None):
    """LIFE-711: after a live run, fill the Action Inbox page from the payloads just stored (switch ACTION_INBOX_VIEW). Never fails the stage: one fixed-word line either way; counts only."""
    env = os.environ if environ is None else environ
    if not switches.state("ACTION_INBOX_VIEW", env):
        return None
    from lifeos.inbox import page_writer                                           # noqa: PLC0415
    try:
        counts = (writer or page_writer.publish)(payloads, now, env)
    except InboxError as error:
        print(f"inbox-view-page failed {error.reason}")
        return None
    print("inbox-view-page " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return counts


def run(limit, live, wiring=None):
    """-> counts only. No wiring: a no-op. Dry run: compute, write nothing. Live: accept to the store and publish the payload, once per (workspace, account). One scope's failure never stops the other."""
    wiring = wiring or production_wiring(live=live)
    totals = {"unwired": 0, "scopes": 0, "refused": 0, "published": 0, "written": 0, "unchanged": 0, "suppressed": 0, "closed": 0, "sources_ok": 0, "sources_degraded": 0, "threads": 0, "review": 0}
    if wiring is None:
        totals["unwired"] = 1
        return totals
    now = wiring.clock()
    allowed = [s for s in wiring.scopes if s.workspace in WORKSPACES and s.workspace not in UNRELEASED_WORKSPACES and not tgp_account(s.account)]
    totals["refused"] = len(wiring.scopes) - len(allowed)                             # TGP and unknown workspaces: nothing is read, nothing is written

    published = []

    def one(scope):
        payload, counts = reconcile(wiring, scope, live, now)
        for line in source_lines(scope.workspace, payload.get("sources") or ()):
            print(line)                                                                # one fixed-word line per source: workspace, source, status, reason code. Never an account, a name, a subject or a count of items.
        if live:
            wiring.sink(payload)
            counts["published"] = 1
            published.append(payload)
        return counts

    isolated = isolate.run_items(allowed, one)
    if live and published and wiring.production:
        publish_page(published, now, wiring.env if wiring.env is not None else {})
    for _, counts in isolated.results:
        totals["scopes"] += 1
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value
    return isolate.record(totals, isolated)


__all__ = ["Scope", "Wiring", "production_wiring", "publish_page", "reconcile", "run", "observe", "tgp_account"]
