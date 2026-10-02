"""Counts-only Interview probe and bounded B2 evidence execution."""
import os
from lifeos.platform.runtime import RunContext, DeadlineExceeded
from lifeos.platform.notion_client import NotionError
from . import notion
from .identity import resolve_parent
from .models import Ownership, ParentQuery, State
from lifeos.platform.names import split_title


def run(limit, live, environ=os.environ, client=None, evidence=(), context=None, _probe=False):
    context = context or RunContext.start(60)
    counts = {key: 0 for key in ("observed", "valid_parents", "with_rounds", "matched", "not_found", "blocked",
                                "human_pages", "machine_pages", "writes_planned", "writes")}
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
                counts["blocked"] += 1
                reason("pipeline_incomplete")
                break
            if _probe:
                break
            result = apply(client, environ, parent_query, round_query, live, context, deferred)
            for key, value in result.items():
                if key != "code":
                    counts[key] = counts.get(key, 0) + value
            reason(result["code"])
            if result["code"] == "child_match":
                counts["matched"] += 1
            elif result["code"] in ("create_allowed", "parent_create_allowed"):
                counts["not_found"] += 1
            elif result["code"] not in ("parent_created", "round_created", "parent_exists"):
                counts["blocked"] += 1
            if result["readback_failed"] or result["code"] == "write_failed":
                break
        return counts
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        counts["blocked"] += 1
        reason("pipeline_incomplete")
        return counts


def probe(limit, live, **kwargs):
    return run(limit, False, _probe=True, **kwargs)
