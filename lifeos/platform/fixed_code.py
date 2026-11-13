"""LIFE-645: `with fixed(catch, fail, code):` re-raises anything `catch` matches as the caller's fixed-code error, with no chained cause (a Notion body, a URL or a token never rides along).
`keep` names errors that already carry a fixed code: they pass through unchanged (`with fixed(Exception, fail, code, keep=NotionError):`)."""
from contextlib import contextmanager

from lifeos.platform.notion_client import NotionError


def note_cause(error):
    """LIFE-654: a stage's fixed code hides which Notion answer caused it; print that answer's own fixed code (`cause=NOTION_HTTP_409`), never its body, ids or text."""
    if isinstance(error, NotionError):
        print(f"cause={error}")


@contextmanager
def fixed(catch, fail, code, keep=()):
    try:
        yield
    except keep:
        raise
    except catch as error:
        note_cause(error)
        raise fail(code) from None
