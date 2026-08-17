"""The mirror itself: the four phases, dry-run fidelity, and what is spared."""

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import matching
import syncing

from .support import (DIR, TreeCase, build, failing_scandir, failing_unlink,
                      make_junction, needs_junctions, needs_symlinks,
                      operations, snapshot, summary)


class SyncCase(TreeCase):
    """Runs sync() in-process and keeps its output for inspection."""

    def run_sync(self, *patterns, follow=False, dry_run=False, verbose=True):
        config = self.dest / "sync.toml"
        out = io.StringIO()
        with redirect_stdout(out):
            code = syncing.sync(self.src, self.dest, matching.Matcher(list(patterns)),
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

    def test_source_file_over_a_kept_destination_folder_is_an_error(self):
        """A folder phase 1 could not remove must not be copied into.

        shutil.copy2 given a directory copies *into* it, so this used to leave
        a spurious dest/x/x, report 'updated 1, errors 0', and never converge.
        """
        build(self.src, {"x": "a file"})
        build(self.dest, {"x/keep.log": "excluded"})
        with redirect_stderr(io.StringIO()):
            code = self.run_sync("*.log")

        self.assertEqual(1, code)
        self.assertIn("errors 1", summary(self.out))
        self.assertNotIn("x/x", snapshot(self.dest))
        # the excluded entry, and the folder holding it, are still spared
        self.assertDest({"x/": DIR, "x/keep.log": "excluded"})

    def test_the_blocking_folder_is_reported_in_dry_run_too(self):
        # a preview that hides the problem is worse than no preview
        build(self.src, {"x": "a file"})
        build(self.dest, {"x/keep.log": "excluded"})
        with redirect_stderr(io.StringIO()) as err:
            code = self.run_sync("*.log", dry_run=True)
        self.assertEqual(1, code)
        self.assertIn("cannot copy x", err.getvalue())
        self.assertEqual({"x/": DIR, "x/keep.log": "excluded"},
                         snapshot(self.dest))

    def test_a_file_replaces_an_empty_destination_folder(self):
        # the folder is removable, so this must still work
        build(self.src, {"x": "a file"})
        build(self.dest, {"x/": DIR})
        code = self.run_sync()
        self.assertEqual(0, code)
        self.assertDest({"x": "a file"})


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

    def test_a_source_file_never_overwrites_the_config(self):
        """Protection covers the write side too, not only the deletion.

        Mirroring a project that carries its own sync.toml used to replace the
        one driving the run, so the next run silently mirrored a different
        source into this folder.
        """
        build(self.src, {"sync.toml": 'source = "somewhere else"'})
        (self.dest / "sync.toml").write_text("source = 'mine'", encoding="utf-8")
        code = self.run_sync()
        self.assertEqual(0, code)
        self.assertEqual("source = 'mine'",
                         (self.dest / "sync.toml").read_text(encoding="utf-8"))
        self.assertIn("protected, not overwritten: sync.toml", self.out)

    def test_a_config_outside_the_destination_is_not_protected(self):
        # protection is computed relative to dest: a config living elsewhere
        # simply has nothing to protect inside the tree
        build(self.src, {"a.txt": "x"})
        build(self.dest, {"stale.txt": "old"})
        out = io.StringIO()
        with redirect_stdout(out):
            syncing.sync(self.src, self.dest, matching.Matcher([]), False,
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

    def test_a_folder_that_will_be_kept_is_previewed_as_kept(self):
        # nothing is deleted during a dry run, so the rmdir that fails in a real
        # run has to be simulated: otherwise the preview announced a deletion
        # that never happens and counted it in the summary
        build(self.src, {"a.txt": "x"})
        build(self.dest, {"stale/keep.log": "excluded", "empty/": DIR})
        self.run_sync("*.log", dry_run=True)
        preview, preview_summary = operations(self.out), summary(self.out)
        self.assertIn("~ folder not empty, kept: stale", preview)

        self.run_sync("*.log", dry_run=False)
        self.assertEqual(preview, operations(self.out))
        self.assertEqual(preview_summary.replace("[dry-run] ", ""),
                         summary(self.out))


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


class TestBlockedByAFailedDeletion(SyncCase):
    """An entry we could not delete must not be written through."""

    def test_nothing_is_written_below_an_undeletable_entry(self):
        # dest/x is a file the source has as a folder. If the deletion fails,
        # creating dest/x/ and copying into it would either fail confusingly or,
        # worse, follow whatever is still standing there.
        build(self.src, {"x/c.txt": "content", "ok.txt": "unrelated"})
        build(self.dest, {"x": "cannot be removed"})
        with failing_unlink(self.dest / "x"), redirect_stderr(io.StringIO()) as err:
            code = self.run_sync()

        self.assertEqual(1, code)
        self.assertIn("cannot delete x", err.getvalue())
        self.assertIn("errors 1", summary(self.out))
        # the blocked subtree is skipped entirely, the rest still goes through
        self.assertDest({"x": "cannot be removed", "ok.txt": "unrelated"})
        self.assertNotIn("create dir x", operations(self.out))
        self.assertNotIn("copy x/c.txt", operations(self.out))


class TestUnscannedSource(SyncCase):
    """A source folder we could not list must not cause any deletion.

    "Missing from the source" and "we could not look" produce the same empty
    scan, and only the second one must never be acted on: the source is intact,
    so deleting the destination copies loses the only affected replica.
    """

    def test_the_destination_copy_survives_an_unreadable_source_folder(self):
        build(self.src, {"docs/": DIR, "top.txt": "x"})
        build(self.dest, {"docs/important.txt": "irreplaceable",
                          "docs/deep/also.txt": "irreplaceable too"})
        with failing_scandir(self.src / "docs"), \
                redirect_stderr(io.StringIO()) as err:
            code = self.run_sync()

        self.assertEqual(1, code)
        self.assertIn("cannot read docs", err.getvalue())
        self.assertDest({"top.txt": "x", "docs/": DIR,
                         "docs/important.txt": "irreplaceable",
                         "docs/deep/": DIR,
                         "docs/deep/also.txt": "irreplaceable too"})
        self.assertIn("~ source not scanned, kept: docs/important.txt",
                      operations(self.out))
        self.assertIn("deleted 0", summary(self.out))

    def test_an_unreadable_root_deletes_nothing_at_all(self):
        # the worst case: the whole scan comes back empty, and the mirror used
        # to read that as "the source is empty, wipe the destination"
        build(self.dest, {"a.txt": "keep", "sub/b.txt": "keep too"})
        with failing_scandir(self.src), redirect_stderr(io.StringIO()):
            code = self.run_sync()

        self.assertEqual(1, code)
        self.assertDest({"a.txt": "keep", "sub/": DIR, "sub/b.txt": "keep too"})
        self.assertIn("deleted 0", summary(self.out))

    def test_the_rest_of_the_mirror_still_syncs(self):
        # the guard is scoped to the unscanned subtree, not the whole run
        build(self.src, {"docs/": DIR, "new.txt": "fresh"})
        build(self.dest, {"docs/keep.txt": "x", "stale.txt": "old"})
        with failing_scandir(self.src / "docs"), redirect_stderr(io.StringIO()):
            self.run_sync()
        self.assertDest({"docs/": DIR, "docs/keep.txt": "x",
                         "new.txt": "fresh"})
        self.assertIn("delete stale.txt", operations(self.out))

    def test_a_dry_run_previews_the_same_restraint(self):
        build(self.src, {"docs/": DIR})
        build(self.dest, {"docs/important.txt": "x"})
        with failing_scandir(self.src / "docs"), redirect_stderr(io.StringIO()):
            self.run_sync(dry_run=True)
        self.assertNotIn("delete docs/important.txt", operations(self.out))
        self.assertIn("deleted 0", summary(self.out))

    @needs_junctions
    def test_a_skipped_link_cycle_deletes_nothing_either(self):
        # the cycle guard leaves the subtree unwalked, which is the same
        # situation: unverified, not superfluous
        build(self.src, {"sub/": DIR})
        make_junction(self.src / "sub" / "loop", self.src)
        build(self.dest, {"sub/loop/old.txt": "was mirrored before"})
        with redirect_stderr(io.StringIO()):
            self.run_sync()
        self.assertEqual("was mirrored before",
                         (self.dest / "sub/loop/old.txt").read_text())


class TestSymlinks(SyncCase):
    """follow_symlinks = false: links are recreated, not followed."""

    def link_to(self, path: Path, target: Path, is_dir=False):
        path.symlink_to(target, target_is_directory=is_dir)

    @needs_symlinks
    def test_a_file_link_is_recreated_as_a_link(self):
        build(self.src, {"real.txt": "content"})
        self.link_to(self.src / "alias.txt", self.src / "real.txt")
        self.run_sync()
        self.assertTrue((self.dest / "alias.txt").is_symlink())
        self.assertEqual(os.readlink(self.src / "alias.txt"),
                         os.readlink(self.dest / "alias.txt"))

    @needs_symlinks
    def test_an_unchanged_link_is_left_alone(self):
        build(self.src, {"real.txt": "content"})
        self.link_to(self.src / "alias.txt", self.src / "real.txt")
        self.run_sync()
        self.run_sync()
        self.assertEqual([], operations(self.out))

    @needs_symlinks
    def test_a_retargeted_link_is_replaced(self):
        build(self.src, {"a.txt": "a", "b.txt": "b"})
        self.link_to(self.src / "alias.txt", self.src / "a.txt")
        self.run_sync()
        (self.src / "alias.txt").unlink()
        self.link_to(self.src / "alias.txt", self.src / "b.txt")
        self.run_sync()
        self.assertEqual(os.readlink(self.src / "alias.txt"),
                         os.readlink(self.dest / "alias.txt"))

    @needs_symlinks
    def test_a_link_replaces_a_plain_destination_file(self):
        build(self.src, {"real.txt": "content"})
        self.link_to(self.src / "alias.txt", self.src / "real.txt")
        build(self.dest, {"alias.txt": "I am a plain file"})
        self.run_sync()
        self.assertTrue((self.dest / "alias.txt").is_symlink())

    @needs_symlinks
    def test_a_stale_link_in_the_destination_is_deleted(self):
        build(self.src, {"a.txt": "x"})
        build(self.dest, {"real.txt": "content"})
        self.link_to(self.dest / "stale.txt", self.dest / "real.txt")
        self.run_sync()
        self.assertFalse((self.dest / "stale.txt").exists())
        self.assertFalse((self.dest / "stale.txt").is_symlink())

    @needs_symlinks
    def test_following_symlinks_copies_real_content(self):
        build(self.src, {"real.txt": "content"})
        self.link_to(self.src / "alias.txt", self.src / "real.txt")
        self.run_sync(follow=True)
        self.assertFalse((self.dest / "alias.txt").is_symlink())
        self.assertEqual("content",
                         (self.dest / "alias.txt").read_text(encoding="utf-8"))

    @needs_symlinks
    def test_a_link_never_costs_an_excluded_entry(self):
        """Phase 4 must not rmtree a folder phase 1 deliberately kept.

        The destination folder holds an excluded entry, so phase 1 left it
        standing. Wiping it here to make room for the link would delete
        exactly what the exclusion promised to spare.
        """
        build(self.src, {"real.txt": "content"})
        self.link_to(self.src / "x", self.src / "real.txt")
        build(self.dest, {"x/keep.log": "excluded, must survive"})
        with redirect_stderr(io.StringIO()):
            code = self.run_sync("*.log")
        self.assertEqual("excluded, must survive",
                         (self.dest / "x" / "keep.log").read_text(encoding="utf-8"))
        self.assertEqual(1, code)


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
