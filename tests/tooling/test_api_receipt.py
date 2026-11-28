"""tools/api_receipt.py prints calls per job per service from the stage_timing rows of a run log."""

import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("api_receipt", ROOT / "tools" / "api_receipt.py")
api_receipt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_receipt)


def line(job, stage, api, begin="2026-11-28T00:00:00-06:00"):
    row = {"stage": stage, "begin": begin, "run": "7", "api_calls": api}
    return f"{job}\tUNKNOWN STEP\t2026-11-28T06:00:00.0Z stage_timing: {json.dumps(row)}"


class Receipt(unittest.TestCase):
    def test_a_job_is_named_by_its_last_part_and_its_stages_are_summed_per_service(self):
        text = "\n".join(
            [
                line("finish / finish", "publish", {"notion": 40, "gmail": 3}),
                line("finish / finish", "audit", {"notion": 2}, begin="2026-11-28T00:01:00-06:00"),
                line("run / mail / mail", "mail", {"gmail": 9, "gmail_units": 300, "gmail_lease_wait_s": 4}),
                "finish / finish\tx\tnot a timing line",
            ]
        )
        calls, extras = api_receipt.tally(text)
        self.assertEqual(calls, {"finish": {"notion": 42, "gmail": 3}, "mail": {"gmail": 9}})
        self.assertEqual(extras, {"mail": {"gmail_units": 300, "gmail_lease_wait_s": 4}})
        table = api_receipt.render(calls, extras)
        self.assertIn("| finish | 3 | 42 | 45 |", table)
        self.assertIn("| all | 12 | 42 | 54 |", table)
        self.assertIn("mail: gmail_lease_wait_s=4, gmail_units=300", table)

    def test_a_row_whose_braces_the_log_masked_is_read_by_its_keys(self):
        masked = (
            'finish / finish\tUNKNOWN STEP\t2026-11-28T06:22:47.6Z stage_timing: ***"api_calls": ***"gmail": 3, "gmail_lease_wait_s": 120, '
            '"gmail_units": 1*** "begin": "2026-11-28T00:19:47-06:00", "elapsed_s": 180.0, "run": "7", "stage": "finalize"***'
        )
        calls, extras = api_receipt.tally(masked)
        self.assertEqual(calls, {"finish": {"gmail": 3}})
        self.assertEqual(extras, {"finish": {"gmail_lease_wait_s": 120, "gmail_units": 1}})

    def test_the_same_row_printed_twice_counts_once_and_a_log_without_counts_is_unverified_not_zero(self):
        one = line("finish / finish", "publish", {"notion": 5})
        calls, _ = api_receipt.tally(one + "\n" + one)
        self.assertEqual(calls, {"finish": {"notion": 5}})
        self.assertIn("UNVERIFIED", api_receipt.render({}, {}))


if __name__ == "__main__":
    unittest.main()
