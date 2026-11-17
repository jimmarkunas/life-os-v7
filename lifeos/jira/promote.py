"""LIFE-742: may an agent move this ticket to Agent Go itself, or must it wait for Jim? A pure check on a fetched Jira issue, no network and no write."""
# Jim, Nov 16 2026: agents may promote tickets on their own, "everything but the scary list". The rule is written once in docs/development-policy/04-work-selection-lifecycle-reconciliation.md.
# This module only decides. The agent then makes transition 17 (To Do to Agent Go) and posts the one comment, or posts `[waiting on] Jim: <the one thing>`.
import re

from lifeos.jira.release import blockers_done, has_done_when, text_of

# LIFE-753: the label an agent adds with every `[waiting on] Jim:` comment; the saved filter "Waiting on Jim" lists the tickets that carry it.
WAITING_LABEL = "needs-jim"                                   # LIFE-780: the one workflow-control label (was waiting-on-jim, jim-only, hold, planning-only, needs-approval, protected-file)

# The scary list: a ticket whose description names one of these steps needs Jim's yes at the moment it arises. Each pattern reads the ticket text, case-insensitive.
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
# A ticket may say what it will NOT do ("No credentials"): a sentence under a STOP, Not in scope or Never heading names refusals, not needs, so those lines are not scanned.
REFUSAL_HEADING = re.compile(r"^\W*(stop|not in scope|never|out of scope)\b", re.I)


def scanned_lines(description):
    """The description's lines outside any STOP / Not in scope / Never section."""
    out, skipping = [], False
    for line in text_of(description).splitlines():
        if re.match(r"^\W*(outcome|done when|steps|scope|authority|release|facts|targets|who verifies)\b", line, re.I):
            skipping = False
        if REFUSAL_HEADING.match(line):
            skipping = True
            continue
        if not skipping:
            out.append(line)
    return out


def protected_steps(description):
    """-> sorted names of the protected categories the ticket's own needs mention (empty means none)."""
    body = "\n".join(scanned_lines(description))
    return sorted(name for name, pattern in COMPILED.items() if pattern.search(body))


def decide(issue):
    """-> ("promote", []) | ("wait", [categories]) | ("skip", [reason]). `skip` changes nothing in Jira; `wait` means post `[waiting on] Jim:`; `promote` means transition 17 plus one comment."""
    fields = issue.get("fields") or {}
    status = ((fields.get("status") or {}).get("name") or "To Do")
    if status not in ("To Do", "To do"):
        return "skip", ["not_to_do"]
    if fields.get("issuetype", {}).get("name") == "Epic":
        return "skip", ["epic"]
    if not has_done_when(fields.get("description")):
        return "skip", ["no_done_when"]
    if not blockers_done(fields.get("issuelinks")):
        return "skip", ["blocked"]
    if WAITING_LABEL in (fields.get("labels") or []):
        return "skip", ["skip_label"]
    needs = protected_steps(fields.get("description"))
    if needs:
        return "wait", needs
    return "promote", []


def act(issue):
    """-> {"decision", "reasons", "label", "comment"}: on `wait`, the label and exact comment to apply in one step (LIFE-753); otherwise both are None."""
    decision, reasons = decide(issue)
    if decision != "wait":
        return {"decision": decision, "reasons": reasons, "label": None, "comment": None}
    return {"decision": decision, "reasons": reasons, "label": WAITING_LABEL, "comment": f"[waiting on] Jim: approve {', '.join(r.replace('_', ' ') for r in reasons)} before this ticket moves to Agent Go"}
