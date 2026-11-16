"""LIFE-736: a read-only resolver over the Agent Directory catalog (Notion): one exact lookup, a redacted snapshot, and an honest status for every row. No network."""

# A row grants nothing and is never proof an agent is online. load() checks the access policy (service notion, read) before calling the caller's fetch(); resolve() returns ONE entry by exact
# Agent ID or exact unique alias for one workspace (never a list, wildcard or broadcast) and re-checks the grant each call; snapshot() is the redacted machine-readable projection.
# Fails closed with a fixed reason word: Notion unavailable, duplicate ID, bad record, no workspace, secret-looking value (nsec, private key, token), stale data, partial identity, ambiguous alias, TGP.
# routable-verified needs a row claim AND a fresh same-host Proof from a live check handed in by the caller; a row, Candidate, surface or persona alone never qualifies. Nothing is cached.
# Live discovery of the Mac, Oracle and Buzz is a separate approved step: docs/platform/agent-directory-discovery.md.
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lifeos.platform import access_policy

CENTRAL = ZoneInfo("America/Chicago")
MAX_AGE = timedelta(days=7)                       # a directory row last checked longer ago than this is stale
PROOF_TTL = timedelta(hours=1)                    # a routing proof is a live observation: it expires quickly
STATUSES = ("manual-only", "configured-untested", "not-reachable", "routable-verified")
KINDS = {"Client Surface": "client_surface", "Community Identity": "agent_identity", "Bot Profile": "agent_identity", "Agent Session": "agent_session", "Persona": "persona"}
PLATFORMS = ("Claude", "ChatGPT", "Buzz", "Orca", "Hermes")
SPACES = ("Personal", "Bytalos", "TGP", "Unknown")
GRANTABLE_SPACES = ("Personal", "Bytalos")        # TGP is never inferred or resolved from a label: it needs its own isolated grant that this module does not model
AGENT_ID = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
FULL_NPUB = re.compile(r"npub1[02-9ac-hj-np-z]{58}")
SECRET = re.compile(r"nsec1[02-9ac-hj-np-z]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|bws?_[A-Za-z0-9]{20,})\b|(?i:(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,})")
TEXT_FIELDS = ("Agent", "Identity Alias", "Invocation Route", "Permission Boundary", "Capabilities", "Related Identity", "Notes", "Source Reference")


class DirectoryError(ValueError):
    """`reason` is a fixed word; no message from Notion or from a row is ever kept."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Entry:
    agent_id: str
    name: str
    kind: str                     # agent_identity | client_surface | agent_session | persona
    record_type: str
    platform: str
    host: str
    interface: str
    spaces: tuple
    evidence: str
    discovery_state: str
    reachability: str
    alias: str
    route: str
    boundary: str
    last_checked: datetime


@dataclass(frozen=True)
class Directory:
    entries: tuple                # sorted by agent_id
    source: str
    read_at: datetime
    principal: str                # the 64-hex principal that read it (kept only as a short fingerprint in a snapshot)


@dataclass(frozen=True)
class Proof:
    """A receipt from an actual live check of one agent: who, where, which session, when. Handed in by the caller; the directory itself can never supply one."""
    agent_id: str
    host: str
    session_id: str
    verified_at: datetime
    receipt: str


@dataclass(frozen=True)
class Resolution:
    decision: str                 # allow | deny   ("allow" means only: this one catalog entry may be shown; it authorizes no send, run or write)
    reason: str
    entry: object = None
    status: str = "not-reachable"


def _text(row, field):
    value = row.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DirectoryError("bad_row")
    return value.strip()


def _when(text):
    try:
        if len(text) == 10:
            return datetime.combine(date.fromisoformat(text), datetime.min.time(), tzinfo=CENTRAL)      # a date-only "last checked" counts from the start of that Central day
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise DirectoryError("bad_last_checked") from None
    return when if when.tzinfo else when.replace(tzinfo=CENTRAL)


def _spaces(value):
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else []
        except ValueError:
            raise DirectoryError("bad_row") from None
    if not isinstance(value, (list, tuple)) or not all(isinstance(s, str) for s in value):
        raise DirectoryError("bad_row")
    if not value:
        raise DirectoryError("missing_workspace")
    if any(s not in SPACES for s in value):
        raise DirectoryError("bad_workspace")
    return tuple(sorted(set(value)))


def _entry(row):
    if not isinstance(row, dict):
        raise DirectoryError("bad_row")
    for field in TEXT_FIELDS:
        if SECRET.search(_text(row, field)):
            raise DirectoryError("secret_material")                                       # refuse the whole directory; the value is never echoed
    agent_id = _text(row, "Agent ID")
    if not AGENT_ID.match(agent_id):
        raise DirectoryError("bad_agent_id")
    record_type, platform = _text(row, "Record Type"), _text(row, "Platform")
    if record_type not in KINDS or platform not in PLATFORMS:
        raise DirectoryError("bad_record")
    last = _text(row, "date:Last Checked:start") or _text(row, "Last Checked")
    if not last:
        raise DirectoryError("bad_last_checked")
    alias = _text(row, "Identity Alias")
    return Entry(agent_id, _text(row, "Agent"), KINDS[record_type], record_type, platform, _text(row, "Host") or "Unknown", _text(row, "Interface"), _spaces(row.get("Space")), _text(row, "Evidence") or "Unverified",
                 _text(row, "Discovery State") or "Candidate", _text(row, "Reachability") or "Unknown", FULL_NPUB.sub(lambda m: "npub…" + m.group(0)[-3:], alias), _text(row, "Invocation Route"), _text(row, "Permission Boundary"), _when(last))


def build(rows, source, now, principal=""):
    """Rows (native dicts) -> Directory. Raises DirectoryError on the first problem; one bad or duplicate row refuses the whole directory (a half-trusted catalog is worse than none)."""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise DirectoryError("empty_directory")
    entries = [_entry(row) for row in rows]
    ids = [e.agent_id for e in entries]
    if len(set(ids)) != len(ids):
        raise DirectoryError("duplicate_agent_id")
    return Directory(tuple(sorted(entries, key=lambda e: e.agent_id)), source, now, principal)


def load(fetch, policy, principal, now, source="notion:life-os-agent-directory"):
    """Read the directory once. `policy` is access_policy.load(...) (None when unreadable), `principal` the 64-hex Buzz key of the reader. A deny or a failed fetch raises; nothing is read before the grant."""
    decision, _ = access_policy.decide(policy, principal, "notion", "read")
    if decision != "allow":
        raise DirectoryError("grant_denied")
    try:
        rows = fetch()
    except Exception:                                                                      # noqa: BLE001 - the type and text of a connector failure are never kept
        raise DirectoryError("directory_unavailable") from None
    return build(rows, source, now, principal)


def fetch_unavailable():
    """The default reader: no Notion read is wired in until Jim approves the exact grant (docs/platform/agent-directory-discovery.md, section B)."""
    raise DirectoryError("directory_unavailable")


def is_stale(entry, now, max_age=MAX_AGE):
    return now - entry.last_checked > max_age or entry.last_checked > now + timedelta(minutes=5)


def status_of(entry, now, proof=None, max_age=MAX_AGE):
    """-> (status, reason). Honest by construction: a catalog row never reaches routable-verified on its own."""
    if entry.discovery_state == "Retired":
        return "not-reachable", "retired"
    if is_stale(entry, now, max_age):
        return "not-reachable", "stale_data"
    if entry.kind in ("client_surface", "persona") or entry.reachability == "Manual Only":
        return "manual-only", "no_programmatic_route"
    if entry.discovery_state == "Candidate":
        return "not-reachable", "candidate_identity_unresolved"
    if entry.reachability == "Verified" and entry.discovery_state == "Routable Verified":
        if "[partial]" in entry.alias or "…" in entry.alias:
            return "configured-untested", "identity_partial"                           # a truncated public key names no one: it can never be routed to
        problem = _proof_problem(entry, proof, now)
        return ("routable-verified", "proof_ok") if problem is None else ("configured-untested", problem)
    if entry.reachability == "Configured Untested":
        return "configured-untested", "configured_not_tested"
    if entry.reachability == "Verified":
        return "configured-untested", "row_claim_without_state"
    return "not-reachable", "reachability_unknown"


def _proof_problem(entry, proof, now):
    if not isinstance(proof, Proof) or proof.agent_id != entry.agent_id:
        return "row_claim_without_proof"
    if proof.host != entry.host or entry.host == "Unknown":
        return "proof_wrong_host"
    if not proof.session_id or not proof.receipt or SECRET.search(proof.receipt + proof.session_id):
        return "proof_incomplete"
    if now - proof.verified_at > PROOF_TTL or proof.verified_at > now + timedelta(minutes=5):
        return "proof_expired"
    return None


def resolve(directory, now, policy, principal, agent_id=None, alias=None, workspace=None, proofs=(), max_age=MAX_AGE):
    """Look up ONE entry. Exactly one of agent_id / alias; a workspace is required and must be one of the entry's own (Personal or Bytalos). Revocation is re-checked on every call."""
    decision, _ = access_policy.decide(policy, principal, "notion", "read")
    if decision != "allow":
        return Resolution("deny", "grant_denied")
    if not isinstance(directory, Directory) or (agent_id is None) == (alias is None):
        return Resolution("deny", "bad_request")
    key = agent_id if agent_id is not None else alias
    if not isinstance(key, str) or not key.strip():
        return Resolution("deny", "bad_request")
    if re.search(r"[*?,;|]", key) or key.strip().lower() in ("all", "everyone", "any", "@all", "@everyone"):
        return Resolution("deny", "broadcast_refused")
    if workspace not in SPACES:
        return Resolution("deny", "missing_workspace")
    if workspace not in GRANTABLE_SPACES:
        return Resolution("deny", "workspace_not_granted")                                # TGP and Unknown never resolve from a label
    if agent_id is not None:
        found = [e for e in directory.entries if e.agent_id == agent_id.strip()]
    else:
        wanted = _fold(alias)
        found = [e for e in directory.entries if _fold(e.alias) == wanted]
    if not found:
        return Resolution("deny", "unknown_agent")
    if len(found) > 1:
        return Resolution("deny", "ambiguous_alias")
    entry = found[0]
    if workspace not in entry.spaces:
        return Resolution("deny", "cross_space_denied")
    proof = next((p for p in proofs if isinstance(p, Proof) and p.agent_id == entry.agent_id), None)
    status, reason = status_of(entry, now, proof, max_age)
    return Resolution("allow", reason, entry, status)


def _fold(text):
    return re.sub(r"\s+", " ", text.strip().lower())


def snapshot(directory, now, proofs=(), max_age=MAX_AGE):
    """The redacted machine-readable projection: deterministic order, fixed keys, provenance and last-seen. It is a catalog view; `authorizes` is always empty."""
    out = []
    for entry in directory.entries:
        proof = next((p for p in proofs if isinstance(p, Proof) and p.agent_id == entry.agent_id), None)
        status, reason = status_of(entry, now, proof, max_age)
        out.append({"agent_id": entry.agent_id, "kind": entry.kind, "record_type": entry.record_type, "platform": entry.platform, "host": entry.host, "interface": entry.interface,
                    "spaces": list(entry.spaces), "alias": entry.alias, "evidence": entry.evidence, "discovery_state": entry.discovery_state, "reachability_claimed": entry.reachability,
                    "status": status, "reason": reason, "last_checked": entry.last_checked.isoformat(), "stale": is_stale(entry, now, max_age)})
    return {"version": 1, "source": directory.source, "read_at": directory.read_at.isoformat(), "snapshot_at": now.isoformat(), "max_age_days": max_age.days,
            "principal": directory.principal[:8], "count": len(out), "authorizes": [], "note": "catalog only: a listed agent is not online, reachable or permitted; verify the session and grants before any routing", "entries": out}


def hermes_profiles(allowlist_text):
    """The profile names in hermes/connector-allowlists.txt (repo evidence for the Hermes rows): one Oracle runtime, several profiles."""
    names = []
    for line in allowlist_text.splitlines():
        line = line.split("#", 1)[0]
        if ":" in line:
            names.append(line.split(":", 1)[0].strip())
    return tuple(n for n in names if n)


def check_hermes(directory, allowlist_text):
    """-> sorted Agent IDs of Hermes bot-profile rows whose alias is NOT a profile in the repo allowlist table (a row with no repo backing), plus profiles the table has but the directory lacks, as ('missing', name)."""
    profiles = set(hermes_profiles(allowlist_text))
    rows = [e for e in directory.entries if e.platform == "Hermes" and e.record_type == "Bot Profile"]
    extra = sorted(e.agent_id for e in rows if e.alias not in profiles)
    missing = sorted(("missing", p) for p in profiles if p not in {e.alias for e in rows})
    return extra + missing


def now_central():
    return datetime.now(timezone.utc).astimezone(CENTRAL)
