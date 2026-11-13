"""LIFE-645: the one UTC clock. A stage keeps it under its own name (`_now = clock.utcnow`) so a test can pin that stage alone."""
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)
