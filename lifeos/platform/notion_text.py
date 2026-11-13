"""LIFE-645: the plain text of a Notion rich_text list or of a block, read the one way (plain_text, else the text content; anything that is not an object is skipped)."""


def plain(items):
    return "".join((part.get("plain_text") or (part.get("text") or {}).get("content") or "") for part in items or [] if isinstance(part, dict))


def block_text(block):
    """The text of a paragraph, heading, callout, to-do or any block that carries rich_text under its own type."""
    return plain((block.get(block.get("type")) or {}).get("rich_text"))
