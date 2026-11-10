"""Shared fakes for the API clients' network calls: patch urllib.request.urlopen with a function that returns Response(...) or raises
http_error(...). Bodies given as dict/list are JSON-encoded; bytes are sent as is."""
import io
import json
import urllib.error
from email.message import Message


class Response:
    def __init__(self, body=b"{}", status=200, headers=None):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self, amt=None):
        """Like the real response: all of the body, or at most `amt` bytes, advancing (LIFE-549: the fake used to ignore the size)."""
        if amt is None or amt < 0:
            data, self.body = self.body, b""
            return data
        data, self.body = self.body[:amt], self.body[amt:]
        return data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(request, code, body=b"secret body text", headers=None):
    """An HTTPError carrying a body and the URL, so tests can prove neither leaks into the client's fixed error code."""
    message = Message()                                          # LIFE-549: the real error's headers are a case-insensitive Message, not a dict
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(request.full_url, code, "msg", message, io.BytesIO(body))


class ScriptedOpener:
    """Shared deterministic opener fake; each item is a response or exception, and requested URLs stay test-local."""

    def __init__(self, *results):
        self.results, self.urls = list(results), []

    def open(self, request, timeout=0):
        self.urls.append(request.full_url)
        if not self.results:
            raise AssertionError("unexpected extra HTTP request")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result
