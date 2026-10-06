import unittest

from lifeos.delivery import census


class FakeGmail:
    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    def list_ids_complete(self, query, limit):
        self.calls.append(query)
        return list(self.bodies)

    def message_record(self, i):
        body = self.bodies[i]
        if body is None:
            raise census.GmailError("GMAIL_MESSAGE_INCOMPLETE")
        return {"body_text": body}

    def trash(self, *a):
        raise AssertionError("the census never writes")

    relabel = apply_amazon = trash


class NumbersTests(unittest.TestCase):
    def test_carrier_shapes(self):
        text = "UPS 1Z999AA10123456784 and TBA123456789012 and USPS 9400111899223197428490 and Tracking number: 123456789012 and tracking #1234567890"
        self.assertEqual(census.numbers_in(text), [("UPS", "1Z999AA10123456784"), ("AMAZON", "TBA123456789012"), ("USPS", "9400111899223197428490"),
                                                   ("FEDEX", "123456789012"), ("DHL", "1234567890")])

    def test_bare_digits_are_not_tracking_numbers(self):
        self.assertEqual(census.numbers_in("Order 123456789012 call 2125551234"), [])

    def test_a_repeat_in_one_message_counts_once(self):
        self.assertEqual(len(census.numbers_in("1Z999AA10123456784 again 1Z999AA10123456784")), 1)


class RunTests(unittest.TestCase):
    def test_counts_only_and_no_write(self):
        gm = FakeGmail({"a": "1Z999AA10123456784", "b": "1Z999AA10123456784 TBA123456789012", "c": "nothing here", "d": None})
        counts = census.run(0, True, gmail=gm)
        self.assertEqual((counts["messages"], counts["with_numbers"], counts["without_numbers"], counts["unreadable"]), (4, 2, 1, 1))
        self.assertEqual((counts["numbers"], counts["distinct"], counts["repeated"], counts["written"]), (3, 2, 1, 0))
        self.assertEqual(counts["by_carrier"]["UPS"], 2)
        self.assertNotIn("1Z999", str(counts))
        self.assertIn("newer_than:365d", gm.calls[0])


if __name__ == "__main__":
    unittest.main()
