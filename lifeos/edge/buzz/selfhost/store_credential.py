"""LIFE-870: Oracle-only private Buzz pilot credential writer.
Run as the Oracle ubuntu user with that user's Bitwarden machine token already
loaded from the existing private env file; never from an agent's environment.
No secret is printed, passed to subprocess argv, or stored on disk here.

We use Bitwarden's official SDK for *secret creation*. The bws CLI's current
'secret create NAME VALUE PROJECT' form exposes VALUE in /proc/pid/cmdline.
The bws executable is used only to delete this program's synthetic write probe
by ID (the ID is not a secret). Never delete a pre-existing secret.
"""
import os
import platform
import pwd
import resource
import secrets
import subprocess
import sys
from dataclasses import dataclass

OWNER = "BUZZ_SELFHOST_OWNER_KEY"
RELAY = "BUZZ_SELFHOST_RELAY_KEY"
NAMES = (OWNER, RELAY)


def _obj(v):
    return v.get("data", v) if isinstance(v, dict) else getattr(v, "data", v)


def _attr(v, name):
    return v.get(name) if isinstance(v, dict) else getattr(v, name, None)


@dataclass(frozen=True)
class Verdict:
    name: str
    status: str


def _project_visible(client, project_id):
    info = _obj(client.projects().get(project_id))
    return _attr(info, "id") == project_id


def _existing_names(client, project_id):
    result = _obj(client.secrets().list(project_id))
    rows = _attr(result, "data") or _attr(result, "secrets") or result
    if not isinstance(rows, (tuple, list)):
        raise RuntimeError("list_unavailable")
    return {_attr(row, "key") for row in rows}


def _create(client, name, project_id, org_id, value):
    reply = _obj(client.secrets().create(name, "LIFE-870 Oracle private Buzz pilot", org_id, value, [project_id]))
    ident = _attr(reply, "id")
    if not isinstance(ident, str) or not ident:
        raise RuntimeError("create_receipt_missing")
    return ident


def check_write(client, project_id, org_id, delete_probe):
    """Actual write proof, not merely a read-capable project listing.
    Only an ephemeral synthetic probe is created and then deleted by its ID.
    If cleanup fails, STOP and report the probe name (no key generation)."""
    if not _project_visible(client, project_id):
        return Verdict("LIFE_OS_EDGE_OPENCLAW", "not_visible")
    name = "LIFEOS_BUZZ_PROBE_" + secrets.token_hex(6)
    try:
        ident = _create(client, name, project_id, org_id, "synthetic-write-probe-" + secrets.token_hex(12))
    except Exception:
        return Verdict("LIFE_OS_EDGE_OPENCLAW", "no_write")
    try:
        delete_probe(ident)
    except Exception:
        return Verdict(name, "probe_cleanup_required")
    return Verdict("LIFE_OS_EDGE_OPENCLAW", "write_ok")


def store(client, name, project_id, org_id):
    """Never overwrite an existing owner/relay key or produce an orphan repeat."""
    if name not in NAMES:
        return Verdict(name, "name_refused")
    if not _project_visible(client, project_id):
        return Verdict(name, "project_unavailable")
    try:
        if name in _existing_names(client, project_id):
            return Verdict(name, "already_exists")
    except Exception:
        return Verdict(name, "cannot_check_existing")
    value = secrets.token_hex(32)  # Nostr private key: 32 bytes; created *only on Oracle*
    try:
        _create(client, name, project_id, org_id, value)
    except Exception:
        return Verdict(name, "creation_uncertain_check_vault")
    finally:
        del value
    return Verdict(name, "created")


def client_from_token():
    from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict
    token = os.environ.get("BWS_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("vault_unavailable")
    client = BitwardenClient(client_settings_from_dict({
        "apiUrl": os.environ.get("BW_API_URL") or "https://api.bitwarden.com",
        "identityUrl": os.environ.get("BW_IDENTITY_URL") or "https://identity.bitwarden.com",
        "deviceType": DeviceType.SDK, "userAgent": "lifeos-oracle-buzz-private-pilot"}))
    client.access_token_login(token)
    return client


def delete_probe_only(secret_id):
    # The id, unlike the generated secret value, can safely be passed as argv.
    result = subprocess.run(["bws", "secret", "delete", secret_id], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("probe_cleanup_failed")


def main(argv):
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if platform.system() != "Linux" or platform.machine().lower() not in ("aarch64", "arm64") or pwd.getpwuid(os.geteuid()).pw_name != "ubuntu":
        print("Oracle_ubuntu_only")
        return 77
    if len(argv) != 1 or argv[0] not in ("check", *NAMES):
        print("usage: store-credential check|BUZZ_SELFHOST_OWNER_KEY|BUZZ_SELFHOST_RELAY_KEY")
        return 64
    org_id, project_id = os.environ.get("BUZZ_SELFHOST_BW_ORG_ID", ""), os.environ.get("BUZZ_SELFHOST_BW_PROJECT_ID", "")
    if not org_id or not project_id:
        print("LIFE_OS_EDGE_OPENCLAW config_missing")
        return 78
    try:
        client = client_from_token()
        outcome = check_write(client, project_id, org_id, delete_probe_only)
        print(outcome.name + " " + outcome.status)
        if outcome.status != "write_ok":
            return 1
        if argv[0] == "check":
            return 0
        outcome = store(client, argv[0], project_id, org_id)
        print(outcome.name + " " + outcome.status)
        return 0 if outcome.status == "created" else 1
    except Exception:
        print("LIFE_OS_EDGE_OPENCLAW vault_unavailable")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
