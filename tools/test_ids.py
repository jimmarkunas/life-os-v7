"""LIFE-1019: the collected test IDs, for the proof that a tests/ change kept every test.

    python tools/test_ids.py            print the count and the SHA-256 of the sorted ID list
    python tools/test_ids.py --list     print the sorted IDs

Collection is the suite's own: unittest discovery under tests/ from the repository root, as tests/shard.py does."""

import hashlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def ids():
    def walk(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from walk(item)
            else:
                yield item.id()

    return sorted(walk(unittest.TestLoader().discover(str(ROOT / "tests"), top_level_dir=str(ROOT))))


def main(argv):
    found = ids()
    if "--list" in argv:
        print("\n".join(found))
        return 0
    print(f"{len(found)} ids sha256 {hashlib.sha256(chr(10).join(found).encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
