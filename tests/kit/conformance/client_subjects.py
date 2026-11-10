"""Observers for the clients family: the real Gmail and Google Calendar clients (signatures only) and their kit fakes."""
import inspect

from lifeos.platform.gcal import GoogleCalendar
from lifeos.platform.gmail import Gmail
from tests.kit.gcal import CalendarEvents
from tests.kit.gmail import FakeGmail


def observe(target):
    def run(case):
        method = getattr(target, case["method"], None)
        if method is None:
            return {"params": None}
        return {"params": [name if p.default is inspect.Parameter.empty else f"{name}={p.default!r}"
                           for name, p in inspect.signature(method).parameters.items() if name != "self"]}
    return run


SUBJECTS = {"real_gmail": observe(Gmail), "fake_gmail": observe(FakeGmail), "real_calendar": observe(GoogleCalendar), "fake_calendar": observe(CalendarEvents)}
