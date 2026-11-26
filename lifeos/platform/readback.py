"""After a write, READ again (up to `attempts` times, `pause` seconds apart) until what is read matches, since Notion may
briefly show the old state. Never repeats a write.
"""
import time


def confirmed(read, matches, attempts=4, pause=3, sleep=None):
    """-> (True, value) for the first value `matches` accepts, else (False, the last value). No pause before the first
    read.
    """
    sleep = sleep or time.sleep
    value = None
    for attempt in range(attempts):
        if attempt:
            sleep(pause)
        value = read()
        if matches(value):
            return True, value
    return False, value
