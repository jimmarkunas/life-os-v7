"""LIFE-526: the one read-back wait: after a write, READ again (up to `attempts` times, `pause` seconds apart) until what is read matches.

Notion may briefly still show the old state. It only ever repeats a READ, never a write; a write that may or may not have happened is the caller's business (read it back; do not send it again). It prints nothing and knows no content: `read` and `matches` are the caller's. Used by the Notion card writers (jobs/fit/capacity_card, megibow/card, platform/report_region)."""
import time


def confirmed(read, matches, attempts=4, pause=3, sleep=None):
    """-> (True, value) for the first value read that `matches` accepts, else (False, the last value read). No pause before the first read."""
    sleep = sleep or time.sleep                                  # looked up at call time, so a test that patches time.sleep still takes effect
    value = None
    for attempt in range(attempts):
        if attempt:
            sleep(pause)
        value = read()
        if matches(value):
            return True, value
    return False, value
