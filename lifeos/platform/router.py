"""The hourly router contract for the ChatGPT-native `LIFE OS Daily Runs` task: independent modules with one of three outcomes, and strict
ownership of Daily Report regions. No scheduler, queue, datastore or framework: the ChatGPT task runs hourly outside GitHub and applies
these rules; V7's own hourly workflow is unrelated and unchanged. Fixed codes only: no content, ids or messages."""
PASS, NO_ACTION, DEGRADED = "PASS", "NO_ACTION", "DEGRADED"
OUTCOMES = (PASS, NO_ACTION, DEGRADED)

# A Daily Report region is a callout whose first child heading names its owner. Nothing else is writable by any module.
JIRA_REGION = "JIRA Execution"                          # V7 owns it exclusively (lifeos/jira/card.py): ChatGPT modules never touch it
CALENDAR_REGION = "Calendar"                            # V7 owns it exclusively (lifeos/agenda/card.py)
DCC_REGION = "ChatGPT · Daily Command Center"      # the one region the Daily Command Center module may write
OWNERS = {JIRA_REGION: "v7-jira", CALENDAR_REGION: "v7-calendar", DCC_REGION: "daily-command-center"}


class RouterError(RuntimeError):
    """The message is always a fixed code."""


def run_modules(modules):
    """modules: ordered [(name, callable returning PASS | NO_ACTION | DEGRADED)]. Each runs on its own: one that raises or returns anything
    else is DEGRADED, and never stops the rest. -> {"modules": {name: outcome}, "overall": outcome, "summary": one compact line}."""
    names = [name for name, _ in modules]
    if len(set(names)) != len(names):
        raise RouterError("ROUTER_DUPLICATE_MODULE")
    outcomes = {}
    for name, module in modules:
        try:
            result = module()
        except Exception:                                # nothing about the failure is kept: it must not leak and must not spread
            result = DEGRADED
        outcomes[name] = result if result in OUTCOMES else DEGRADED
    values = set(outcomes.values())
    overall = DEGRADED if DEGRADED in values else PASS if PASS in values else NO_ACTION
    return {"modules": outcomes, "overall": overall, "summary": " | ".join(f"{n}={o}" for n, o in outcomes.items())}


def resolve_target(regions, module):
    """regions: [(heading, block_id)] of the page. -> the one block id `module` may write. Missing is never created, ambiguous never guessed."""
    heading = next((h for h, owner in OWNERS.items() if owner == module), None)
    if heading is None:
        raise RouterError("ROUTER_MODULE_OWNS_NOTHING")
    found = [block_id for text, block_id in regions if text.strip() == heading]
    if len(found) != 1:
        raise RouterError("ROUTER_TARGET_MISSING" if not found else "ROUTER_TARGET_AMBIGUOUS")
    return found[0]


def check_write(module, headings):
    """Every region a write would touch must be owned by `module`; an unknown region is not owned by anyone, so it fails closed too."""
    if any(OWNERS.get(h.strip()) != module for h in headings):
        raise RouterError("ROUTER_REGION_NOT_OWNED")


def protected_intact(module, before, after):
    """before/after: {heading: any stable digest of that region}. True only if every region owned by someone else is exactly as it was:
    present stays present and unchanged, absent stays absent (a missing V7 block is never recreated)."""
    return all(before.get(h) == after.get(h) for h, owner in OWNERS.items() if owner != module)
