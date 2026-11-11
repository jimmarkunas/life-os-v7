"""LIFE-596 scratch proof: one deliberate B904 violation. This branch is never merged."""


def f():
    try:
        return 1
    except ValueError:
        raise RuntimeError("x")
