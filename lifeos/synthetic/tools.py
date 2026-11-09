"""Registered fake tools and their handlers (LIFE-374). A tool is known by name, belongs to one service and carries its risk classes. The handlers touch only the
fake world: a read counts one handler call; a mutation is staged by counting a mutation call and becomes real only when the dispatcher commits it."""
from lifeos.synthetic.authz_fixture import CANARY, ref_hash

READ, WRITE, DELETE = "read", "write", "delete"
PROTECTED = ("send", "share", "booking", "social_publish", "finance")        # need a write grant and an exact Jim approval
MANAGEMENT = ("admin", "proxy")                                               # need an approved management scope (LIFE-372 N11)
ROUTINE = (READ, WRITE, DELETE)
KNOWN_RISKS = ROUTINE + PROTECTED + MANAGEMENT


def read_handler(world, req):
    world.read_calls += 1
    obj = req.get("object_id", "obj-1")
    if obj.endswith("-TIMEOUT"):
        raise TimeoutError(obj)
    text = f"fake content of {obj}" + (f" {CANARY}" if obj.endswith("-CANARY") else "")
    return {"object_id": obj, "text": text}


def mutation_handler(tool):
    def run(world, req):
        world.mutation_calls += 1
        return {"staged": tool, "account": ref_hash(req.get("account_id"))}
    return run


def _tool(service, actions, handler=None, unsupported=False):
    return {"service": service, "actions": tuple(actions), "handler": handler, "unsupported": unsupported}


TOOLS = {
    "jira_read_issue": _tool("jira", [READ], read_handler),
    "jira_write_issue": _tool("jira", [WRITE], mutation_handler("jira_write_issue")),
    "notion_read_page": _tool("notion", [READ], read_handler),
    "notion_write_page": _tool("notion", [WRITE], mutation_handler("notion_write_page")),
    "notion_delete_page": _tool("notion", [DELETE], mutation_handler("notion_delete_page")),
    "gmail_read_message": _tool("gmail", [READ], read_handler),
    "gmail_send_message": _tool("gmail", ["send"], mutation_handler("gmail_send_message")),
    "drive_read_file": _tool("drive", [READ], read_handler),
    "drive_share_file": _tool("drive", ["share"], mutation_handler("drive_share_file")),
    "calendar_book_slot": _tool("calendar", ["booking"], mutation_handler("calendar_book_slot")),
    "social_publish_post": _tool("social", ["social_publish"], mutation_handler("social_publish_post")),
    "finance_transfer": _tool("finance", ["finance"], mutation_handler("finance_transfer")),
    "composio_admin": _tool("composio", ["admin"], mutation_handler("composio_admin")),
    "buzz_proxy_execute": _tool("buzz", ["proxy"], mutation_handler("buzz_proxy_execute")),
    "composio_sandbox_run": _tool("composio", [READ], read_handler, unsupported=True),     # a sandbox or workbench action the plan does not support
    "multi_risk_tool": _tool("gmail", ["send", DELETE], mutation_handler("multi_risk_tool")),
    "mystery_tool": _tool("gmail", ["teleport"], mutation_handler("mystery_tool")),
}


def secret_shaped(value):
    return CANARY in str(value)
