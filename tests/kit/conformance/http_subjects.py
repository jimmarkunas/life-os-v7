"""Observers for the http family: the fake (tests/kit/http.py) and the real urllib over a loopback server. Stdlib only."""
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.kit.http import Response, http_error


def observe_fake(case):
    body, headers = case["body"].encode(), case.get("headers", {})
    request = urllib.request.Request("http://example.invalid/x")
    out = {}
    if case["kind"] == "response":
        response = Response(body, case["status"], headers)
        with response as entered:
            out["ctx_returns_self"] = entered is response
        out["exit_returns_false"] = response.__exit__(None, None, None) is False
        out["status"] = response.status
        fresh = Response(body, case["status"], headers)
        if "partial" in case:
            out["partial"] = fresh.read(case["partial"]).decode()
        else:
            out["body"] = fresh.read().decode()
        if "lookup" in case:
            out["header"] = fresh.headers.get(case["lookup"])
        return out
    error = http_error(request, case["status"], body, headers)
    out["raised"] = isinstance(error, urllib.error.HTTPError)
    out["code"] = error.code
    out["body"] = error.read().decode()
    out["url_ok"] = error.url == request.full_url
    if "lookup" in case:
        out["header"] = error.headers.get(case["lookup"])
    return out


class _Handler(BaseHTTPRequestHandler):
    cases = {}

    def do_GET(self):                                                        # noqa: N802 - the stdlib names the hook
        case = self.cases[self.path.lstrip("/")]
        data = case["body"].encode()
        self.send_response(case["status"])
        for key, value in case.get("headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Loopback:
    """A throwaway HTTP server on 127.0.0.1 that answers each case's status, headers and body."""

    def __init__(self, spec):
        handler = type("Handler", (_Handler,), {"cases": {c["name"]: c for c in spec["cases"]}})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False

    def observe(self, case):
        url = f"http://127.0.0.1:{self.server.server_address[1]}/{case['name']}"
        out = {}
        if case["kind"] == "response":
            with urllib.request.urlopen(url, timeout=10) as real:
                out["ctx_returns_self"] = True                                # the real object is its own context; checked below on a second open
                out["status"] = real.status
                if "partial" in case:
                    out["partial"] = real.read(case["partial"]).decode()
                else:
                    out["body"] = real.read().decode()
                if "lookup" in case:
                    out["header"] = real.headers.get(case["lookup"])
            opened = urllib.request.urlopen(url, timeout=10)
            out["ctx_returns_self"] = opened.__enter__() is opened
            out["exit_returns_false"] = not opened.__exit__(None, None, None)
            return out
        try:
            urllib.request.urlopen(url, timeout=10)
        except urllib.error.HTTPError as error:
            out["raised"] = True
            out["code"] = error.code
            out["body"] = error.read().decode()
            out["url_ok"] = error.url == url
            if "lookup" in case:
                out["header"] = error.headers.get(case["lookup"])
        return out
