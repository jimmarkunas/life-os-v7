"""Bounded counts-only read-only Interview stages. B1 has no mutation path."""
import os
from lifeos.platform.runtime import RunContext, DeadlineExceeded
from lifeos.platform.notion_client import NotionError
from . import notion
from .identity import resolve_parent, resolve_child, creation_eligibility
from .models import Ownership, ParentQuery, State
from lifeos.platform.names import split_title


def run(limit, live, environ=os.environ, client=None, evidence=(), context=None):
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
        for index, (parent_query, round_query) in enumerate(evidence):
            if index >= max(0, limit):
                counts["blocked"] += 1
                reason("pipeline_incomplete")
                break
            context.require_time()
            parent = resolve_parent(parent_query, scan)
            if parent.state != State.MATCH:
                result = parent
            else:
                children = notion.child_scan(client, parent.page_id, context)
                explicit = notion.explicit_child(client, round_query.explicit_child_page_id) if round_query.explicit_child_page_id else None
                result = resolve_child(parent, round_query, children, explicit)
                reason(creation_eligibility(target, parent, owners.get(parent.page_id, Ownership.UNKNOWN), result, children, round_query))
            counts[{State.MATCH: "matched", State.NOT_FOUND: "not_found", State.BLOCKED: "blocked"}[result.state]] += 1
            reason(result.code)
        return counts
    except (NotionError, DeadlineExceeded, KeyError, TypeError, ValueError):
        counts["blocked"] += 1
        reason("pipeline_incomplete")
        return counts


def probe(limit, live, **kwargs):
    return run(limit, live, **kwargs)
