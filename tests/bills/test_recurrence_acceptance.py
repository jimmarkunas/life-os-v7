import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.bills import paid
from tests.kit.db import BillsPaidDB
from tests.kit.notion import PaidBillTracker


def paid_page(*, page_id, cycle, next_due="2026-10-10"):
    return {
        "id": page_id,
        "parent": {"type": "data_source_id", "data_source_id": PaidBillTracker.SOURCE},
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Synthetic recurrence fixture"}]},
            "Status": {"type": "select", "select": {"name": "Active"}},
            "Cycle": {"type": "select", "select": {"name": cycle}},
            "Paid": {"type": "checkbox", "checkbox": True},
            "Due Date": {"type": "date", "date": {"start": "2026-10-01"}},
            "Next Due": {"type": "formula", "formula": {"type": "date", "date": {"start": next_due}}},
            "Last Paid": {"type": "date", "date": None},
            "Costs per Cycle": {"type": "number", "number": 12.34},
            "Last Observed Amount": {"type": "number", "number": 12.34},
        },
    }


class RecurrenceAcceptanceTests(unittest.TestCase):
    def test_weekly_biweekly_and_quarterly_paid_transitions_are_deterministic(self):
        cases = (
            ("Weekly", "11111111-1111-4111-8111-111111111117", "2026-10-17"),
            ("Bi-Weekly", "11111111-1111-4111-8111-111111111114", "2026-10-24"),
            ("Quarterly", "11111111-1111-4111-8111-111111111191", "2027-01-09"),
        )
        now = datetime(2026, 10, 3, 12, tzinfo=ZoneInfo("America/Chicago"))

        for cycle, page_id, expected_next_due in cases:
            with self.subTest(cycle=cycle):
                page = paid_page(page_id=page_id, cycle=cycle)
                client = PaidBillTracker(page)
                database = BillsPaidDB(client.event_log)

                counts = paid.run(1, True, client=client, now=now, connect=lambda: database)
                props = client.pages[page_id]["properties"]

                self.assertEqual(counts["advanced"], 1)
                self.assertEqual(counts["failed"], 0)
                self.assertEqual(counts["review"], 0)
                self.assertEqual(props["Due Date"]["date"]["start"], "2026-10-10")
                self.assertEqual(props["Last Paid"]["date"]["start"], "2026-10-03")
                self.assertEqual(props["Next Due"]["formula"]["date"]["start"], expected_next_due)
                self.assertFalse(props["Paid"]["checkbox"])
                self.assertEqual(client.writes, [("Last Paid", "Due Date"), ("Paid",)])


if __name__ == "__main__":
    unittest.main()
