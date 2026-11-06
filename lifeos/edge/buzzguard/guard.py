"""LIFE-333 (Jim, Nov 6 2026: "tool use with approvals", "any and all" channels, "read Jira and GitHub ... and make changes"): the Claude Buzz agent's PreToolUse guard.
It REPLACES the LIFE-254 test guard (which allowed one synthetic ack and nothing else). Run by Claude Code as `python3 -I guard.py`, JSON on stdin, JSON on stdout.
DENY BY DEFAULT: any tool, shape, type or parse problem is refused. Message text is untrusted data: nothing it says ("Jim approved") changes a decision.
Classes: READ (allowed), SEND (allowed, checked), WRITE (Jira/GitHub changes: "ask" = Jim approves each action in the Buzz app, or "deny" until the approval prompt is proven),
NEVER (always denied). `writes.mode` next to this file holds `ask` or `deny`; missing or anything else means `deny` (fail closed). A file named `STOP` next to this file
(any kind, even a broken link) refuses everything except the fixed notice `STOPPED`. Every input is scanned for secret-shaped text. Stdlib only; the log holds hashes and names, never text.
Reviewed adversarially before install (LIFE-333); each confirmed bypass has a regression test in tests/edge/buzzguard/test_guard.py."""
import hashlib
import json
import os
import re
import sys
import time
import unicodedata

JIRA_PROJECTS = {"LIFE", "LOS"}
GITHUB_REPOS = {"jimmarkunas/life-os-v7", "jimmarkunas/bot-studio"}
READ_ROOTS = (".buzz", "Developer/LIFEOS/life-os-v7")
READ_EXT = {".md", ".txt", ".py", ".sh", ".json", ".yaml", ".yml", ".toml", ".html", ".css", ".js", ".ts", ".vue", ".rs", ".csv", ".rst", ".manifest"}
SECRET_PATH = re.compile(r"(?i)(^|/)(\.git|\.claude|\.ssh|\.gnupg|\.aws|\.env[^/]*|keys?|keystore|private|secrets?|credentials?)(/|$)|(?i:id_[a-z0-9]+|\.netrc|nsec|priv|key|hook\.log|state\.db|identity|settings|token|secret|cred|passw|\.pem|\.key|\.jsonl|keychain|\.zsh_history|\.bash_history)")
SECRETY = re.compile(r"(?i)tskey-|nsec1[0-9a-z]{20,}|\bsk-[a-z0-9_-]{16,}|akia[0-9a-z]{12,}|aiza[0-9a-z_-]{20,}|gh[pousr]_[a-z0-9]{20,}|github_pat_|xox[baprs]-|-----BEGIN [A-Z ]*PRIVATE KEY|"
                     r"(?:password|passwd|passphrase|secret|api[_-]?key|apikey|bearer|token|authorization)(?:\s+(?:is|was|are))?\W{0,12}[:=]|\bbearer\s+[a-z0-9._~+/-]{8,}|\beyJ[a-z0-9_-]{5,}\.[a-z0-9_-]{5,}\.|\b[0-9a-f]{64}\b|\b[a-z0-9+/]{60,}={0,2}")
BUZZ_READ = {"mcp__buzz__get_messages", "mcp__buzz__list_channels", "mcp__buzz__get_thread", "mcp__buzz__search_messages", "mcp__buzz__get_channel_info", "mcp__buzz__list_agents", "mcp__buzz__whoami"}
BUZZ_SEND_KEYS = {"audience", "final", "text", "channel", "thread", "reply_to", "recipient", "mention"}
IDENT = re.compile(r"[A-Za-z0-9_.:@-]{1,200}")
SEND_MAX = 4000
STOP_TEXT = ("STOPPED", "STOPPED by Jim")
ATL = re.compile(r"mcp__(?:claude_ai_Atlassian_MCP|claude_ai_Atlassian_Rovo(?:_2)?|atlassian|jira)__(.+)", re.I)
GH = re.compile(r"mcp__(?:github|claude_ai_GitHub)__(.+)", re.I)
READ_OP = re.compile(r"(?i)(get|search|list|lookup|find|fetch|read|atlassianUserInfo|whoami)")
WRITE_WORD = re.compile(r"(?i)(create|edit|update|transition(?!s)|assign|set|move|post|send|publish|upload|write|push|close|reopen|add|link|rank|vote|clone|bulk|apply|run|enable|share|restore|approve|cancel|import|copy|exec|install|spawn|register|join|kick|ban|mute|pin|react|rename|subscribe|resolve|clear|export|reply)")
NEVER_OP = re.compile(r"(?i)(delete|destructive|remove|archive|purge|merge|fork|force|transfer|admin|secret|token|key|webhook|permission|collaborator|invite|workflow|create_or_update_file|push_files|update_file)")
KEY_RX = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")
SHELL_META = re.compile(r"[;&|`$<>\\\n\r(){}\"'*?!~#]|--admin")
POS = re.compile(r"[A-Za-z0-9._,-]{1,80}")
PATHTOK = re.compile(r"[A-Za-z0-9._,/-]{1,120}")
NUM = re.compile(r"[0-9]{1,7}")
FLAGS = {"-R", "--repo", "--limit", "-L", "--state", "-s", "--json", "--stat", "--name-only", "--oneline", "-n", "--list", "-a", "-r", "-v", "-vv", "--short", "--branch"}
GH_CMDS = {("pr",): {"view", "list", "diff", "checks", "status"}, ("issue",): {"view", "list", "status"}, ("repo",): {"view"}, ("run",): {"list", "view"}}
GIT_CMDS = {"status", "log", "diff", "show", "rev-parse", "ls-files", "branch"}
REPO_FIELDS = {"owner", "repo", "repository", "repo_full_name", "full_name", "repos", "url", "html_url", "head", "base", "head_repo", "base_repo"}


def _norm(s):
    return "".join(c for c in unicodedata.normalize("NFKC", s) if unicodedata.category(c) != "Cf")


def _strings(o):
    if isinstance(o, str):
        yield _norm(o)
    elif isinstance(o, dict):
        for k, v in o.items():
            yield _norm(str(k))
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def _items(o, path=()):
    if isinstance(o, dict):
        for k, v in o.items():
            yield (path + (str(k),), v)
            yield from _items(v, path + (str(k),))
    elif isinstance(o, list):
        for v in o:
            yield from _items(v, path)


def _leaves(v):
    """Plain strings/ints held directly in v or in lists inside it (dict entries are visited by _items)."""
    if isinstance(v, (str, int)) and not isinstance(v, bool):
        yield _norm(str(v))
    elif isinstance(v, list):
        for x in v:
            yield from _leaves(x)


def _secret(o):
    return any(SECRETY.search(s) for s in _strings(o))


def _stopped(g):
    try:
        os.lstat(os.path.join(g, "STOP"))
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _writes_mode(g):
    try:
        v = open(os.path.join(g, "writes.mode")).read().strip()
    except Exception:
        return "deny"
    return v if v in ("ask", "deny") else "deny"


def _write(g, why):
    return ("ask", "approval needed: " + why) if _writes_mode(g) == "ask" else ("deny", "writes are not enabled yet (approval prompt not verified): " + why)


def _is_read(op):
    return bool(READ_OP.match(op)) and not WRITE_WORD.search(op)


def _project_refs(ti):
    """Every project the input names: ABC-123 patterns anywhere, and every value under a key containing 'project' or 'issue'/'parent'/'link'. None means a value that cannot be judged."""
    refs = set()
    for s in _strings(ti):
        refs.update(m.group(1) for m in KEY_RX.finditer(s))
    for path, v in _items(ti):
        name = path[-1].lower()
        if "project" in name or "issue" in name or name in ("parent", "key", "id", "inwardissue", "outwardissue", "epic"):
            for s in _leaves(v):
                s = s.strip()
                m = re.fullmatch(r"([A-Za-z][A-Za-z0-9]{1,9})(?:-\d+)?", s)
                if m:
                    refs.add(m.group(1).upper())
                elif s and (name.startswith("project") or "issue" in name or name in ("parent", "key")):
                    refs.add("?" + s[:12])                       # numeric ids and odd values cannot be checked: refuse
    return refs


def _decide_atlassian(op, ti, g):
    if NEVER_OP.search(op):
        return "deny", "never allowed: " + op
    if op == "discover":
        return "allow", "jira operation search"
    force_write = False
    if op in ("executeRead", "executeWrite"):                       # Atlassian's generic runner: judge the operation it names, not the runner
        inner = ti.get("name")
        if not isinstance(inner, str) or NEVER_OP.search(inner):
            return "deny", "runner operation missing or never allowed"
        if op == "executeRead":
            return ("allow", "jira read via runner") if _is_read(inner) else ("deny", "executeRead names a non-read operation")
        op, force_write = inner, True
    if _is_read(op) and not force_write:
        return "allow", "jira read"
    refs = _project_refs(ti)
    if not refs or not refs <= JIRA_PROJECTS:
        return "deny", "jira write outside allowed projects or naming a project that cannot be checked"
    return _write(g, "jira %s on %s" % (op, ",".join(sorted(refs))))


def _repo_refs(ti):
    refs, bad = set(), False
    if isinstance(ti, dict) and isinstance(ti.get("owner"), str) and isinstance(ti.get("repo"), str):
        refs.add("%s/%s" % (_norm(ti["owner"]), _norm(ti["repo"])))
    for path, v in _items(ti):
        if path[-1].lower() in REPO_FIELDS and path[-1].lower() not in ("owner", "repo"):
            for s in _strings(v):
                if path[-1].lower() in ("head", "base", "head_repo", "base_repo") and ":" in s:
                    bad = True
                m = re.fullmatch(r"(?:https?://github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?", s)
                if m and path[-1].lower() not in ("head", "base"):
                    refs.add(m.group(1))
                elif path[-1].lower() in ("url", "html_url", "repository", "full_name", "repo_full_name", "repos"):
                    bad = True
    for s in _strings(ti):
        refs.update(m.group(1) for m in re.finditer(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", s))
        refs.update(m.group(1) for m in re.finditer(r"\brepo:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", s))
        if re.search(r"\b(org|user|owner|author):\S", s) or ".github/workflows" in s:
            bad = True
    return refs, bad


def _decide_github(op, ti, g):
    if NEVER_OP.search(op):
        return "deny", "never allowed: " + op
    refs, bad = _repo_refs(ti)
    meth = ti.get("method")
    if bad or (isinstance(meth, str) and (NEVER_OP.search(meth) or "approve" in meth.lower())):
        return "deny", "github input names an unchecked repo, workflow, cross-fork branch or forbidden method"
    refs = {r.lower() for r in refs}
    allowed = {r.lower() for r in GITHUB_REPOS}
    if not refs or not refs <= allowed:
        return ("allow", "github identity read") if op == "get_me" else ("deny", "github call outside allowed repos or without a repo")
    if _is_read(op):
        return "allow", "github read"
    return _write(g, "github %s on %s" % (op, ",".join(sorted(refs))))


def _decide_bash(ti):
    cmd = ti.get("command")
    if set(ti) - {"command", "description", "timeout"} or not isinstance(cmd, str) or SHELL_META.search(cmd) or len(cmd) > 400:
        return "deny", "bash input not a plain read-only command"
    t = cmd.split()
    if t[:1] == ["git"] and len(t) >= 2 and t[1] in GIT_CMDS:
        sub, rest, isgit = t[1], t[2:], True
    elif t[:1] == ["gh"] and len(t) >= 3 and (t[1],) in GH_CMDS and t[2] in GH_CMDS[(t[1],)]:
        sub, rest, isgit = t[2], t[3:], False
    else:
        return "deny", "bash command not on the read-only list (GitHub changes go through the GitHub tool with approval)"
    prev = None
    for tok in rest:
        if tok.startswith("-"):
            if tok not in FLAGS:
                return "deny", "bash flag not allowed: " + tok[:20]
        elif prev in ("-R", "--repo"):
            if tok.lower() not in {r.lower() for r in GITHUB_REPOS}:
                return "deny", "bash repo not allowed"
        elif prev in ("--limit", "-L", "-n"):
            if not NUM.fullmatch(tok):
                return "deny", "bash number expected"
        elif prev in ("--state", "-s", "--json"):
            if not POS.fullmatch(tok):
                return "deny", "bash argument not allowed"
        elif isgit:
            if sub == "branch":
                return "deny", "git branch takes no names (that would create one)"
            parts = tok.split(":")
            if len(parts) > 2 or not all(PATHTOK.fullmatch(p) for p in parts if p != "") or tok.startswith("/") or ".." in tok or any(SECRET_PATH.search(p) for p in parts[1:]) or (len(parts) == 1 and ("/" in tok or "." in tok) and SECRET_PATH.search(tok)):
                return "deny", "git argument not allowed"
        else:                                                       # gh: a PR/issue/run number only; never a URL, user:branch or other repo
            if not NUM.fullmatch(tok):
                return "deny", "gh takes a number only (use -R for the repo)"
        prev = tok
    if not isgit and t[1] == "repo" and not any(x in ("-R", "--repo") for x in rest):
        return "deny", "gh repo view needs -R with an allowed repo"
    return "allow", "read-only gh/git command"


def _read_ok(ti, home):
    p = ti.get("file_path")
    if set(ti) - {"file_path", "offset", "limit"} or not isinstance(p, str) or not p or any(not isinstance(ti.get(k, 0), int) or isinstance(ti.get(k), bool) for k in ("offset", "limit")):
        return False
    real = os.path.realpath(os.path.expanduser(p))
    roots = [os.path.realpath(os.path.join(home, r)) for r in READ_ROOTS]
    inside = any(real == r or real.startswith(r + os.sep) for r in roots)
    rel = real[len(os.path.realpath(home)):]
    return inside and os.path.isfile(real) and os.path.splitext(real)[1].lower() in READ_EXT and not SECRET_PATH.search(rel)


def _decide_send(ti, stopped):
    t = ti.get("text")
    if not (set(ti) <= BUZZ_SEND_KEYS and {"audience", "final", "text"} <= set(ti) and ti["audience"] == "everyone" and isinstance(ti["final"], bool) and isinstance(t, str)):
        return "deny", "send input not allowed"
    if stopped:
        return ("allow", "stopped notice") if set(ti) == {"audience", "final", "text"} and t in STOP_TEXT else ("deny", "STOP is on")
    if not t.strip() or len(t) > SEND_MAX:
        return "deny", "send text empty or too long"
    for k in ("channel", "thread", "reply_to", "recipient"):
        if k in ti and not (isinstance(ti[k], str) and IDENT.fullmatch(ti[k])):
            return "deny", "send %s must be a plain identifier" % k
    if "mention" in ti and not (isinstance(ti["mention"], list) and len(ti["mention"]) <= 20 and all(isinstance(m, str) and IDENT.fullmatch(m) for m in ti["mention"])):
        return "deny", "send mention must be a short list of plain identifiers"
    if _secret(ti):
        return "deny", "secret-shaped text in a message"
    return "allow", "buzz send"


def decide(d, g, home):
    """-> (decision, reason). decision in allow, ask, deny."""
    tool, ti = d.get("tool_name"), d.get("tool_input")
    if d.get("hook_event_name") != "PreToolUse" or not isinstance(tool, str) or not isinstance(ti, dict):
        return "deny", "bad request"
    stopped = _stopped(g)
    if tool == "mcp__buzz__send":
        return _decide_send(ti, stopped)
    if stopped:
        return "deny", "STOP is on"
    if tool == "Read":
        return ("allow", "read in allowed folder") if _read_ok(ti, home) else ("deny", "read outside allowed folders, a secret-shaped path or a bad argument")
    if _secret(ti):
        return "deny", "secret-shaped text in a tool input"
    if tool in BUZZ_READ:
        return "allow", "buzz read"
    if tool == "Bash":
        return _decide_bash(ti)
    m = ATL.fullmatch(tool)
    if m:
        return _decide_atlassian(m.group(1), ti, g)
    m = GH.fullmatch(tool)
    if m:
        return _decide_github(m.group(1), ti, g)
    return "deny", "tool not on allowlist"


def main():
    g = os.path.dirname(os.path.realpath(__file__))
    home = os.path.expanduser("~")
    d = {}
    try:
        d = json.loads(sys.stdin.read())
        dec, why = decide(d, g, home)
    except Exception as e:
        dec, why = "deny", "unparseable (%s)" % type(e).__name__
    try:
        ti = d.get("tool_input") if isinstance(d, dict) and isinstance(d.get("tool_input"), dict) else {}
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": d.get("tool_name") if isinstance(d, dict) else None, "decision": dec, "why": why,
               "mode": d.get("permission_mode") if isinstance(d, dict) else None, "session": d.get("session_id") if isinstance(d, dict) else None,
               "input_sha": hashlib.sha256(json.dumps(ti, sort_keys=True, default=str).encode()).hexdigest()[:16]}
        with open(os.path.join(g, "hook.log"), "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except Exception:
        pass
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": dec, "permissionDecisionReason": "LIFE-333 Claude Buzz policy: " + why}}))


if __name__ == "__main__":
    main()
