"""One-time Google sign-in for the Drive credential (platform, no job knowledge). It asks Google for the drive.file scope only (files LIFE OS
creates), catches the reply on a loopback port, and writes the credential JSON {"client_id", "client_secret", "refresh_token"} to stdout, which
must be a pipe straight into the Edge's encrypted credential store: it refuses to print the credential on a terminal. Prompts go to the terminal,
never to stdout. It runs on Jim's Mac, where the browser is (an SSH -L tunnel to the Edge proved fragile, Oct 24), and pipes over ssh; the client is
the existing LIFE OS Gmail Web client (docs/DRIVE_RESUMES.md). Standalone (stdlib only):
  python3 google_consent.py --port 8765 | ssh opc@lifeos-edge-01 "sudo bash /opt/lifeos-close/bin/install-close.sh credential gdrive"
"""
import base64
import getpass
import hashlib
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request

SCOPE = "https://www.googleapis.com/auth/drive.file"          # the same values as lifeos.platform.gdrive (a test holds them equal)
TOKEN_URL = "https://oauth2.googleapis.com/token"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def auth_url(client_id, redirect, state, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTH_URL + "?" + urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
                                                    "access_type": "offline", "prompt": "consent", "state": state,
                                                    "code_challenge": challenge, "code_challenge_method": "S256"})


def exchange(client_id, client_secret, code, redirect, verifier, opener=urllib.request.urlopen):
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect,
                                   "grant_type": "authorization_code", "code_verifier": verifier}).encode()
    with opener(urllib.request.Request(TOKEN_URL, data=body, method="POST"), timeout=30) as response:
        reply = json.loads(response.read())
    if SCOPE not in str(reply.get("scope", "")).split() or not reply.get("refresh_token"):
        raise SystemExit("Google did not grant the drive.file scope with a refresh token; nothing was stored.")
    return json.dumps({"client_id": client_id, "client_secret": client_secret, "refresh_token": reply["refresh_token"]})


def catch(server, state):
    """Serve until Google redirects back once -> the authorization code (the state must match)."""
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = query.get("state") == [state] and "code" in query
            got["code"] = query["code"][0] if ok else None
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("LIFE OS: done, you can close this tab." if ok else "LIFE OS: sign-in refused; nothing was stored.").encode())

        def log_message(self, *args):
            return

    server.RequestHandlerClass = Handler
    while "code" not in got:
        server.handle_request()
    return got["code"]


def main(argv=(), stdout=sys.stdout, tty=None):
    tty = tty or open("/dev/tty", "w")
    if stdout.isatty():
        tty.write("Refused: pipe this into the Edge credential command; the credential is never printed on screen.\n")
        return 2
    client_id = input_tty("Client ID of the LIFE OS Gmail Web client (Bitwarden openclaw/gmail/clientId): ", tty).strip()
    client_secret = getpass.getpass("Its client secret (Bitwarden openclaw/gmail/clientSecret; not shown): ").strip()
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 0             # a fixed port for the SSH tunnel; 0 = any free port
    server = http.server.HTTPServer(("127.0.0.1", port), http.server.BaseHTTPRequestHandler)
    redirect, state, verifier = f"http://127.0.0.1:{server.server_port}", secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    url = auth_url(client_id, redirect, state, verifier)
    tty.write("Open this address in the browser on your Mac (the SSH tunnel carries the reply back here):\n" + url + "\n")     # never a browser on the Edge
    tty.flush()
    code = catch(server, state)
    if not code:
        tty.write("Sign-in refused; nothing was stored.\n")
        return 1
    stdout.write(exchange(client_id, client_secret, code, redirect, verifier) + "\n")
    stdout.flush()
    tty.write("Credential sent to the Edge (not shown).\n")
    return 0


def input_tty(prompt, tty):
    tty.write(prompt)
    tty.flush()
    with open("/dev/tty") as source:
        return source.readline()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
