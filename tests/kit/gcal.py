"""Synthetic Calendar API event pages and a read-only calendar fake."""


class CalendarEvents:
    def __init__(self, *pages):
        self.pages, self.windows = list(pages), []

    def list_events(self, time_min, time_max):
        self.windows.append((time_min, time_max))
        out = []
        while self.pages:
            page = self.pages.pop(0)
            if isinstance(page, BaseException):
                raise page
            if not isinstance(page, list):
                raise AssertionError("invalid scripted calendar page")
            out.extend(page)
        return out
