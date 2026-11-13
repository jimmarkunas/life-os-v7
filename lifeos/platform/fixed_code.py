"""LIFE-645: `with fixed(catch, fail, code):` re-raises anything `catch` matches as the caller's fixed-code error, with no chained cause (a Notion body, a URL or a token never rides along).
`keep` names errors that already carry a fixed code: they pass through unchanged (`with fixed(Exception, fail, code, keep=NotionError):`)."""
from contextlib import contextmanager


@contextmanager
def fixed(catch, fail, code, keep=()):
    try:
        yield
    except keep:
        raise
    except catch:
        raise fail(code) from None
