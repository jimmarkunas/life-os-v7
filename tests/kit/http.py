"""Shared fakes for the API clients' network calls: patch urllib.request.urlopen with a function that returns Response(...) or raises
http_error(...). Bodies given as dict/list are JSON-encoded; bytes are sent as is."""
import io
import json
import urllib.error


class Response:
    def __init__(self, body=b"{}"):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, *_):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(request, code, body=b"secret body text", headers=None):
    """An HTTPError carrying a body and the URL, so tests can prove neither leaks into the client's fixed error code."""
    return urllib.error.HTTPError(request.full_url, code, "msg", headers or {}, io.BytesIO(body))
