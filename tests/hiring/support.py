"""LIFE-544: test helpers that more than one test module (often in another domain) uses. Moved here out of test_*.py files, unchanged."""
import copy


def writes(tree):
    return [e for e in tree.log if e[0] != "GET"]


class FakeStore:
    """The projection-row store stand-in: `row` is what it holds, `saves` counts verified saves, `fail` makes the read-back refuse."""
    table = "v7_hiring_outcomes"

    def __init__(self, row=None, fail=False):
        self.row, self.saves, self.fail = row, 0, fail

    def ensure(self, connection):
        pass

    def load(self, connection, key):
        return copy.deepcopy(self.row)

    def save_verified(self, connection, key, snap, fail):
        if self.fail:
            raise fail("READBACK_MISMATCH")
        self.saves += 1
        self.row = copy.deepcopy(snap)
