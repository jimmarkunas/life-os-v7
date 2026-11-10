"""LIFE-544: test helpers that more than one test module (often in another domain) uses. Moved here out of test_*.py files, unchanged."""


def writes(tree):
    return [e for e in tree.log if e[0] != "GET"]
