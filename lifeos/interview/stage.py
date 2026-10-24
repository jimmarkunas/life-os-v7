"""Counts-only Interview probe and bounded B2 evidence execution."""
import os
from lifeos.platform.runtime import RunContext, DeadlineExceeded
from lifeos.platform.notion_client import NotionError
from . import notion
from .identity import resolve_parent
from .models import Ownership, ParentQuery, State
from lifeos.platform.names import split_title


def run(limit, live, environ=os.environ, client=None, evidence=(), context=None, _probe=False, prep_evidence=()):
    context = context or RunContext.start(60)
    counts = {key: 0 for key in ("observed", "valid_parents", "with_rounds", "matched", "not_found", "blocked",
        "human_pages", "machine_pages", "writes_planned", "writes", "derived_created", "derived_match")}
    counts["why"] = {}

    def reason(code):
        counts["why"][code] = counts["why"].get(code, 0) + 1

    try:
        context.require_time()
        if not environ.get("NOTION_INTERVIEW_TOKEN") or not environ.get("HIRING_PIPELINE_PAGE_ID"):
            target = "target_config_missing"
        else:
            client = client or notion.make_client(environ)
            target = notion.target_check(client, environ)
        if target != "target_ok":
            counts["blocked"] += 1
            reason(target)
            return counts
        if _probe:
            shape = notion.active_shape(client, environ["HIRING_PIPELINE_PAGE_ID"].strip(), context)
            reason(shape.pop("code"))
            counts.update(shape)
        scan = notion.parent_scan(client, environ["HIRING_PIPELINE_PAGE_ID"].strip(), context)
        counts["observed"] = len(scan.items)
        if not scan.complete:
            counts["blocked"] += 1
            reason("pipeline_incomplete")
            return counts
        owners = {}
        for parent in scan.items:
            if not parent.active:
                continue
            context.require_time()
            company, role = split_title(parent.title)
            if not company.strip() or not role.strip():
                counts["blocked"] += 1
                reason("identity_invalid")
                continue
            resolved = resolve_parent(ParentQuery(company, role), scan)
            if resolved.state == State.BLOCKED:
                counts["blocked"] += 1
                reason(resolved.code)
                continue
            counts["valid_parents"] += 1
            owner = notion.ownership(client, parent.page_id, "opportunity")
            owners[parent.page_id] = owner
            counts["machine_pages" if owner == Ownership.MACHINE else "human_pages"] += int(owner != Ownership.UNKNOWN)
            if owner == Ownership.UNKNOWN:
                counts["blocked"] += 1
                reason("ownership_unknown")
            children = notion.child_scan(client, parent.page_id, context)
            if not children.complete:
                counts["blocked"] += 1
                reason("child_scan_incomplete")
                continue
            counts["with_rounds"] += int(bool(children.items))
            for child in children.items:
                owner = notion.ownership(client, child.page_id, "round")
                if owner == Ownership.UNKNOWN:
                    counts["blocked"] += 1
                    reason("ownership_unknown")
                else:
                    counts["machine_pages" if owner == Ownership.MACHINE else "human_pages"] += 1
        from .create import apply
        deferred = set()
        for index, (parent_query, round_query) in enumerate(evidence):
            if index >= max(0, limit):
                if any(not q.refresh_only for _, q in evidence[index:]):     # E05: refresh-only items come last; the ones past the limit simply wait for the next run
                    counts["blocked"] += 1
                    reason("pipeline_incomplete")
                break
            if _probe:
                break
            result = apply(client, environ, parent_query, round_query, live, context, deferred)
            for key, value in result.items():
                if key not in ("code", "cause"):
                    counts[key] = counts.get(key, 0) + value
            if result.get("cause"):
                causes = counts.setdefault("causes", {})
                causes[result["cause"]] = causes.get(result["cause"], 0) + 1
            reason(result["code"])
            if result["code"] == "child_match":
                counts["matched"] += 1
            elif result["code"] in ("create_allowed", "parent_create_allowed"):
                counts["not_found"] += 1
            elif result["code"] not in ("parent_created", "round_created", "parent_exists", "parent_adopted", "adopt_allowed", "parent_upgraded",
                                           "upgrade_allowed", "parent_refreshed", "refresh_allowed", "transcript_linked", "transcript_link_allowed",
                                           "round_confirmed", "confirm_allowed", "round_rescheduled", "reschedule_allowed",
                                           "round_cancelled", "cancel_allowed", "round_details", "details_allowed", "round_unchanged",
                                           "refresh_not_machine", "refresh_has_rounds"):
                counts["blocked"] += 1
            if result["readback_failed"] or result["code"] == "write_failed":
                break
        from .prep import apply as apply_prep
        for index, (parent_query, round_query, prep) in enumerate(prep_evidence):
            if index >= max(0, limit):
                reason("pipeline_incomplete")
                counts["blocked"] += 1
                break
            result = apply_prep(client, environ, parent_query, round_query, prep, live, context)
            counts["writes_planned"] += result["writes_planned"]
            counts["writes"] += result["writes"]
            counts["derived_created"] += int(result["code"] == "derived_created")
            counts["derived_match"] += int(result["code"] == "derived_match")
            reason(result["code"])
            if result["code"] not in ("derived_allowed", "derived_created", "derived_match"):
                counts["blocked"] += 1
            if result["code"] in ("derived_readback_failed", "readback_protected_changed") or (
                    result["writes"] > 0 and result["code"] != "derived_created"):
                break
        return counts
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError) as error:
        from .create import cause_of
        counts["blocked"] += 1
        reason("pipeline_incomplete")
        causes = counts.setdefault("causes", {})                       # counts only: a fixed code or the exception's class name
        causes[cause_of(error)] = causes.get(cause_of(error), 0) + 1
        return counts


def probe(limit, live, **kwargs):
    return run(limit, False, _probe=True, **kwargs)
