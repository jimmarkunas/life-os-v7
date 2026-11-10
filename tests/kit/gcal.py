"""Synthetic Calendar API event pages and a read-only calendar fake."""


class CalendarEvents:
    def __init__(self, *pages):
        self.pages, self.windows, self.private_properties = list(pages), [], []

    def list_events(self, time_min, time_max, private_property=None):          # LIFE-549: the real client takes this optional filter too
        self.windows.append((time_min, time_max))
        self.private_properties.append(private_property)
        out = []
        while self.pages:
            page = self.pages.pop(0)
            if isinstance(page, BaseException):
                raise page
            if not isinstance(page, list):
                raise AssertionError("invalid scripted calendar page")
            out.extend(page)
        return out
