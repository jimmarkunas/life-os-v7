"""The experimental package of integrated POC-to-production delivery: is a ticket's package complete, and may it be promoted.

Pure checks on text and on an evidence dict: no network, no write, no new service. `missing` is what `release.py` asks
before it lets a package Task out of an Epic in Scoping. `promotion` is the one decision after the integrated proof:
GO, RETRY with the next approved alternative, NO-GO (prior production kept, no new tickets) or BLOCKED (a missing approval
or proof; nothing is promoted and nothing is marked Done). The rules are in docs/policy/integrated-delivery.md."""

import re

PACKAGE_LABEL = "package"
SECTIONS = (
    ("outcome", r"outcome"),
    ("candidate_pocs", r"candidate\s+pocs?"),
    ("integration", r"integration"),
    ("deployment", r"deployment"),
    ("rollback", r"rollback"),
    ("acceptance_matrix", r"acceptance\s+matrix"),
    ("budget", r"budget"),
    ("alternatives", r"alternatives"),
    ("approval", r"approval"),
)
PROOFS = ("outcome", "security", "cost", "rollback")
ACT = re.compile(r"^\s*[-*•]\s*(?P<act>[^:\n]+?)\s*:\s*(?P<rest>.+)$", re.M)


def _heading_body(text, pattern):
    """The text under a heading that starts with `pattern` (markdown # heading or a bold line), up to the next heading."""
    head = re.compile(r"^[#*\s]*(?:experimental\s+package\W*)?" + pattern + r"\b[^\n]*$", re.I | re.M)
    found = head.search(text)
    if not found:
        return None
    rest = text[found.end() :]
    stop = re.search(r"^(?:#{1,6}\s|\*\*[^\n]+\*\*\s*$)", rest, re.M)
    return (rest[: stop.start()] if stop else rest).strip()


def missing(description):
    """-> the package sections a description lacks or leaves empty, in order; an empty list means complete."""
    from lifeos.jira.release import text_of  # noqa: PLC0415

    text = description if isinstance(description, str) else text_of(description)
    return [name for name, pattern in SECTIONS if not (_heading_body(text, pattern) or "").strip()]


def approval_gaps(description):
    """-> the protected acts in the Approval section that name no target or no budget (each line is `act: target; budget`)."""
    from lifeos.jira.release import text_of  # noqa: PLC0415

    text = description if isinstance(description, str) else text_of(description)
    body = _heading_body(text, r"approval") or ""
    gaps = []
    for found in ACT.finditer(body):
        rest = found.group("rest").lower()
        if "target" not in rest or "budget" not in rest:
            gaps.append(found.group("act").strip())
    return gaps


def promotion(evidence):
    """-> (verdict, reasons) for the integrated proof of a package.

    evidence keys: approval (the named protected approval is on the ticket), unsafe, acceptance ("passed" or "failed"),
    alternatives_left (approved alternatives not yet tried), budget_left, same_head_ci, proofs (the PROOFS covered)."""
    if evidence.get("unsafe"):
        return "NO-GO", ["unsafe_deployment", "retain_prior_production", "no_new_tickets"]
    if evidence.get("acceptance") != "passed":
        if evidence.get("alternatives_left", 0) > 0 and evidence.get("budget_left"):
            return "RETRY", ["acceptance_failed", "next_approved_alternative"]
        return "NO-GO", ["acceptance_failed_exhausted", "retain_prior_production", "no_new_tickets"]
    if not evidence.get("budget_left"):
        return "NO-GO", ["budget_exhausted", "retain_prior_production", "no_new_tickets"]
    blocked = []
    if not evidence.get("approval"):
        blocked.append("missing_protected_approval")
    if not evidence.get("same_head_ci"):
        blocked.append("ci_not_green_on_this_head")
    blocked += ["no_%s_proof" % name for name in PROOFS if name not in (evidence.get("proofs") or ())]
    return ("BLOCKED", blocked) if blocked else ("GO", ["promote_proven_implementation"])
