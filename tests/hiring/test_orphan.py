"""LIFE-47 P0 (BRANCH ONLY): the one-shot removal of the orphaned status paragraph."""
import unittest

from lifeos.hiring import orphan
from lifeos.hiring.snapshot import HiringError
from tests.hiring.test_hiring import ENV, page, writes
from tests.kit.tree import Tree

ORPHAN = "0000000000000000000000000000a002"


class TrashTree(Tree):
    def call(self, method, path, body=None):
        parts = path.strip("/").split("/")
        if method == "PATCH" and len(parts) == 2 and body and body.get("in_trash") is True:
            self.log.append((method, path))
            block = self.blocks[parts[1]]
            self.kids[block["parent"].get("block_id") or block["parent"]["page_id"]].remove(parts[1])
            block["in_trash"] = True
            return {}
        return super().call(method, path, body)


def tree(**kwargs):
    t = page(**kwargs)
    t.__class__ = TrashTree
    return t


class OrphanTests(unittest.TestCase):
    def test_dry_run_proves_and_writes_nothing(self):
        t = tree(status="orphan")
        self.assertEqual(orphan.run(0, False, ENV | {"HIRING_STATUS_BLOCK_ID": ORPHAN}, t), {"trashed": False, "changed": 0})
        self.assertEqual(writes(t), [])

    def test_live_trashes_only_the_orphan_and_the_rest_of_the_page_is_unchanged(self):
        t = tree(status="orphan")
        before = {k: v for k, v in t.snapshot().items() if k != ORPHAN}
        self.assertEqual(orphan.run(0, True, ENV | {"HIRING_STATUS_BLOCK_ID": ORPHAN}, t), {"trashed": True, "changed": 1})
        self.assertEqual([p for m, p in writes(t)], [f"/blocks/{ORPHAN}"])
        self.assertEqual({k: v for k, v in t.snapshot().items() if k != ORPHAN}, before)
        self.assertNotIn(ORPHAN, t.kids["right"])
        self.assertEqual(orphan.run(0, True, ENV | {"HIRING_STATUS_BLOCK_ID": ORPHAN}, t), {"trashed": True, "changed": 0})       # a replay writes nothing

    def test_anything_unexpected_stops_before_a_write(self):
        for kwargs, env in (({"status": "between"}, {}), ({}, {"HIRING_STATUS_BLOCK_ID": "0000000000000000000000000000a004"}), ({}, {"HIRING_STATUS_BLOCK_ID": ""})):
            t = tree(**kwargs)
            with self.assertRaises(Exception):
                orphan.run(0, True, ENV | {"HIRING_STATUS_BLOCK_ID": ORPHAN} | env, t)
            self.assertEqual(writes(t), [])
        t = tree(status="orphan")
        t.add("late", "paragraph", "right", text="not the last child anymore")             # the orphan is no longer the last child of its column
        with self.assertRaises(HiringError) as error:
            orphan.run(0, True, ENV | {"HIRING_STATUS_BLOCK_ID": ORPHAN}, t)
        self.assertEqual((str(error.exception), writes(t)), ("HIRING_ORPHAN_STATE_UNEXPECTED", []))


if __name__ == "__main__":
    unittest.main()
