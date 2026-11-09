"""LIFE-403 (epic LIFE-401): the per-agent access policy of the one access layer. Pure functions, no network.

The policy file says, for each agent (by its 64-hex Buzz key) and each service (jira, notion, github, gmail, drive, calendar), one level: off, read or write. A call is one of read, write, delete, bulk
(a write that touches more than `bulk_limit` items). The answer is allow, deny or pending (Jim approves deletes and bulk changes on the toggle page). Fail closed everywhere: a missing,
unreadable or malformed policy, an unknown service or an unknown agent with no default is a deny, never an allow. v1 caps: Jira, GitHub, Gmail, Drive and Calendar are read only, Notion is read and write.
"""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

LEVELS = ("off", "read", "write")
SERVICES = ("jira", "notion", "github", "gmail", "drive", "calendar")                # gmail, drive and calendar added Nov 9 (Jim, LIFE-374)
CAP = {"jira": "read", "notion": "write", "github": "read", "gmail": "read", "drive": "read", "calendar": "read"}       # the most any agent can get in v1 (Jim, Nov 8; read only for the new three)
KINDS = ("read", "write", "delete", "bulk")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BULK_DEFAULT = 10
CENTRAL = ZoneInfo("America/Chicago")                                # audit times are Central, with daylight saving (-06:00 in winter, -05:00 in summer)


class PolicyError(ValueError):
    pass


def load(text):
    """Parse and validate a policy document. Raises PolicyError on anything unexpected (the caller then denies every call)."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        raise PolicyError("not_json") from None
    if not isinstance(doc, dict) or doc.get("version") != 1:
        raise PolicyError("bad_version")
    default = doc.get("default", {})
    agents = doc.get("agents", {})
    if not isinstance(default, dict) or not isinstance(agents, dict):
        raise PolicyError("bad_shape")
    limit = doc.get("bulk_limit", BULK_DEFAULT)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise PolicyError("bad_bulk_limit")
    out_agents = {}
    for key, row in agents.items():
        if not isinstance(key, str) or not HEX64.match(key) or not isinstance(row, dict):
            raise PolicyError("bad_agent")
        out_agents[key] = _levels(row)
    return {"default": _levels(default), "agents": out_agents, "bulk_limit": limit}


def _levels(row):
    out = {}
    for service in SERVICES:
        level = row.get(service, "off")
        if level not in LEVELS:
            raise PolicyError("bad_level")
        out[service] = level
    extra = set(row) - set(SERVICES) - {"name"}
    if extra:
        raise PolicyError("unknown_field")
    return out


def level_of(policy, agent, service):
    """The effective level: the agent's row (or the default), clamped to the v1 cap for the service."""
    row = policy["agents"].get(agent, policy["default"])
    level = row[service]
    return min(level, CAP[service], key=LEVELS.index)


def decide(policy, agent, service, kind, count=1):
    """-> (decision, reason). decision is allow, deny or pending. `policy` is the result of load(), or None when it could not be read."""
    if policy is None:
        return "deny", "policy_unreadable"
    if service not in SERVICES:
        return "deny", "unknown_service"
    if kind not in KINDS or type(count) is not int or count < 1:
        return "deny", "bad_request"
    if not isinstance(agent, str) or not HEX64.match(agent):
        return "deny", "unknown_agent"
    level = level_of(policy, agent, service)
    if level == "off":
        return "deny", "service_off"
    if kind == "read":
        return "allow", "read_allowed"
    if CAP[service] != "write":
        return "deny", "service_read_only_in_v1"
    if level != "write":
        return "deny", "write_not_granted"
    if kind == "delete":
        return "pending", "delete_needs_approval"
    if kind == "bulk" or count > policy["bulk_limit"]:
        return "pending", "bulk_needs_approval"
    return "allow", "write_allowed"


def audit_line(agent, service, op, decision, reason, approver=None, now=None):
    """One audit record: when (Central), who, what service and operation, the decision and its reason, and who approved. No arguments, no page text, no secret."""
    when = (now or datetime.now(CENTRAL)).astimezone(CENTRAL)
    clean = lambda v, n: re.sub(r"[^A-Za-z0-9_.:-]", "_", str(v))[:n]
    return {"t": when.isoformat(timespec="seconds"), "agent": clean(agent, 64), "service": clean(service, 16), "op": clean(op, 80), "decision": clean(decision, 8),
            "reason": clean(reason, 40), "approver": clean(approver, 32) if approver else None}
