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
JIRA_SITE = "https://jimmarkunas.atlassian.net"                                     # the site hostname is not secret (it is in the docs and runbooks): a constant, so no GitHub value is read
FIXED_ACCOUNTS = {("jira", "personal"): "jira-personal", ("jira", "bytalos"): "jira-bytalos"}   # Jira is not a credential Jim names: these scope labels are committed, not read from the environment
DEFAULT_LABELS = {("calendar", "personal", 1): "personal calendar", ("calendar", "bytalos", 1): "bytalos calendar", ("gmail", "personal", 1): "personal inbox", ("gmail", "bytalos", 1): "bytalos inbox",
                  ("todoist", "personal", 1): "personal todoist", ("notion", "personal", 1): "personal notion", ("interview_os", "personal", 1): "interview os",
                  ("outlook", "personal", 1): "outlook.com mailbox", ("outlook", "personal", 2): "school mailbox", ("outlook", "personal", 3): "greatestpmever mailbox"}   # not secrets and never an address: committed, so Jim creates no label item; an environment label still overrides
# The credential inventory row (lifeos/platform/credential_list.py) whose expiry each credential follows; a credential with no row has no grant, so no read.
INVENTORY = {"JIRA_API_TOKEN": "jira_api_token", "NOTION_API_TOKEN": "notion_api_token", "GCAL_SERVICE_ACCOUNT_JSON": "gcal_service_account", "GMAIL_OAUTH_REFRESH_TOKEN": "gmail_oauth_refresh_token",
             "FOLLOWUP_GMAIL_BYTALOS_READONLY_REFRESH": "followup_gmail_bytalos_refresh", "FOLLOWUP_TODOIST_PERSONAL_TOKEN": "followup_todoist_personal_token"}
OUTLOOK_INVENTORY = "outlook_sign_in"                                               # an Outlook credential is a label (followup-personal-N) whose token lives in the private store
NO_EXPIRY = "2099-12-31"                                                          # an inventory row with no fixed expiry: a long date, withdrawn by removing the account or the name
EXPIRES = {}                                                                      # {(source, workspace, slot): "YYYY-MM-DD"}: only for a credential with no inventory row
TGP_DOMAINS = stage.TGP_DOMAINS


class WiringError(RuntimeError):
    """`str(error)` is a fixed code."""


def _fetchers():
    """The fetchers module (LIFE-757), or None if it cannot be imported: then no source has a credential and every source shows DEGRADED, never a crash."""
    try:
        from lifeos.platform import followup_fetchers
    except ImportError:
        return None
    return followup_fetchers


def _keys(module, source, workspace):
    return sorted((k for k in getattr(module, "CREDENTIAL_NAMES", {}) if k[:2] == (source, workspace)), key=lambda k: k[2]) if module else []


def account_env(source, workspace, slot=1):
    """The environment name that holds this scope's account LABEL (the fetchers' ACCOUNT_NAMES)."""
    return (getattr(_fetchers(), "ACCOUNT_NAMES", {}) or {}).get((source, workspace, slot or 1), "")


def credential_names():
    """{(source, workspace, slot): the NAME a grant carries}: the fetchers' GRANT_REFS, so a grant can only point its own fetcher at its own credential."""
    return dict(getattr(_fetchers(), "GRANT_REFS", {}) or {})


def _default_label(environ, key):
    """The committed label, but only where the credential is there to read (a stored Outlook token is judged by the reader, which shows DEGRADED until the sign-in is done): no credential, no scope."""
    fetchers = _fetchers()
    name = (getattr(fetchers, "CREDENTIAL_NAMES", {}) or {}).get(key)
    stored = bool(fetchers) and key[0] in fetchers.STORED_TOKENS
    return DEFAULT_LABELS.get(key, "") if name and ((environ.get("OUTLOOK_CLIENT_ID") if stored else environ.get(name)) or "").strip() else ""


def accounts(environ, source, workspace):
    """-> [(slot, label)]: Jira has its committed label; the others are the labels named in the environment, in slot order. A source that does not serve the workspace has none."""
    if workspace not in RELEASED or workspace not in SOURCE_WORKSPACES.get(source, ()):
        return []
    if (source, workspace) in FIXED_ACCOUNTS:
        return [(1, FIXED_ACCOUNTS[(source, workspace)])]
    out = []
    for key in _keys(_fetchers(), source, workspace):
        label = ((environ.get(account_env(*key)) or "").strip() or _default_label(environ, key))[:80]
        if label and not stage.tgp_account(label) and all(label != seen for _, seen in out):
            out.append((key[2], label))
    return out


def configured(environ):
    """-> {(workspace, label): [(source, slot), ...]}: one scope per workspace and account label. Sources the workspace serves but that have no account are added to the workspace's first scope so
    they show DEGRADED (not provisioned) instead of vanishing from the payload."""
    scopes = {}
    for workspace in RELEASED:
        for source in SOURCES:
            for slot, label in accounts(environ, source, workspace):
                scopes.setdefault((workspace, label), []).append((source, slot))
    for workspace in RELEASED:
        mine = sorted(key for key in scopes if key[0] == workspace)
        if not mine:
            continue
        covered = {s for key in mine for s, _ in scopes[key]}
        scopes[mine[0]] += [(s, None) for s in SOURCES if workspace in SOURCE_WORKSPACES[s] and s not in covered]
    return scopes


def expiry_table():
    """EXPIRES plus each credential's date from its inventory row (a row with no fixed expiry follows NO_EXPIRY)."""
    from lifeos.platform.credential_list import CREDENTIALS                       # noqa: PLC0415
    rows = {row.get("key"): row for row in CREDENTIALS}
    table = {}
    for key, raw in (getattr(_fetchers(), "CREDENTIAL_NAMES", {}) or {}).items():
        row = rows.get(OUTLOOK_INVENTORY if raw.startswith("followup-") else INVENTORY.get(raw))
        if row:
            table[key] = row.get("expires") or NO_EXPIRY
    table.update(EXPIRES)
    return table


def grants_for(environ, names, expires=None):
    """One Grant per configured (source, workspace, account) that has a credential NAME and an expiry; Asana, TGP and a source outside its workspaces never get one. Each scope has its own Grant even
    where two scopes share a credential item."""
    expires = expiry_table() if expires is None else expires
    out = []
    for (workspace, label), members in sorted(configured(environ).items()):
        for source, slot in sorted(members, key=lambda m: (m[0], m[1] or 0)):
            key = (source, workspace, slot or 1)
            name, day = names.get(key), expires.get(key)
            if not name or not day or (slot, label) not in accounts(environ, source, workspace):      # a source appended only to show DEGRADED has no account here: no grant
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
    """The environment the fetchers see: the caller's, plus the Jira site (a constant, never a GitHub value) unless the caller set one. The Jira project split is fixed in the fetchers' code."""
    out = dict(environ)
    out.setdefault("JIRA_BASE_URL", JIRA_SITE)
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
                        jira_base=JIRA_SITE)


__all__ = ["EXPIRES", "WiringError", "account_env", "credential_names", "accounts", "configured", "grants_for", "receipt_id",
           "make_audit", "DryStore", "make_sink", "payload_loader", "fetcher_environment", "build"]
