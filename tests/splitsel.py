"""Size the test jobs of a pull request run: `python -m tests.splitsel selected.txt` prints the two GitHub output lines
`jobs=[...]` (the matrix of the `tests` job) and `groups=[...]` (one space-separated target list per job; empty for a FULL
pick, whose jobs run shards of tests.shard instead).

A selected run (the contracts plus the tests a change can affect) goes to as few jobs as keep each under PER_JOB CI
seconds of tests, at most MAX_JOBS, split by the measured seconds in tests/shard-weights.json.
"""

import json
import math
import pathlib
import sys

from tests import shard

FULL_JOBS = list(range(1, 9))  # a FULL pick: 8 jobs x 4 of the 32 shards
PER_JOB = 10.0
MAX_JOBS = 4
UNKNOWN = 1.0  # CI seconds assumed for a target the weights file does not list


def weight(target, weights):
    """Seconds of a target (a dotted module or test id): the weight of the longest listed prefix."""
    name = target
    while name:
        if name in weights:
            return weights[name]
        name = name.rpartition(".")[0]
    return UNKNOWN


def plan(targets, weights, per_job=PER_JOB, max_jobs=MAX_JOBS):
    """> (jobs, groups): the matrix and each job's targets."""
    if not targets or targets[0] == "FULL":
        return FULL_JOBS, []
    cost = {t: weight(t, weights) for t in targets}
    n = max(1, min(max_jobs, len(targets), math.ceil(sum(cost.values()) / per_job)))
    groups, load = [[] for _ in range(n)], [0.0] * n
    for t in sorted(targets, key=lambda t: (-cost[t], t)):  # heaviest first, each to the lightest job
        i = load.index(min(load))
        groups[i].append(t)
        load[i] += cost[t]
    return list(range(1, n + 1)), [" ".join(sorted(g)) for g in groups]


def main(argv=None):
    path = (argv if argv is not None else sys.argv[1:])[0]
    targets = [
        line.strip()
        for line in pathlib.Path(path).read_text().splitlines()
        if line.strip() and not line.startswith("FULL because ")
    ]
    jobs, groups = plan(targets, shard.load_weights())
    print("jobs=" + json.dumps(jobs))
    print("groups=" + json.dumps(groups))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
