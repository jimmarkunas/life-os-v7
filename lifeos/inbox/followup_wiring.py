"""LIFE-757: the production wiring of the followup stage (store, scopes, grants, fetchers, receipts, payload sink); off unless FOLLOWUP_WIRING is on."""
# `build(environ, live)` returns the followup_stage.Wiring, or None while the switch is off (the stage is then a counts-only no-op). Jim, Nov 16 2026: every hourly tick is one full reconciliation of
# every approved source; a complete build, all six sources, new credentials as needed. Nothing here holds a credential VALUE or an account identifier: a grant names a credential NAME that
# `followup_fetchers` resolves from the job's environment, an account LABEL comes from the environment (FOLLOWUP_ACCOUNT_<SOURCE>_<WORKSPACE>[_N]), and the committed expiry table says when a credential
# stops being a valid grant (no expiry row, no grant: that source shows DEGRADED). Which source serves which workspace is committed below (Jim's account map, Nov 16 2026).
# A scope is one (workspace, account label): Jira is one Atlassian account split by project key into a Personal and a Bytalos scope (two credential names), Outlook is up to three Personal accounts
# (one scope each), Todoist, Notion and Interview OS are Personal only. TGP is excluded everywhere: no TGP workspace, and an account label naming the TGP domain is refused, not read.
# Receipts: every read is written to `v7_inbox_access` first (LIFE-707/712); if a receipt cannot be written nothing is read. The readers' request ids repeat across hourly runs, so the receipt id is the
# digest of time, source, workspace, account and the reader's id. Dry run: store reads fall back to empty when the Followup tables do not exist yet; nothing is ever written.
from datetime import datetime, timezone

from lifeos.inbox import followup_stage as stage
from lifeos.inbox.followup_store import FollowupStore, FollowupStoreError, SOURCES
from lifeos.inbox.ledger import Ledger, digest
from lifeos.inbox.model import UNRELEASED_WORKSPACES, WORKSPACES
from lifeos.platform import db, switches
from lifeos.platform import followup_readers as readers

SWITCH = "FOLLOWUP_WIRING"
PRINCIPAL = "followup-stage"
RELEASED = tuple(w for w in WORKSPACES if w not in UNRELEASED_WORKSPACES)
BOTH = ("personal", "bytalos")
SOURCE_WORKSPACES = {"jira": BOTH, "calendar": BOTH, "gmail": BOTH, "outlook": ("personal",), "todoist": ("personal",), "notion": ("personal",), "interview_os": ("personal",)}
MAX_ACCOUNTS = {("outlook", "personal"): 3}                                       # every other (source, workspace) has one account
FIXED_ACCOUNTS = {("jira", "personal"): "jira-personal", ("jira", "bytalos"): "jira-bytalos"}   # Jira is not a credential Jim names: these scope labels are committed, not read from the environment
JIRA_PROJECTS = {"personal": ("JFM", "LIFE", "LOS"), "bytalos": ("BAD", "BPP")}   # one Atlassian account, split by project key; passed to the fetchers as FOLLOWUP_JIRA_PROJECTS_<WORKSPACE>
# Jim, Nov 16 2026: reuse an existing credential wherever one exists. (source, workspace[, n]) -> (credential NAME, key of its row in the credential inventory, or None for a new item). Names only.
REUSED = {**{("jira", w): ("JIRA_API_TOKEN", "jira_api_token") for w in ("personal", "bytalos")},                    # with JIRA_EMAIL, loaded beside it
          **{("outlook", "personal", n): ("OUTLOOK_CLIENT_ID", "outlook_sign_in") for n in (1, 2, 3)},             # the fetcher reads the tokens from v7_outlook_token by label followup-personal-1..3
          ("calendar", "personal"): ("GCAL_SERVICE_ACCOUNT_JSON", "gcal_service_account"), ("calendar", "bytalos"): ("GCAL_SERVICE_ACCOUNT_JSON", "gcal_service_account"),
          ("gmail", "personal"): ("GMAIL_OAUTH_REFRESH_TOKEN", "gmail_oauth_refresh_token"),                       # with GMAIL_OAUTH_CLIENT_ID and _SECRET, loaded beside it
          ("notion", "personal"): ("NOTION_API_TOKEN", "notion_api_token"), ("interview_os", "personal"): ("NOTION_API_TOKEN", "notion_api_token"),
          ("gmail", "bytalos"): ("FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH", None),                                  # new: no item yet, so no expiry row and no grant (degraded) until it exists
          ("todoist", "personal"): ("FOLLOWUP_TODOIST_PERSONAL_TOKEN", None)}                                       # new, same
NO_EXPIRY = "2099-12-31"                                                          # a credential the inventory says has no fixed expiry: a long date, withdrawn by removing the account or the name
EXPIRES = {}                                                                      # {(source, workspace) or (source, workspace, n): "YYYY-MM-DD"}: filled when a credential is created
TGP_DOMAINS = stage.TGP_DOMAINS


class WiringError(RuntimeError):
    """`str(error)` is a fixed code."""


def account_env(source, workspace, n=None):
    return f"FOLLOWUP_ACCOUNT_{source.upper()}_{workspace.upper()}" + (f"_{n}" if n else "")


def _fetchers():
    """The sibling module (LIFE-757 fetchers), or None until it is merged: then no source has a credential name and every source shows DEGRADED, never a crash."""
    try:
        from lifeos.platform import followup_fetchers
    except ImportError:
        return None
    return followup_fetchers


def credential_names():
    """The fetchers' names, overlaid by the committed reuse table (it wins): which existing or new credential NAME each (source, workspace[, n]) is granted."""
    module = _fetchers()
    names = dict(getattr(module, "CREDENTIAL_NAMES", {}) or {}) if module else {}
    names.update({key: name for key, (name, _) in REUSED.items()})
    return names


def _lookup(table, source, workspace, n):
    """An account-specific row (source, workspace, n) first, then the (source, workspace) row."""
    return table.get((source, workspace, n)) or table.get((source, workspace))


def accounts(environ, source, workspace):
    """-> [(n, label)]: the labels named in the environment for this (source, workspace), in order; n is None for the single unnumbered one. A source that does not serve the workspace has none."""
    if workspace not in RELEASED or workspace not in SOURCE_WORKSPACES.get(source, ()):
        return []
    if (source, workspace) in FIXED_ACCOUNTS:
        return [(None, FIXED_ACCOUNTS[(source, workspace)])]
    limit = MAX_ACCOUNTS.get((source, workspace), 1)
    out = []
    for n in (None, *range(1, limit + 1)):
        label = (environ.get(account_env(source, workspace, n)) or "").strip()[:80]
        if label and not stage.tgp_account(label) and all(label != seen for _, seen in out):
            out.append((n, label))
        if len(out) == limit:
            break
    return out


def configured(environ):
    """-> {(workspace, label): [(source, n), ...]}: one scope per workspace and account label. Sources the workspace serves but that have no account are added to the workspace's first scope so they
    show DEGRADED (not provisioned) instead of vanishing from the payload."""
    scopes = {}
    for workspace in RELEASED:
        for source in SOURCES:
            for n, label in accounts(environ, source, workspace):
                scopes.setdefault((workspace, label), []).append((source, n))
    for workspace in RELEASED:
        mine = sorted(key for key in scopes if key[0] == workspace)
        if not mine:
            continue
        covered = {s for key in mine for s, _ in scopes[key]}
        scopes[mine[0]] += [(s, None) for s in SOURCES if workspace in SOURCE_WORKSPACES[s] and s not in covered]
    return scopes


def expiry_table():
    """EXPIRES plus the date of each reused credential from its existing inventory row (no new row to maintain); an inventory row with no fixed expiry gets NO_EXPIRY. A new credential has none."""
    from lifeos.platform.credential_list import CREDENTIALS                       # noqa: PLC0415
    rows = {row.get("key"): row for row in CREDENTIALS}
    table = {}
    for key, (_, inventory) in REUSED.items():
        if inventory in rows:
            table[key] = rows[inventory].get("expires") or NO_EXPIRY
    table.update(EXPIRES)
    return table


def grants_for(environ, names, expires=None):
    """One Grant per configured (source, workspace, account) that has a credential NAME and an expiry row; Asana, TGP and a source outside its workspaces never get one."""
    expires = expiry_table() if expires is None else expires
    out = []
    for (workspace, label), members in sorted(configured(environ).items()):
        for source, n in sorted(members, key=lambda m: (m[0], m[1] or 0)):
            name, day = _lookup(names, source, workspace, n), _lookup(expires, source, workspace, n)
            if not name or not day or (n, label) not in accounts(environ, source, workspace):      # a source appended only to show DEGRADED has no account here: no grant
                continue
            out.append(readers.Grant(source, workspace, label, name, datetime.fromisoformat(day + "T23:59:59+00:00")))
    return tuple(out)


def receipt_id(receipt):
    return digest("|".join(str(receipt.get(k, "")) for k in ("t", "source", "tenant", "account", "request_id")))


def make_audit(ledger):
    """audit(receipt) for the readers: allow and deny open a receipt, ok and degraded settle it. Raises WiringError when the ledger cannot write (the readers then read nothing)."""
    def audit(receipt):
        request = receipt_id(receipt)
        try:
            if receipt["outcome"] in ("allow", "deny"):
                now = datetime.fromisoformat(receipt["t"])
                ledger.record_access({"request_id": request, "principal_id": PRINCIPAL, "source": receipt["source"], "workspace": receipt["tenant"], "account": receipt["account"][:80],
                                      "decision": receipt["outcome"], "reason": str(receipt["reason"])[:40]}, now)
            else:
                ledger.settle_access(request, str(receipt["outcome"])[:24])
        except Exception:                                                          # noqa: BLE001 - only a fixed code leaves; the driver's text never does
            raise WiringError("FOLLOWUP_AUDIT_UNAVAILABLE") from None
    return audit


class DryStore:
    """Reads fall back to empty when the Followup tables are not there yet; every write refuses. Used for a dry run, which never writes."""

    def __init__(self, store):
        self._store = store

    def cursor_for(self, workspace, account, source):
        try:
            return self._store.cursor_for(workspace, account, source)
        except FollowupStoreError:
            return None

    def threads(self, workspace, account):
        try:
            return self._store.threads(workspace, account)
        except FollowupStoreError:
            return []

    def __getattr__(self, name):
        raise WiringError("FOLLOWUP_DRY_RUN_NO_WRITE")


def make_sink(store, clock):
    """sink(payload): the one place the payload is published, once per scope, live only. A store without the payload table refuses with a fixed code."""
    def sink(payload):
        save = getattr(store, "save_payload", None)
        if save is None:
            raise WiringError("FOLLOWUP_PAYLOAD_STORE_MISSING")
        save(payload["workspace"], payload["account"], payload, clock())
    return sink


def payload_loader(store, workspace, account):
    """The `load(credential_ref)` that `followup.payload_reader` wants: this scope's last published payload, or None (the reader then says unavailable)."""
    def load(_credential_ref):
        read = getattr(store, "load_payload", None)
        return read(workspace, account) if read else None
    return load


def fetcher_environment(environ):
    """The environment the fetchers see: the caller's, plus each Jira scope's committed project allowlist as FOLLOWUP_JIRA_PROJECTS_<WORKSPACE>. A caller value can only narrow it: a key
    outside the workspace's committed list (a Bytalos scope asking for a Personal project) is dropped, and an empty result falls back to the committed list."""
    out = dict(environ)
    for workspace, projects in JIRA_PROJECTS.items():
        name = f"FOLLOWUP_JIRA_PROJECTS_{workspace.upper()}"
        asked = [p.strip().upper() for p in (environ.get(name) or "").split(",") if p.strip()]
        out[name] = ",".join(p for p in asked if p in projects) or ",".join(projects)
    return out


def build(environ, live=False, clock=None):
    """-> the Wiring, or None while the switch is off or no source has a configured account."""
    if not switches.state(SWITCH, environ):
        return None
    scopes = configured(environ)
    if not scopes:
        return None
    clock = clock or (lambda: datetime.now(timezone.utc))
    real = FollowupStore(db.connect)
    store = real if live else DryStore(real)
    module = _fetchers()
    fetchers = module.build_fetchers(fetcher_environment(environ)) if module and hasattr(module, "build_fetchers") else {}
    made = tuple(stage.Scope(workspace, label, tuple(sorted({s for s, _ in members}))) for (workspace, label), members in sorted(scopes.items()))
    return stage.Wiring(store=store, scopes=made, grants=grants_for(environ, credential_names()), fetchers=fetchers, audit=make_audit(Ledger(db.connect)), sink=make_sink(real, clock), clock=clock,
                        jira_base=(environ.get("JIRA_BASE_URL") or "").rstrip("/"))


__all__ = ["EXPIRES", "WiringError", "account_env", "credential_names", "accounts", "configured", "grants_for", "receipt_id",
           "make_audit", "DryStore", "make_sink", "payload_loader", "fetcher_environment", "build"]
