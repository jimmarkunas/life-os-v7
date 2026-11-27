"""Classify a closed Jira issue so administrative cancellations never count as delivered work.

Every closed issue falls in exactly one class: delivered_manual, delivered_auto, cancelled or unknown (delivered, but
closed before the manual/automatic split was tracked). Missing or contradictory evidence raises JiraError, which the
accountability stages report as a DEGRADED region.
"""

from lifeos.platform.jira import JiraError

AUTO_LABEL = "auto-closed"
SUPERSEDED_LABEL = "superseded"
DELIVERED_RESOLUTION = "Done"
CLASSES = ("delivered_manual", "delivered_auto", "cancelled", "unknown")
CORRECTION_VERSION = "cancellations-excluded-1"
FIELDS = "resolution,labels,statuscategorychangedate"


def _evidence(fields):
    if "resolution" not in fields or "labels" not in fields:
        raise JiraError("ACCOUNTABILITY_CLOSURE_EVIDENCE_MISSING")
    resolution = (fields["resolution"] or {}).get("name")
    labels = fields["labels"]
    if not resolution or not isinstance(labels, list):
        raise JiraError("ACCOUNTABILITY_CLOSURE_EVIDENCE_MISSING")
    return resolution, set(labels)


def classify(fields, closed_at=None, since=None):
    """-> one of CLASSES for a closed issue's `fields`. `since` is when manual/automatic tracking began (aware datetime)."""
    resolution, labels = _evidence(fields)
    cancelled = resolution != DELIVERED_RESOLUTION
    if (SUPERSEDED_LABEL in labels and not cancelled) or (AUTO_LABEL in labels and cancelled):
        raise JiraError("ACCOUNTABILITY_CLOSURE_EVIDENCE_CONTRADICTORY")
    if cancelled or SUPERSEDED_LABEL in labels:
        return "cancelled"
    if AUTO_LABEL in labels:
        return "delivered_auto"
    if since is not None and closed_at is not None and closed_at >= since:
        return "delivered_manual"
    return "unknown"


def counts(classes):
    """-> {class: n, "total": n}; the four classes always add up to the total."""
    out = {name: 0 for name in CLASSES}
    for name in classes:
        out[name] += 1
    out["total"] = sum(out.values())
    return out


def snapshot_pair(original, corrected):
    """A published snapshot is never edited: the corrected figures sit beside it under an explicit version."""
    return {"original": original, "corrected": corrected, "correction_version": CORRECTION_VERSION}
