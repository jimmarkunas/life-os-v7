"""Read-only Google sign-in helper (LIFE-423, LOS-86 T13). It PRINTS a sign-in link for Jim to open himself, catches the reply on a loopback port (PKCE, state checked) and writes the
credential only to a pipe, exactly like `google_consent` (whose PKCE link builder and loopback catcher it reuses; nothing there is changed). The differences are the point:

* Scopes are limited to the read-only set for ONE service: Gmail readonly, Drive readonly (metadata), Calendar readonly. Any other scope, or a reply that grants more, is refused in code.
* A link is produced only for an account on the allowlist (account, tenant, service, label or folder, size cap). An account that is not listed gets no link and nothing is stored.
* A refusal returns a named, machine-readable code; nothing is written to stdout, the credential pipe, or anywhere else.
* The receipt shows the scopes granted and never holds a secret. Nothing here opens a browser, calls Google by itself or reads a credential store; tests use a fake token endpoint.

Run on Jim's Mac (the host he chose, Nov 9 2026), piped to the Edge credential command, the same way as the full-scope script:
  python3 -m lifeos.platform.google_consent_readonly --account ACCT --service gmail --allowlist FILE | ssh ... credential NAME
"""
import getpass
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request

from lifeos.platform import google_consent as base

READONLY_SCOPES = {"gmail": ("https://www.googleapis.com/auth/gmail.readonly",),
                   "drive": ("https://www.googleapis.com/auth/drive.metadata.readonly",),
                   "calendar": ("https://www.googleapis.com/auth/calendar.readonly",)}
ENTRY_FIELDS = ("account", "tenant", "service", "label", "size_cap")


def refuse(code):
    return {"outcome": "refused", "code": code}


def entry_ok(entry):
    return (isinstance(entry, dict) and all(isinstance(entry.get(k), str) and entry[k].strip() for k in ("account", "tenant", "label"))
            and entry.get("service") in READONLY_SCOPES and isinstance(entry.get("size_cap"), int) and not isinstance(entry["size_cap"], bool) and entry["size_cap"] > 0)


def plan(allowlist, account, service, scopes=None):
    """-> {"outcome": "ok", "scopes": [...], "entry": {...}} or a refusal. No link is built, nothing is read from anywhere but the arguments.
    The account must be on the allowlist for this service, and the scopes (default: the read-only set) must be exactly that service's read-only set."""
    if not (isinstance(account, str) and account.strip() and service in READONLY_SCOPES and isinstance(allowlist, list)):
        return refuse("DENY_INPUT")
    entries = [e for e in allowlist if entry_ok(e) and e["account"] == account and e["service"] == service]
    if len(entries) != 1:
        return refuse("DENY_ACCOUNT_AMBIGUOUS")                                  # not listed, listed twice, or listed for another service: no link
    wanted = list(READONLY_SCOPES[service])
    if scopes is not None and (not isinstance(scopes, (list, tuple)) or sorted(map(str, scopes)) != sorted(wanted)):
        return refuse("DENY_UNSUPPORTED_ACTION")                                 # any write, delete or extra scope
    return {"outcome": "ok", "scopes": wanted, "entry": dict(entries[0])}


def link(client_id, redirect, state, verifier, scopes):
    """The sign-in link: the full-scope script's PKCE link with only the scope swapped, so the challenge code is reused, not copied."""
    parts = urllib.parse.urlparse(base.auth_url(client_id, redirect, state, verifier))
    query = urllib.parse.parse_qsl(parts.query)
    query = [(k, " ".join(scopes) if k == "scope" else v) for k, v in query]
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(query)))


def exchange(client_id, client_secret, code, redirect, verifier, wanted, opener=urllib.request.urlopen):
    """-> (credential JSON string, granted scopes) or a refusal dict. The reply must grant exactly the wanted read-only scopes and a refresh token; anything more is refused."""
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect,
                                   "grant_type": "authorization_code", "code_verifier": verifier}).encode()
    with opener(urllib.request.Request(base.TOKEN_URL, data=body, method="POST"), timeout=30) as response:
        reply = json.loads(response.read())
    granted = sorted(str(reply.get("scope", "")).split())
    if granted != sorted(wanted):
        return refuse("DENY_UNSUPPORTED_ACTION")
    if not reply.get("refresh_token"):
        return refuse("DENY_INPUT")
    return json.dumps({"client_id": client_id, "client_secret": client_secret, "refresh_token": reply["refresh_token"]}), granted


def receipt(entry, scopes, outcome, code=None):
    """What may be shown or kept: who, which service, which scopes, and that the credential went to the pipe. Never a secret."""
    return {"outcome": outcome, "code": code, "account": entry["account"] if entry else None, "tenant": entry["tenant"] if entry else None,
            "service": entry["service"] if entry else None, "label": entry["label"] if entry else None, "size_cap": entry["size_cap"] if entry else None,
            "scopes_granted": list(scopes), "stored": "pipe" if outcome == "stored" else "nothing"}


def run(allowlist, account, service, client_id, client_secret, stdout, tty, opener=urllib.request.urlopen, server=None, catcher=base.catch, scopes=None):
    """One sign-in. The link goes to `tty`; the credential only to `stdout` (a pipe); the receipt to `tty`. -> the receipt. A refusal writes nothing to `stdout`."""
    if stdout.isatty():
        tty.write("Refused: pipe this into the Edge credential command; the credential is never printed on screen.\n")
        return receipt(None, (), "refused", "DENY_SECRET_EXPOSURE")
    verdict = plan(allowlist, account, service, scopes)
    if verdict["outcome"] != "ok":
        out = receipt(None, (), "refused", verdict["code"])
        tty.write(json.dumps(out) + "\n")
        return out                                                                # no link, no server, nothing stored
    entry, wanted = verdict["entry"], verdict["scopes"]
    server = server or http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    redirect, state, verifier = f"http://127.0.0.1:{server.server_port}", secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    tty.write("Open this address yourself in the browser on this Mac and click Allow (read-only access to " + service + "):\n" + link(client_id, redirect, state, verifier, wanted) + "\n")
    tty.flush()
    code = catcher(server, state)
    if not code:
        out = receipt(entry, (), "refused", "DENY_PRINCIPAL")                     # the reply did not carry this sign-in's state
        tty.write(json.dumps(out) + "\n")
        return out
    result = exchange(client_id, client_secret, code, redirect, verifier, wanted, opener)
    if isinstance(result, dict):
        out = receipt(entry, (), "refused", result["code"])
        tty.write(json.dumps(out) + "\n")
        return out
    credential, granted = result
    stdout.write(credential + "\n")
    stdout.flush()
    out = receipt(entry, granted, "stored")
    tty.write(json.dumps(out) + "\n")
    return out


def main(argv=(), stdout=sys.stdout, tty=None):
    tty = tty or open("/dev/tty", "w")
    arg = lambda name: argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None
    account, service, path = arg("--account"), arg("--service"), arg("--allowlist")
    try:
        with open(path) as handle:
            allowlist = json.load(handle)
    except (TypeError, OSError, ValueError):
        tty.write(json.dumps(receipt(None, (), "refused", "DENY_INPUT")) + "\n")
        return 2
    verdict = plan(allowlist, account, service)
    if verdict["outcome"] != "ok":                                                # decided before any prompt for a client id or secret
        tty.write(json.dumps(receipt(None, (), "refused", verdict["code"])) + "\n")
        return 1
    client_id = base.input_tty("Client ID of the LIFE OS Gmail Web client (Bitwarden openclaw/gmail/clientId): ", tty).strip()
    client_secret = getpass.getpass("Its client secret (Bitwarden openclaw/gmail/clientSecret; not shown): ").strip()
    return 0 if run(allowlist, account, service, client_id, client_secret, stdout, tty)["outcome"] == "stored" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
