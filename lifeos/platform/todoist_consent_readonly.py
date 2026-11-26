"""Read-only Todoist sign-in helper: one link, the reply caught on loopback, the token written only to a pipe. Scope
`data:read` only; a reply with a refresh token is refused and nothing is written (refresh tokens rotate hourly, which
a read-only Bitwarden item cannot follow).
  python3 -m lifeos.platform.todoist_consent_readonly | pbcopy
"""
import getpass
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request

from lifeos.platform import google_consent as base

SCOPE = "data:read"
AUTHORIZE = "https://app.todoist.com/oauth/authorize"
TOKEN_URL = "https://api.todoist.com/oauth/access_token"
REDIRECT = "http://127.0.0.1:8765/callback"


def refuse(code):
    return {"outcome": "refused", "code": code}


def link(client_id, state, redirect=REDIRECT):
    return AUTHORIZE + "?" + urllib.parse.urlencode({"client_id": client_id, "scope": SCOPE, "state": state, "redirect_uri": redirect, "response_type": "code"})


def exchange(client_id, client_secret, code, redirect=REDIRECT, opener=urllib.request.urlopen):
    """-> (token, scope) or a refusal. The reply must grant exactly data:read and carry no refresh token."""
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect}).encode()
    with opener(urllib.request.Request(TOKEN_URL, data=body, method="POST"), timeout=30) as response:
        reply = json.loads(response.read())
    if sorted(str(reply.get("scope", SCOPE)).replace(",", " ").split()) != [SCOPE]:
        return refuse("DENY_UNSUPPORTED_ACTION")
    if reply.get("refresh_token"):
        # turn refresh tokens off for the app, or use the account's own API token
        return refuse("DENY_ROTATING_TOKEN")
    token = reply.get("access_token")
    return (token, SCOPE) if isinstance(token, str) and token.strip() else refuse("DENY_INPUT")


def receipt(outcome, code=None, scope=()):
    return {"outcome": outcome, "code": code, "scopes_granted": list(scope), "stored": "pipe" if outcome == "stored" else "nothing"}


def run(client_id, client_secret, stdout, tty, opener=urllib.request.urlopen, server=None, catcher=base.catch):
    """One sign-in: the link goes to `tty`, the token only to `stdout` (a pipe). A refusal writes nothing to `stdout`.
    """
    if stdout.isatty():
        tty.write("Refused: pipe this into the clipboard command; the token is never printed on screen.\n")
        return receipt("refused", "DENY_SECRET_EXPOSURE")
    server = server or http.server.HTTPServer(("127.0.0.1", 8765), http.server.BaseHTTPRequestHandler)
    state = secrets.token_urlsafe(24)
    tty.write("Open this address yourself in the browser on this Mac and click Agree (read-only access to Todoist):\n" + link(client_id, state) + "\n")
    tty.flush()
    code = catcher(server, state)
    if not code:
        out = receipt("refused", "DENY_PRINCIPAL")
    else:
        result = exchange(client_id, client_secret, code, opener=opener)
        if isinstance(result, dict):
            out = receipt("refused", result["code"])
        else:
            stdout.write(result[0] + "\n")
            stdout.flush()
            out = receipt("stored", None, (result[1],))
    tty.write(json.dumps(out) + "\n")
    return out


def main(stdout=sys.stdout, tty=None):
    tty = tty or open("/dev/tty", "w")
    client_id = base.input_tty("Client ID of the LIFE OS Followup Read app (Todoist app console): ", tty).strip()
    client_secret = getpass.getpass("Its client secret (not shown): ").strip()
    return 0 if run(client_id, client_secret, stdout, tty)["outcome"] == "stored" else 1


if __name__ == "__main__":
    sys.exit(main())
