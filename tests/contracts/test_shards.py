import unittest

from tests import shard


class ShardsPartitionTheSuite(unittest.TestCase):
    """The PR check runs `python -m tests.shard 1 2` and `2 2` side by side (Jim, Oct 26 2026). A test that fell in neither shard would silently never run."""

    def ids(self, k, n):
        suite, total = shard.shard_suite(k, n)
        return [t.id() for t in shard.tests_of(suite)], total

    def test_every_test_runs_in_exactly_one_of_two_shards(self):
        first, total = self.ids(1, 2)
        second, _ = self.ids(2, 2)
        self.assertEqual(len(first) + len(second), total)
        self.assertEqual(set(first) & set(second), set())
        everything = {t.id() for t in shard.tests_of(unittest.defaultTestLoader.discover("tests", top_level_dir=str(shard.ROOT)))}
        self.assertEqual(set(first) | set(second), everything)

    def test_each_shard_is_substantial_and_this_file_is_in_one(self):
        first, total = self.ids(1, 2)
        second, _ = self.ids(2, 2)
        self.assertGreater(min(len(first), len(second)), total // 3)           # a lopsided split would put the time back on one side
        self.assertTrue(any("test_shards" in i for i in first + second))

    def test_a_module_is_never_split_across_shards(self):
        first, _ = self.ids(1, 2)
        second, _ = self.ids(2, 2)
        modules = lambda ids: {i.rsplit(".", 2)[0] for i in ids}
        self.assertEqual(modules(first) & modules(second), set())

    def test_bad_arguments_are_refused(self):
        for argv in (["0", "2"], ["3", "2"]):
            with self.assertRaises(SystemExit):
                shard.main(argv)
