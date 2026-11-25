"""LIFE-856: the after-tick domains run asks whether the tick's hourly `finish` job ran before it runs the jobs that moved out of it. Synthetic jobs; no network."""
import io
import tempfile
import unittest
from pathlib import Path

from lifeos.platform import pipeline_ran as p


def output(jobs=None, boom=False):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "out")
        def api(url):
            if boom:
                raise OSError("down")
            return {"jobs": jobs}
        p.main({"TICK_RUN": "123", "GITHUB_OUTPUT": str(path)}, api)
        return path.read_text().strip()


class Ran(unittest.TestCase):
    def test_a_finish_job_that_ran_is_true_and_a_skipped_one_is_false(self):
        self.assertEqual(output([{"name": "prep", "conclusion": "success"}, {"name": "finish", "conclusion": "success"}]), "ran=true")
        self.assertEqual(output([{"name": "finish", "conclusion": "failure"}]), "ran=true")                  # a failed pipeline still owes its moved work
        self.assertEqual(output([{"name": "prep", "conclusion": "success"}, {"name": "finish", "conclusion": "skipped"}]), "ran=false")
        self.assertEqual(output([{"name": "prep", "conclusion": "success"}]), "ran=false")

    def test_an_unreadable_answer_is_unknown_never_false(self):
        self.assertEqual(output(boom=True), "ran=unknown")
