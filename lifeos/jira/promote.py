"""May an agent move this ticket to Agent Go itself, or must it wait for Jim? A pure check on a fetched Jira issue: no network, no write."""

# Agents promote on their own except for the scary list below (policy: docs/life-os-development-policy.md section 4).
# The agent makes transition 17 (To Do to Agent Go) and posts the one comment, or `[waiting on] Jim: <the one thing>`.
import re

from lifeos.jira.release import JIM_FIRST, gate, has_done_when, jim_first, text_of

WAITING_LABEL = "needs-jim"

# The scary list: steps that need Jim's yes when they arise, matched case-insensitively against the ticket text.
PROTECTED = {
    "credential": r"\b(credentials?|api[ _-]?keys?|access tokens?|oauth|bitwarden|secrets?|passwords?|rotat\w+ (a )?(token|key))\b",
    "live_access": r"\b(live (provider|read|write|access|run|data)s?|production (data|access|deploy\w*|change)s?|provider data|real (provider )?data)\b",
    "host_install": r"\b(install\w* (on|into|to) (the )?(oracle|mac|host)|oracle (run line|setup|config\w*|install\w*)|systemd|ssh to|sudo)\b",
    "spend": r"\b(paid (service|model|usage|provider)|model spend|new subscription|spend beyond|budget increase)\b",
    "schema": r"\b(create (a |the )?(new )?(table|database|datastore|schema)|schema change|new datastore|migration)\b",
    "delete": r"\b(delete (production|live|github) \w+|hard[- ]delete|drop (a |the )?table|purge)\b",
    "workflow_security": r"\b(workflow `?permissions`?|write permission|branch protection|security setting|deny rules?|protected[- ]file)\b",
    "ingress": r"\b(public (endpoint|ingress|url)|open(ing)? a port|cloudflare tunnel|tailscale (config|funnel))\b",
}
COMPILED = {name: re.compile(pattern, re.I) for name, pattern in PROTECTED.items()}
# Lines under a STOP, Not in scope or Never heading name refusals, not needs, so they are not scanned.
REFUSAL_HEADING = re.compile(r"^\W*(stop|not in scope|never|out of scope)\b", re.I)


def scanned_lines(description):
    out, skipping = [], False
    for line in text_of(description).splitlines():
        if re.match(r"^\W*(outcome|done when|steps|scope|authority|release|facts|targets|who verifies)\b", line, re.I):
            skipping = False
        if REFUSAL_HEADING.match(line) or JIM_FIRST.match(line):
            skipping = True
            continue
        if not skipping:
            out.append(line)
    return out


def protected_steps(description):
    body = "\n".join(scanned_lines(description))
    return sorted(name for name, pattern in COMPILED.items() if pattern.search(body))


def decide(issue):
    """-> ("promote", []) | ("wait", [categories]) | ("skip", [reason]). `skip` changes nothing in Jira."""
    fields = issue.get("fields") or {}
    status = (fields.get("status") or {}).get("name") or "To Do"
    if status not in ("To Do", "To do"):
        return "skip", ["not_to_do"]
    if fields.get("issuetype", {}).get("name") == "Epic":
        return "skip", ["epic"]
    if not has_done_when(fields.get("description")):
        return "skip", ["no_done_when"]
    why = gate(fields)
    if why:
        return "skip", [why]
    present, open_items = jim_first(fields.get("description"))
    if not present:
        return "skip", ["no_jim_first"]
    if open_items:
        return "wait", ["jim_first"]
    needs = protected_steps(fields.get("description"))
    if needs:
        return "wait", needs
    return "promote", []


def act(issue):
    """-> {"decision", "reasons", "label", "comment"}; on `wait`, the label and exact comment to apply, else both
    None."""
    decision, reasons = decide(issue)
    if decision != "wait":
        return {"decision": decision, "reasons": reasons, "label": None, "comment": None}
    return {
        "decision": decision,
        "reasons": reasons,
        "label": WAITING_LABEL,
        "comment": (
            "[waiting on] Jim: clear the Jim must do first items before this ticket moves to Agent Go"
            if reasons == ["jim_first"]
            else f"[waiting on] Jim: approve {', '.join(r.replace('_', ' ') for r in reasons)} before this ticket moves to Agent Go"
        ),
    }
