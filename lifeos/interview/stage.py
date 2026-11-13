"""Counts-only Interview probe and bounded B2 evidence execution."""
import os
from lifeos.platform import stage_contract
from lifeos.platform.runtime import RunContext, DeadlineExceeded
from lifeos.platform.notion_client import NotionError
from . import notion
from .identity import resolve_parent
from .models import Ownership, ParentQuery, State
from lifeos.platform.names import split_title


QUIET = ("parent_created", "round_created", "parent_exists", "parent_adopted", "adopt_allowed", "parent_upgraded",
         "upgrade_allowed", "parent_refreshed", "refresh_allowed", "transcript_linked", "transcript_link_allowed",
         "round_confirmed", "confirm_allowed", "round_rescheduled", "reschedule_allowed",
         "round_cancelled", "cancel_allowed", "round_details", "details_allowed", "round_unchanged",
         "refresh_not_machine", "refresh_has_rounds")           # evidence outcomes that are not a block
DERIVED_OK = ("derived_allowed", "derived_created", "derived_match")


def _reason(counts, code):
    counts["why"][code] = counts["why"].get(code, 0) + 1


def _block(counts, code):
    counts["blocked"] += 1
    _reason(counts, code)


def _tally(counts, cause):
    causes = counts.setdefault("causes", {})                       # counts only: a fixed code or the exception's class name
    causes[cause] = causes.get(cause, 0) + 1


def evidence_outcome(result):
    """PURE: how one evidence result moves the counts. -> (the count it adds one to, or None; stop the loop)."""
    code = result["code"]
    bump = "matched" if code == "child_match" else "not_found" if code in ("create_allowed", "parent_create_allowed") else None if code in QUIET else "blocked"
    return bump, bool(result["readback_failed"] or code == "write_failed")


def prep_outcome(result):
    """PURE: how one prep result moves the counts. -> (blocked, stop the loop)."""
    code = result["code"]
    return code not in DERIVED_OK, code in ("derived_readback_failed", "readback_protected_changed") or (result["writes"] > 0 and code != "derived_created")


def _survey(client, environ, context, counts):
    """READ: every active parent and its rounds, and who owns each page. -> False when the pipeline could not be read completely."""
    scan = notion.parents(client, environ, context)
    counts["observed"] = len(scan.items)
    counts["parents_ledger"], counts["parents_tree_only"] = scan.ledger, scan.tree_only        # LIFE-360: proof the Ledger path ran (ledger > 0), not the page-tree fallback alone
    if not scan.complete:
        _block(counts, "pipeline_incomplete")
        return False
    for parent in scan.items:
        if not parent.active:
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
    return True


def _apply_evidence(client, environ, evidence, limit, live, context, probe, counts):
    """APPLY each evidence item (create.apply writes and reads back); the outcome of each is tallied by evidence_outcome."""
    from .create import apply
    deferred = set()
    for index, (parent_query, round_query) in enumerate(evidence):
        if index >= max(0, limit):
            if any(not q.refresh_only for _, q in evidence[index:]):
                _block(counts, "pipeline_incomplete")
            else:                                                   # E05: refresh-only items come last; the ones past the limit are counted, not an alarm
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
        if stop:
            break


def _apply_prep(client, environ, prep_evidence, limit, live, context, counts):
    """APPLY each derived prep write; the outcome of each is tallied by prep_outcome."""
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
        if stop:
            break


def run(limit, live, environ=os.environ, client=None, evidence=(), context=None, _probe=False, prep_evidence=()):
    context = context or RunContext.start(60)
    counts = {key: 0 for key in ("observed", "valid_parents", "with_rounds", "matched", "not_found", "blocked",
        "human_pages", "machine_pages", "writes_planned", "writes", "derived_created", "derived_match")}
    counts["why"] = {}
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
        if _probe:                                                      # LIFE-361: creation needs the Ledger's Stage (six options) and Manual source, not a container page
            shape = notion.ledger_shape(client, (environ.get("NOTION_JOB_LEDGER_DATA_SOURCE_ID") or "").strip(), context)
            _reason(counts, shape.pop("code"))
            counts.update(shape)
        if _survey(client, environ, context, counts):
            _apply_evidence(client, environ, evidence, limit, live, context, _probe, counts)
            _apply_prep(client, environ, prep_evidence, limit, live, context, counts)
        return counts
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError) as error:
        from .create import cause_of
        _block(counts, "pipeline_incomplete")
        _tally(counts, cause_of(error))
        return counts


def probe(limit, live, **kwargs):
    return run(limit, False, _probe=True, **kwargs)


CONTRACT = stage_contract.Contract(      # LIFE-623: the stage declared; run() = target check, read (_survey), apply (_apply_evidence, _apply_prep; each is write then read-back inside create/prep.apply), decide (evidence_outcome, prep_outcome: pure)
    name="interview-stage", run=run,
    inputs=("the Hiring Pipeline page and the Job Ledger (parents and rounds)", "page ownership markers", "the evidence items and prep items the caller passes"),
    outputs=("observed", "valid_parents", "with_rounds", "matched", "not_found", "blocked", "human_pages", "machine_pages", "writes_planned", "writes", "derived_created", "derived_match", "why"),
    dry_run="reads the pipeline and counts what each evidence item would create or change (writes_planned); changes nothing",
    write="creates or updates machine-owned Interview parents and rounds and their derived prep sections, reading each back; human-owned pages are never written")
