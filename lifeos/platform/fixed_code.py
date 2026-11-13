"""LIFE-645: `with fixed(catch, fail, code):` re-raises anything `catch` matches as the caller's fixed-code error, with no chained cause (a Notion body, a URL or a token never rides along)."""
from contextlib import contextmanager


@contextmanager
def fixed(catch, fail, code):
    try:
        yield
    except catch:
        raise fail(code) from None
