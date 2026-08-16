"""Path helpers and the copy decision."""

import os
import unittest
from pathlib import Path

import sync

from .support import DIR, TreeCase, build, make_junction, needs_junctions


class TestResolvePath(TreeCase):

    def test_absolute_path_is_kept(self):
        self.assertEqual(self.src, sync.resolve_path(str(self.src), self.dest))

    def test_relative_path_is_joined_to_the_base(self):
        build(self.dest, {"sub/": DIR})
        self.assertEqual(self.dest / "sub", sync.resolve_path("sub", self.dest))

    def test_user_home_is_expanded(self):
        self.assertEqual(Path.home().resolve(), sync.resolve_path("~", self.dest))

    def test_environment_variables_are_expanded(self):
        var = "%SYNC_TEST_DIR%" if os.name == "nt" else "$SYNC_TEST_DIR"
        os.environ["SYNC_TEST_DIR"] = str(self.src)
        self.addCleanup(os.environ.pop, "SYNC_TEST_DIR", None)
        self.assertEqual(self.src, sync.resolve_path(var, self.dest))


class TestOverlaps(TreeCase):

    def test_same_folder_overlaps(self):
        self.assertTrue(sync.overlaps(self.src, self.src))

    def test_source_inside_destination_overlaps(self):
        build(self.dest, {"inner/": DIR})
        self.assertTrue(sync.overlaps(self.dest / "inner", self.dest))

    def test_destination_inside_source_overlaps(self):
        build(self.src, {"inner/": DIR})
        self.assertTrue(sync.overlaps(self.src, self.src / "inner"))

    def test_siblings_do_not_overlap(self):
        self.assertFalse(sync.overlaps(self.src, self.dest))


class TestByDepth(unittest.TestCase):

    def test_orders_by_depth_then_name(self):
        rels = ["b/c/d", "a", "b/a", "b", "a/z", "a/b"]
        self.assertEqual(["a", "b", "a/b", "a/z", "b/a", "b/c/d"],
                         sorted(rels, key=sync.by_depth))

    def test_is_deterministic_for_equal_depth(self):
        # the tie-break on the name is what keeps two runs printing the same
        # order; without it the order came from set iteration
        names = {"zulu", "alpha", "mike"}
        self.assertEqual(sorted(names, key=sync.by_depth),
                         sorted(names, key=sync.by_depth))
        self.assertEqual(["alpha", "mike", "zulu"], sorted(names, key=sync.by_depth))


class TestNeedsCopy(unittest.TestCase):

    def test_different_size_needs_a_copy(self):
        self.assertTrue(sync.needs_copy(10, 1000.0, 20, 1000.0))

    def test_identical_size_and_time_does_not(self):
        self.assertFalse(sync.needs_copy(10, 1000.0, 10, 1000.0))

    def test_small_time_difference_is_tolerated(self):
        # 1s of slack for filesystems with coarse timestamp granularity
        self.assertFalse(sync.needs_copy(10, 1000.0, 10, 1000.5))
        self.assertFalse(sync.needs_copy(10, 1000.5, 10, 1000.0))

    def test_large_time_difference_needs_a_copy(self):
        self.assertTrue(sync.needs_copy(10, 1000.0, 10, 1002.0))

    def test_missing_source_stat_sentinel_always_copies(self):
        # scan_source stores size -1 when it cannot stat, so the copy is
        # attempted and fails loudly instead of the file being dropped
        self.assertTrue(sync.needs_copy(-1, 0.0, 0, 0.0))


class TestLinkDetection(TreeCase):

    def test_plain_file_is_not_a_link(self):
        build(self.src, {"a.txt": "hello"})
        self.assertFalse(sync.is_link(self.src / "a.txt"))

    def test_plain_directory_is_not_a_link(self):
        build(self.src, {"sub/": DIR})
        self.assertFalse(sync.is_link(self.src / "sub"))

    def test_missing_path_is_not_a_link(self):
        self.assertFalse(sync.is_link(self.src / "nope"))

    @needs_junctions
    def test_junction_is_a_link(self):
        build(self.src, {"real/x.txt": "content"})
        make_junction(self.dest / "j", self.src / "real")
        self.assertTrue(sync.is_link(self.dest / "j"),
                        "a junction must count as a link, or sync walks into "
                        "it and deletes files outside the destination")

    @needs_junctions
    def test_entry_is_link_agrees_with_is_link(self):
        build(self.src, {"real/x.txt": "content", "plain.txt": "x"})
        make_junction(self.dest / "j", self.src / "real")
        with os.scandir(self.dest) as it:
            entries = {e.name: e for e in it}
        self.assertTrue(sync.entry_is_link(entries["j"]))
        with os.scandir(self.src) as it:
            entries = {e.name: e for e in it}
        self.assertFalse(sync.entry_is_link(entries["plain.txt"]))


class TestLstatInfo(TreeCase):

    def test_returns_size_time_and_link_flag(self):
        build(self.src, {"a.txt": "hello"})
        info = sync.lstat_info(self.src / "a.txt")
        self.assertIsNotNone(info)
        size, mtime, is_link = info
        self.assertEqual(5, size)
        self.assertGreater(mtime, 0)
        self.assertFalse(is_link)

    def test_returns_none_for_a_missing_path(self):
        self.assertIsNone(sync.lstat_info(self.src / "nope"))


if __name__ == "__main__":
    unittest.main()
