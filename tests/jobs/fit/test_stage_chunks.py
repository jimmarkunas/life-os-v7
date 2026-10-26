"""LIFE-76 (Oct 25): the Fit stage scores and WRITES a few rows at a time, so a run that hits its stage timeout keeps the rows it finished, and it prints where it is."""
import contextlib
import io
import json
from unittest import mock

from lifeos.jobs.fit import stage
from tests.jobs.fit.test_cloud import Base, PROFILE

ENV = {"V7_FIT_GATE": "false", "V7_CLOUD_FIT": "off"}


class StageTimeout(Exception):
    """Stands in for lifeos.jobs.tick.StageTimeout: the alarm firing inside a score."""


class ChunkedStage(Base):
    def stale(self, n):
        for job_id in range(1, n + 1):
            self.job(job_id)
            self.db.raw.execute("UPDATE v7_job_fit SET model_version='old' WHERE job_id=?", (job_id,))

    def scored(self):
        return self.db.raw.execute("SELECT COUNT(*) FROM v7_job_fit WHERE model_version <> 'old'").fetchone()[0]

    def run_stage(self, limit, live=True, real_score=stage.score_rows):
        out = io.StringIO()
        with mock.patch.object(stage.store, "connect", return_value=self.db), mock.patch.object(stage.store, "ensure_schema"), \
                mock.patch.object(stage.fit_profile, "load", return_value=PROFILE), mock.patch.object(stage.sponsors, "load", return_value=None), \
                mock.patch.object(stage, "score_rows", side_effect=real_score), contextlib.redirect_stdout(out):
            counts = stage.run(limit, live, ENV)
        return counts, out.getvalue()

    def test_every_row_is_scored_and_written_across_chunks(self):
        self.stale(25)
        counts, _ = self.run_stage(25)
        self.assertEqual(counts["picked"], 25)
        self.assertEqual(self.scored(), 25)
        self.assertEqual(counts["go"] + counts["no_go"] + counts["unscorable"], 25)

    def test_a_dry_run_scores_and_writes_nothing(self):
        self.stale(12)
        counts, _ = self.run_stage(12, live=False)
        self.assertEqual((counts["picked"], self.scored()), (12, 0))
        self.assertEqual(counts["go"] + counts["no_go"] + counts["unscorable"], 12)

    def test_a_timeout_in_a_later_chunk_keeps_the_chunks_already_written(self):
        self.stale(25)
        calls = []

        def slow(rows, *a, **kw):
            calls.append(len(rows))
            if len(calls) == 3:
                raise StageTimeout()                                             # the alarm fires while the third chunk is being scored
            return stage_score(rows, *a, **kw)
        stage_score = stage.score_rows
        with self.assertRaises(StageTimeout):
            self.run_stage(25, real_score=slow)
        self.assertEqual(calls, [stage.CHUNK, stage.CHUNK, 5])
        self.assertEqual(self.scored(), 2 * stage.CHUNK)                          # two full chunks were already saved: the next run starts at row 21, not row 1

    def test_the_next_run_after_a_timeout_picks_only_what_is_left(self):
        self.stale(25)
        calls = []

        def slow(rows, *a, **kw):
            calls.append(len(rows))
            if len(calls) == 2:
                raise StageTimeout()
            return stage_score(rows, *a, **kw)
        stage_score = stage.score_rows
        with self.assertRaises(StageTimeout):
            self.run_stage(25, real_score=slow)
        counts, _ = self.run_stage(25)
        self.assertEqual(counts["picked"], 25 - stage.CHUNK)

    def test_progress_lines_name_each_phase_with_seconds_and_counts_only(self):
        self.stale(12)
        _, printed = self.run_stage(12)
        lines = [json.loads(l.split(": ", 1)[1]) for l in printed.splitlines() if l.startswith("fit-progress: ")]
        phases = [l["phase"] for l in lines]
        self.assertEqual(phases[:3], ["profile", "embedder", "prepared"])
        self.assertIn("picked", phases)
        self.assertEqual(phases.count("scored"), 2)                               # 12 rows = chunks of 10 and 2
        self.assertEqual(lines[-1], {"phase": "scored", "seconds": lines[-1]["seconds"], "done": 12, "of": 12})
        self.assertTrue(all(set(l) <= {"phase", "seconds", "picked", "semantic", "done", "of"} for l in lines))
