"""Counts-only Interview probe and bounded evidence execution."""

import os
from lifeos.platform import stage_contract
from lifeos.platform.runtime import RunContext, DeadlineExceeded
from lifeos.platform.notion_client import NotionError
from . import notion
from .identity import resolve_parent
from .models import Ownership, Parent, ParentQuery, State
from lifeos.platform.names import split_title


QUIET = (
    "parent_created",
    "round_created",
    "parent_exists",
    "parent_adopted",
    "adopt_allowed",
    "parent_upgraded",
    "upgrade_allowed",
    "parent_refreshed",
    "refresh_allowed",
    "transcript_linked",
    "transcript_link_allowed",
    "round_confirmed",
    "confirm_allowed",
    "round_rescheduled",
    "reschedule_allowed",
    "round_cancelled",
    "cancel_allowed",
    "round_details",
    "details_allowed",
    "round_unchanged",
    "refresh_not_machine",
    "refresh_has_rounds",
)  # evidence outcomes that are not a block
DERIVED_OK = ("derived_allowed", "derived_created", "derived_match")


def _reason(counts, code):
    counts["why"][code] = counts["why"].get(code, 0) + 1


def _block(counts, code):
    counts["blocked"] += 1
    _reason(counts, code)
    counts["blocked_why"][code] = counts["blocked_why"].get(code, 0) + 1


def _tally(counts, cause):
    causes = counts.setdefault("causes", {})
    causes[cause] = causes.get(cause, 0) + 1


def evidence_outcome(result):
    """Pure. -> (the count this result adds one to, or None; whether to stop the loop)."""
    code = result["code"]
    bump = (
        "matched"
        if code == "child_match"
        else "not_found"
        if code in ("create_allowed", "parent_create_allowed")
        else None
        if code in QUIET
        else "blocked"
    )
    return bump, bool(result["readback_failed"] or code == "write_failed")


def prep_outcome(result):
    """Pure. -> (blocked, whether to stop the loop)."""
    code = result["code"]
    return code not in DERIVED_OK, code in ("derived_readback_failed", "readback_protected_changed") or (
        result["writes"] > 0 and code != "derived_created"
    )


ROSTER_STAGES = ("Interviewing", "Offer", "Working", "Ended")


def _closed_rounds(client, parent, context, rounds):
    """Collect the machine rounds of an inactive job for the Interviews list; nothing is counted or written."""
    children = notion.child_scan(client, parent.page_id, context)
    if not children.complete:
        return False
    for child in children.items:
        if child.identity_valid is True and notion.ownership(client, child.page_id, "round") == Ownership.MACHINE:
            rounds.append((parent, child))
    return True


def _root_rounds(client, environ, context, rounds):
    """Collect rounds of machine opportunity pages the Job Ledger scan does not reach. -> False when a read was
    incomplete."""
    root = (environ.get("HIRING_PIPELINE_PAGE_ID") or "").strip()
    scan = notion.parent_scan(client, root, context)
    if not scan.complete:
        return False
    try:
        direct = [
            Parent(b["id"], (b.get("child_page") or {}).get("title", ""))
            for b in notion.children(client, root, [notion.MAX_CALLS], context)
            if b.get("type") == "child_page"
        ]
    except NotionError:
        return False
    seen = set()
    for parent in [*scan.items, *direct]:
        if parent.page_id in seen:
            continue
        seen.add(parent.page_id)
        context.require_time()
        if notion.ownership(client, parent.page_id, "opportunity") == Ownership.MACHINE and not _closed_rounds(
            client, parent, context, rounds
        ):
            return False
    return True


def _survey(client, environ, context, counts, rounds=None):
    """READ every active parent and its rounds and who owns each page. -> False when the pipeline was not read
    completely. `rounds`, when given, collects every machine round met as (parent, child).
    """
    scan = notion.parents(client, environ, context)
    counts["observed"] = len(scan.items)
    counts["parents_ledger"], counts["parents_tree_only"] = scan.ledger, scan.tree_only
    if not scan.complete:
        _block(counts, "pipeline_incomplete")
        return False
    for parent in scan.items:
        if not parent.active:
            if (
                rounds is not None
                and parent.stage in ROSTER_STAGES
                and not _closed_rounds(client, parent, context, rounds)
            ):
                rounds.append(None)
            continue
        context.require_time()
        company, role = split_title(parent.title)
        if not company.strip() or not role.strip():
            _block(counts, "identity_invalid")
            continue
        resolved = resolve_parent(ParentQuery(company, role), scan)
        if resolved.state == State.BLOCKED:
            _block(counts, resolved.code)
            continue
        counts["valid_parents"] += 1
        owner = notion.ownership(client, parent.page_id, "opportunity")
        counts["machine_pages" if owner == Ownership.MACHINE else "human_pages"] += int(owner != Ownership.UNKNOWN)
        if owner == Ownership.UNKNOWN:
            _block(counts, "ownership_unknown")
        children = notion.child_scan(client, parent.page_id, context)
        if not children.complete:
            _block(counts, "child_scan_incomplete")
            continue
        counts["with_rounds"] += int(bool(children.items))
        for child in children.items:
            owner = notion.ownership(client, child.page_id, "round")
            if owner == Ownership.UNKNOWN:
                _block(counts, "ownership_unknown")
            else:
                counts["machine_pages" if owner == Ownership.MACHINE else "human_pages"] += 1
                if rounds is not None and owner == Ownership.MACHINE and child.identity_valid is True:
                    rounds.append((parent, child))
    return True


def _apply_evidence(client, environ, evidence, limit, live, context, probe, counts):
    from .create import apply

    deferred = set()
    for index, (parent_query, round_query) in enumerate(evidence):
        if index >= max(0, limit):
            if any(not q.refresh_only for _, q in evidence[index:]):
                _block(counts, "pipeline_incomplete")
            else:  # refresh-only items come last; those past the limit are counted, not an alarm
                counts["why"]["refresh_deferred"] = len(evidence) - index
            break
        if probe:
            break
        result = apply(client, environ, parent_query, round_query, live, context, deferred)
        for key, value in result.items():
            if key not in ("code", "cause"):
                counts[key] = counts.get(key, 0) + value
        if result.get("cause"):
            _tally(counts, result["cause"])
        _reason(counts, result["code"])
        bump, stop = evidence_outcome(result)
        if bump:
            counts[bump] += 1
        if bump == "blocked":
            counts["blocked_why"][result["code"]] = counts["blocked_why"].get(result["code"], 0) + 1
        if stop:
            break


def _apply_prep(client, environ, prep_evidence, limit, live, context, counts):
    from .prep import apply as apply_prep

    for index, (parent_query, round_query, prep) in enumerate(prep_evidence):
        if index >= max(0, limit):
            _block(counts, "pipeline_incomplete")
            break
        result = apply_prep(client, environ, parent_query, round_query, prep, live, context)
        counts["writes_planned"] += result["writes_planned"]
        counts["writes"] += result["writes"]
        counts["derived_created"] += int(result["code"] == "derived_created")
        counts["derived_match"] += int(result["code"] == "derived_match")
        _reason(counts, result["code"])
        blocked, stop = prep_outcome(result)
        counts["blocked"] += int(blocked)
        if blocked:
            counts["blocked_why"][result["code"]] = counts["blocked_why"].get(result["code"], 0) + 1
        if stop:
            break


def _roster(client, environ, rounds, live, context, counts):
    """Keep the Interviews list current, only after a complete survey; an absent or unreadable list is a reason code,
    never a block.
    """
    from . import roster

    if not _root_rounds(client, environ, context, rounds):
        rounds.append(None)
    unique, seen = [], set()
    for item in rounds:
        if item is None or item[1].page_id not in seen:
            unique.append(item)
            seen.add(item[1].page_id if item else None)
    rounds = unique
    if None in rounds:
        counts["why"]["roster_skipped"] = counts["why"].get("roster_skipped", 0) + 1
        return
    roster.sync(client, environ, rounds, live, context, counts)


def run(
    limit,
    live,
    environ=os.environ,
    client=None,
    evidence=(),
    context=None,
    _probe=False,
    prep_evidence=(),
    roster=False,
):
    context = context or RunContext.start(60)
    counts = {
        key: 0
        for key in (
            "observed",
            "valid_parents",
            "with_rounds",
            "matched",
            "not_found",
            "blocked",
            "human_pages",
            "machine_pages",
            "writes_planned",
            "writes",
            "derived_created",
            "derived_match",
        )
    }
    counts["why"], counts["blocked_why"] = {}, {}
    try:
        context.require_time()
        if not environ.get("NOTION_INTERVIEW_TOKEN") or not environ.get("HIRING_PIPELINE_PAGE_ID"):
            target = "target_config_missing"
        else:
            client = client or notion.make_client(environ)
            target = notion.target_check(client, environ)
        if target != "target_ok":
            _block(counts, target)
            return counts
        if _probe:
            shape = notion.ledger_shape(
                client, (environ.get("NOTION_JOB_LEDGER_DATA_SOURCE_ID") or "").strip(), context
            )
            _reason(counts, shape.pop("code"))
            counts.update(shape)
        rounds = [] if roster else None
        if _survey(client, environ, context, counts, rounds):
            _apply_evidence(client, environ, evidence, limit, live, context, _probe, counts)
            _apply_prep(client, environ, prep_evidence, limit, live, context, counts)
            if roster and not _probe:
                _roster(client, environ, rounds, live, context, counts)
        return counts
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError) as error:
        from .create import cause_of

        _block(counts, "pipeline_incomplete")
        _tally(counts, cause_of(error))
        return counts


def probe(limit, live, **kwargs):
    return run(limit, False, _probe=True, **kwargs)


CONTRACT = stage_contract.Contract(  # run() = target check, read (_survey), apply (_apply_evidence, _apply_prep), decide (pure)
    name="interview-stage",
    run=run,
    inputs=(
        "the Hiring Pipeline page and the Job Ledger (parents and rounds)",
        "page ownership markers",
        "the evidence items and prep items the caller passes",
    ),
    outputs=(
        "observed",
        "valid_parents",
        "with_rounds",
        "matched",
        "not_found",
        "blocked",
        "human_pages",
        "machine_pages",
        "writes_planned",
        "writes",
        "derived_created",
        "derived_match",
        "why",
        "blocked_why",
    ),
    dry_run="reads the pipeline and counts what each evidence item would create or change (writes_planned); changes nothing",
    write="creates or updates machine-owned Interview parents and rounds and their derived prep sections, reading each back; human-owned pages are never written",
)
