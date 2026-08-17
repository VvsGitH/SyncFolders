"""The two tree walkers: what they record, and what they refuse to walk into."""

import io
import unittest
from unittest import mock

import sync

from .support import (DIR, TreeCase, build, failing_scandir, make_junction,
                      needs_junctions, needs_symlinks)


def matcher(*patterns):
    return sync.Matcher(list(patterns))


class TestScanSource(TreeCase):

    def scan(self, *patterns, follow=False):
        return sync.scan_source(self.src, matcher(*patterns), follow)

    def test_records_files_with_size_and_time(self):
        build(self.src, {"a.txt": "hello"})
        dirs, files, links, errors, _ = self.scan()
        self.assertEqual(set(), dirs)
        self.assertEqual({"a.txt"}, set(files))
        path, size, mtime = files["a.txt"]
        self.assertEqual(5, size)
        self.assertGreater(mtime, 0)
        self.assertTrue(path.endswith("a.txt"))
        self.assertEqual(0, errors)

    def test_records_nested_paths_as_posix(self):
        build(self.src, {"a/b/c.txt": "x"})
        _, files, _, _, _ = self.scan()
        self.assertEqual({"a/b/c.txt"}, set(files))

    def test_keeps_empty_directories(self):
        build(self.src, {"empty/": DIR})
        dirs, files, _, _, _ = self.scan()
        self.assertEqual({"empty"}, dirs)
        self.assertEqual({}, files)

    def test_excluded_file_is_skipped(self):
        build(self.src, {"a.txt": "x", "b.log": "x"})
        _, files, _, _, _ = self.scan("*.log")
        self.assertEqual({"a.txt"}, set(files))

    def test_excluded_directory_is_never_walked_into(self):
        build(self.src, {"node_modules/deep/x.txt": "x", "keep.txt": "y"})
        dirs, files, _, _, _ = self.scan("node_modules/")
        self.assertEqual({"keep.txt"}, set(files))
        self.assertEqual(set(), dirs)

    def test_dir_only_pattern_leaves_a_file_of_that_name_alone(self):
        build(self.src, {"pkg.egg-info/x.txt": "x", "f.egg-info": "keep me"})
        dirs, files, _, _, _ = self.scan("*.egg-info/")
        self.assertEqual({"f.egg-info"}, set(files))
        self.assertEqual(set(), dirs)

    def test_unreadable_directory_is_counted_and_reported(self):
        build(self.src, {"good/a.txt": "x", "bad/b.txt": "y"})
        stderr = io.StringIO()
        with failing_scandir(self.src / "bad"), mock.patch("sys.stderr", stderr):
            _, files, _, errors, _ = self.scan()
        self.assertEqual(1, errors)
        self.assertIn("cannot read bad", stderr.getvalue())
        self.assertEqual({"good/a.txt"}, set(files))

    def test_unreadable_root_reports_the_dot(self):
        stderr = io.StringIO()
        with failing_scandir(self.src), mock.patch("sys.stderr", stderr):
            _, files, _, errors, _ = self.scan()
        self.assertEqual(1, errors)
        self.assertIn("cannot read .", stderr.getvalue())
        self.assertEqual({}, files)

    @needs_junctions
    def test_junction_in_the_source_is_walked_as_real_content(self):
        # deliberate asymmetry with the destination: a source junction is
        # content to copy, and recreating it as a link would need admin rights
        build(self.tmp, {"outside/x.txt": "from outside"})
        make_junction(self.src / "j", self.tmp / "outside")
        dirs, files, links, _, _ = self.scan()
        self.assertIn("j", dirs)
        self.assertIn("j/x.txt", files)
        self.assertEqual({}, links)

    @needs_junctions
    def test_a_junction_pointing_at_an_ancestor_is_not_walked(self):
        # the walk used to follow it until the path limit, recording ~60 nested
        # duplicates of the whole subtree and copying every one of them
        build(self.src, {"a.txt": "x", "sub/": DIR})
        make_junction(self.src / "sub" / "loop", self.src)
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            dirs, files, _, errors, _ = self.scan()
        self.assertEqual({"sub"}, dirs)
        self.assertEqual({"a.txt"}, set(files))
        self.assertEqual(1, errors)
        self.assertIn("link cycle, skipped: sub/loop", stderr.getvalue())

    @needs_symlinks
    def test_a_followed_symlink_cycle_is_not_walked(self):
        build(self.src, {"a.txt": "x", "sub/": DIR})
        (self.src / "sub" / "loop").symlink_to(self.src, target_is_directory=True)
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            dirs, files, _, errors, _ = self.scan(follow=True)
        self.assertEqual({"sub"}, dirs)
        self.assertEqual({"a.txt"}, set(files))
        self.assertEqual(1, errors)

    @needs_symlinks
    def test_a_symlink_to_a_sibling_folder_is_still_followed(self):
        # the cycle guard must only reject ancestors, not every link
        build(self.src, {"real/x.txt": "content", "sub/": DIR})
        (self.src / "sub" / "alias").symlink_to(self.src / "real",
                                                target_is_directory=True)
        dirs, files, _, errors, _ = self.scan(follow=True)
        self.assertEqual(0, errors)
        self.assertIn("sub/alias", dirs)
        self.assertIn("sub/alias/x.txt", files)


class TestScanDest(TreeCase):

    def scan(self, *patterns, protected=frozenset()):
        return sync.scan_dest(self.dest, matcher(*patterns), set(protected))

    def test_records_entries_with_size_time_and_link_flag(self):
        build(self.dest, {"a.txt": "hello", "sub/": DIR})
        dirs, entries, errors = self.scan()
        self.assertEqual({"sub"}, dirs)
        size, mtime, is_link = entries["a.txt"]
        self.assertEqual(5, size)
        self.assertFalse(is_link)
        self.assertEqual(0, errors)

    def test_excluded_entries_are_invisible(self):
        # the same matcher runs on both sides: what is not copied is also
        # not deleted
        build(self.dest, {"keep.txt": "x", "drop.log": "x",
                          "node_modules/deep/y.txt": "x"})
        dirs, entries, _ = self.scan("*.log", "node_modules/")
        self.assertEqual({"keep.txt"}, set(entries))
        self.assertEqual(set(), dirs)

    def test_protected_entries_are_invisible(self):
        build(self.dest, {"sync.toml": "x", "a.txt": "y"})
        _, entries, _ = self.scan(protected={"sync.toml"})
        self.assertEqual({"a.txt"}, set(entries))

    def test_unreadable_directory_is_counted(self):
        build(self.dest, {"bad/x.txt": "y"})
        with failing_scandir(self.dest / "bad"), mock.patch("sys.stderr", io.StringIO()):
            _, _, errors = self.scan()
        self.assertEqual(1, errors)

    @needs_junctions
    def test_junction_is_a_single_entry_and_is_not_walked_into(self):
        # the regression behind commit 57f3d91: walking into it made sync
        # delete files outside the destination
        build(self.tmp, {"outside/precious.txt": "do not delete"})
        make_junction(self.dest / "j", self.tmp / "outside")
        dirs, entries, _ = self.scan()
        self.assertEqual(set(), dirs)
        self.assertEqual({"j"}, set(entries))
        self.assertTrue(entries["j"][2], "the junction must be flagged as a link")
        self.assertNotIn("j/precious.txt", entries)


if __name__ == "__main__":
    unittest.main()
