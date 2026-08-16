"""The mirror itself: the four phases, dry-run fidelity, and what is spared."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

import sync

from .support import (DIR, TreeCase, build, failing_scandir, make_junction,
                      needs_junctions, operations, snapshot, summary)


class SyncCase(TreeCase):
    """Runs sync() in-process and keeps its output for inspection."""

    def run_sync(self, *patterns, follow=False, dry_run=False, verbose=True):
        config = self.dest / "sync.toml"
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.sync(self.src, self.dest, sync.Matcher(list(patterns)),
                             follow, config, dry_run=dry_run, verbose=verbose)
        self.out = out.getvalue()
        return code

    def assertDest(self, expected, skip=("sync.toml",)):
        self.assertEqual(expected, snapshot(self.dest, skip=skip))


class TestMirroring(SyncCase):

    def test_copies_the_source_exactly(self):
        build(self.src, {"a.txt": "hello", "sub/b.txt": "world", "empty/": DIR})
        self.assertEqual(0, self.run_sync())
        self.assertDest({"a.txt": "hello", "sub/": DIR, "sub/b.txt": "world",
                         "empty/": DIR})

    def test_deletes_entries_missing_from_the_source(self):
        build(self.src, {"keep.txt": "x"})
        build(self.dest, {"stale.txt": "old", "gone/deep.txt": "old"})
        self.run_sync()
        self.assertDest({"keep.txt": "x"})

    def test_updates_a_changed_file(self):
        build(self.src, {"a.txt": "new content"})
        build(self.dest, {"a.txt": "old"})
        self.run_sync()
        self.assertDest({"a.txt": "new content"})
        self.assertIn("update a.txt", operations(self.out))

    def test_is_idempotent(self):
        build(self.src, {"a.txt": "x", "sub/b.txt": "y", "empty/": DIR})
        self.run_sync()
        first = snapshot(self.dest)
        self.run_sync()
        self.assertEqual(first, snapshot(self.dest))
        self.assertEqual("Copied 0, updated 0, folders created 0, links 0, "
                         "deleted 0, errors 0.", summary(self.out))

    def test_preserves_empty_directories(self):
        build(self.src, {"empty/": DIR, "nested/deep/": DIR})
        self.run_sync()
        self.assertDest({"empty/": DIR, "nested/": DIR, "nested/deep/": DIR})

    def test_replaces_a_destination_file_with_a_source_folder(self):
        build(self.src, {"x/c.txt": "content"})
        build(self.dest, {"x": "I am a file"})
        self.run_sync()
        self.assertDest({"x/": DIR, "x/c.txt": "content"})

    def test_replaces_a_destination_folder_with_a_source_file(self):
        build(self.src, {"x": "now a file"})
        build(self.dest, {"x/": DIR})
        self.run_sync()
        self.assertDest({"x": "now a file"})

    @unittest.expectedFailure
    def test_source_file_over_a_kept_destination_folder(self):
        """KNOWN BUG: the file lands *inside* the folder instead of failing.

        When the destination folder cannot be removed because it holds excluded
        entries, phase 3 calls shutil.copy2 with a directory as the target.
        copy2 does not refuse: it copies *into* the directory, so the mirror
        grows a spurious dest/x/x, reports 'updated 1, errors 0', and never
        converges -- every later run reports the same update again.

        Remove this decorator once phase 3 checks the target before copying.
        """
        build(self.src, {"x": "a file"})
        build(self.dest, {"x/keep.log": "excluded"})
        self.run_sync("*.log")
        self.assertNotIn("x/x", snapshot(self.dest))


class TestExclusions(SyncCase):

    def test_excluded_entries_are_not_copied(self):
        build(self.src, {"a.txt": "x", "b.log": "x", "node_modules/n.txt": "x"})
        self.run_sync("*.log", "node_modules/")
        self.assertDest({"a.txt": "x"})

    def test_excluded_entries_in_the_destination_are_not_deleted(self):
        # the symmetry that makes the tool safe to point at a working folder
        build(self.src, {"a.txt": "x"})
        build(self.dest, {"mine.log": "keep me", "node_modules/n.txt": "keep"})
        self.run_sync("*.log", "node_modules/")
        self.assertDest({"a.txt": "x", "mine.log": "keep me",
                         "node_modules/": DIR, "node_modules/n.txt": "keep"})

    def test_folder_holding_excluded_entries_is_kept(self):
        build(self.src, {"a.txt": "x"})
        build(self.dest, {"stale/keep.log": "excluded"})
        self.run_sync("*.log")
        self.assertDest({"a.txt": "x", "stale/": DIR,
                         "stale/keep.log": "excluded"})
        self.assertIn("folder not empty, kept: stale", self.out)


class TestProtectedEntries(SyncCase):

    def test_the_config_is_never_deleted(self):
        build(self.src, {"a.txt": "x"})
        (self.dest / "sync.toml").write_text("source = 'x'", encoding="utf-8")
        self.run_sync()
        self.assertTrue((self.dest / "sync.toml").exists())

    def test_a_config_outside_the_destination_is_not_protected(self):
        # protection is computed relative to dest: a config living elsewhere
        # simply has nothing to protect inside the tree
        build(self.src, {"a.txt": "x"})
        build(self.dest, {"stale.txt": "old"})
        out = io.StringIO()
        with redirect_stdout(out):
            sync.sync(self.src, self.dest, sync.Matcher([]), False,
                      self.tmp / "elsewhere.toml", dry_run=False, verbose=True)
        self.assertDest({"a.txt": "x"}, skip=())


class TestDryRun(SyncCase):

    def test_changes_nothing_on_disk(self):
        build(self.src, {"a.txt": "x", "sub/b.txt": "y"})
        build(self.dest, {"stale.txt": "old"})
        before = snapshot(self.dest)
        self.run_sync(dry_run=True)
        self.assertEqual(before, snapshot(self.dest))

    def test_prefixes_the_summary(self):
        build(self.src, {"a.txt": "x"})
        self.run_sync(dry_run=True)
        self.assertTrue(self.out.strip().splitlines()[-1].startswith("[dry-run]"))

    def test_reports_exactly_what_the_real_run_does(self):
        # a preview that does not match the run is worse than no preview
        build(self.src, {"a.txt": "x", "sub/b.txt": "y", "empty/": DIR})
        build(self.dest, {"stale.txt": "old", "gone/deep.txt": "old"})
        self.run_sync(dry_run=True)
        preview = operations(self.out)
        self.run_sync(dry_run=False)
        self.assertEqual(preview, operations(self.out))


class TestStatsAndExitCode(SyncCase):

    def test_counts_each_category(self):
        # the contents must differ in length: same size and a fresh mtime look
        # identical to needs_copy, which is the point of the 1s tolerance
        build(self.src, {"new.txt": "x", "changed.txt": "much longer now",
                         "d/": DIR})
        build(self.dest, {"changed.txt": "old", "stale.txt": "x"})
        self.run_sync()
        self.assertEqual("Copied 1, updated 1, folders created 1, links 0, "
                         "deleted 1, errors 0.", summary(self.out))

    def test_returns_one_when_a_folder_cannot_be_read(self):
        build(self.src, {"good.txt": "x", "bad/y.txt": "y"})
        with failing_scandir(self.src / "bad"), redirect_stderr(io.StringIO()):
            code = self.run_sync()
        self.assertEqual(1, code)
        self.assertIn("errors 1", summary(self.out))

    def test_quiet_mode_prints_only_the_summary(self):
        build(self.src, {"a.txt": "x"})
        self.run_sync(verbose=False)
        self.assertEqual([], operations(self.out))
        self.assertIn("Copied 1", self.out)


class TestJunctions(SyncCase):

    @needs_junctions
    def test_a_stale_junction_is_removed_without_touching_its_target(self):
        # commit 57f3d91: sync used to walk into the junction and delete the
        # linked-to files, outside the destination entirely
        build(self.src, {"a.txt": "x"})
        build(self.tmp, {"outside/precious.txt": "do not delete"})
        make_junction(self.dest / "j", self.tmp / "outside")
        self.run_sync()
        self.assertDest({"a.txt": "x"})
        self.assertEqual("do not delete",
                         (self.tmp / "outside" / "precious.txt").read_text(
                             encoding="utf-8"))

    @needs_junctions
    def test_a_junction_is_replaced_by_a_real_folder_when_the_source_has_one(self):
        build(self.src, {"j/real.txt": "real content"})
        build(self.tmp, {"outside/precious.txt": "do not delete"})
        make_junction(self.dest / "j", self.tmp / "outside")
        self.run_sync()
        self.assertDest({"j/": DIR, "j/real.txt": "real content"})
        self.assertTrue((self.tmp / "outside" / "precious.txt").exists())


if __name__ == "__main__":
    unittest.main()
