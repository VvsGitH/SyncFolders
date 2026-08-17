"""End to end: the CLI as a user meets it, in a real subprocess.

Only a subprocess can check what actually reaches stdout, stderr and the exit
code, which is the contract scripts depend on.
"""

import shutil
import subprocess
import sys
import unittest

from build import build as build_dist

from .support import (DIR, TreeCase, build, make_junction, needs_junctions,
                      operations, run_cli, snapshot, summary)


class CliCase(TreeCase):

    def init(self, *extra):
        """Creates the config non-interactively, without syncing."""
        result = run_cli(self.dest, "--init", "--source", str(self.src), *extra)
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def sync(self, *args):
        return run_cli(self.dest, *args)

    def assertDest(self, expected):
        self.assertEqual(expected, snapshot(self.dest, skip=("sync.toml",)))


class TestFullCycle(CliCase):

    def test_init_creates_a_config_without_syncing(self):
        build(self.src, {"a.txt": "hello"})
        result = self.init()
        self.assertIn("Created", result.stdout)
        self.assertTrue((self.dest / "sync.toml").is_file())
        self.assertDest({})  # nothing copied yet

    def test_first_sync_mirrors_the_source(self):
        build(self.src, {"a.txt": "hello", "sub/b.txt": "world", "empty/": DIR})
        build(self.dest, {"stale.txt": "old"})
        self.init()
        result = self.sync("-y")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertDest({"a.txt": "hello", "sub/": DIR, "sub/b.txt": "world",
                         "empty/": DIR})

    def test_second_run_is_a_no_op(self):
        build(self.src, {"a.txt": "hello", "sub/b.txt": "world"})
        self.init()
        self.sync("-y")
        result = self.sync()
        self.assertEqual(0, result.returncode)
        self.assertEqual("Copied 0, updated 0, folders created 0, links 0, "
                         "deleted 0, errors 0.", summary(result.stdout))

    def test_quiet_prints_only_the_summary(self):
        build(self.src, {"a.txt": "hello"})
        self.init()
        result = self.sync("-y", "-q")
        self.assertEqual([], operations(result.stdout))
        self.assertEqual(1, len(result.stdout.strip().splitlines()))

    def test_verbose_run_names_the_two_folders(self):
        build(self.src, {"a.txt": "hello"})
        self.init()
        result = self.sync("-y")
        self.assertIn(str(self.src), result.stdout)
        self.assertIn(str(self.dest), result.stdout)


class TestDryRun(CliCase):

    def test_touches_nothing_and_previews_the_real_run(self):
        build(self.src, {"a.txt": "hello", "sub/b.txt": "world"})
        build(self.dest, {"stale.txt": "old"})
        self.init()
        before = snapshot(self.dest)

        preview = self.sync("-n")
        self.assertEqual(before, snapshot(self.dest))
        self.assertTrue(summary(preview.stdout).startswith("[dry-run]"))

        real = self.sync("-y")
        self.assertEqual(operations(preview.stdout), operations(real.stdout))


class TestExclusions(CliCase):

    def test_default_exclusions_apply_in_both_directions(self):
        build(self.src, {"a.txt": "x", "node_modules/n.txt": "x",
                         "build/b.txt": "x", "trace.log": "x"})
        build(self.dest, {"mine.log": "keep me"})
        self.init()
        self.sync("-y")
        self.assertDest({"a.txt": "x", "mine.log": "keep me"})

    def test_dir_only_pattern_spares_a_file_of_the_same_name(self):
        build(self.src, {"pkg.egg-info/x.txt": "x", "f.egg-info": "keep me"})
        self.init()
        self.sync("-y")
        self.assertDest({"f.egg-info": "keep me"})


class TestProtectedEntries(CliCase):

    def test_the_config_survives_the_mirror(self):
        build(self.src, {"a.txt": "x"})
        self.init()
        self.sync("-y")
        self.assertTrue((self.dest / "sync.toml").is_file())

    def test_the_installed_tool_deletes_everything_but_itself(self):
        # the case the protection exists for: the dist folder on the PATH being
        # the folder the tool is mirroring into. Both files must survive -- the
        # wrapper too, or the next `sync` has nothing to launch.
        build(self.src, {"a.txt": "x"})
        app = build_dist(self.tmp / "dist")
        local_app = self.dest / app.name
        local_bat = self.dest / "sync.bat"
        shutil.copy2(app, local_app)
        shutil.copy2(app.with_name("sync.bat"), local_bat)
        run_cli(self.dest, "--init", "--source", str(self.src))
        result = subprocess.run([sys.executable, str(local_app), "-y"],
                                cwd=str(self.dest), capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(local_app.is_file(),
                        "sync.pyz deleted itself while mirroring")
        self.assertTrue(local_bat.is_file(),
                        "sync.bat, the wrapper that launched the run, was deleted")
        self.assertIn("a.txt", snapshot(self.dest))  # the mirror still happened


class TestConfigErrors(CliCase):

    def test_without_a_terminal_and_without_source_it_refuses(self):
        build(self.src, {"a.txt": "x"})
        result = self.sync()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--source", result.stderr)
        self.assertFalse((self.dest / "sync.toml").exists())

    def test_init_does_not_overwrite_without_confirmation(self):
        # confirm() returns False without a terminal, and it returns before
        # printing the prompt, so a scripted re-init is a silent no-op that
        # still exits 0. What matters is that the hand-edited config survives.
        build(self.src, {"a.txt": "x"})
        self.init()
        marker = "# hand written\n" + (self.dest / "sync.toml").read_text(
            encoding="utf-8")
        (self.dest / "sync.toml").write_text(marker, encoding="utf-8")
        result = self.init()
        self.assertEqual("", result.stdout.strip())
        self.assertTrue((self.dest / "sync.toml").read_text(
            encoding="utf-8").startswith("# hand written"))

    def test_an_invalid_config_stops_the_run(self):
        build(self.src, {"a.txt": "x"})
        build(self.dest, {"stale.txt": "keep, nothing must be deleted"})
        (self.dest / "sync.toml").write_text("source = 42\n", encoding="utf-8")
        result = self.sync()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("'source' key is required", result.stderr)
        self.assertEqual("keep, nothing must be deleted",
                         (self.dest / "stale.txt").read_text(encoding="utf-8"))

    def test_a_missing_source_stops_the_run(self):
        (self.dest / "sync.toml").write_text(
            f'source = "{(self.tmp / "nope").as_posix()}"\n', encoding="utf-8")
        result = self.sync()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not exist", result.stderr)

    def test_a_nested_source_stops_the_run(self):
        build(self.dest, {"inner/x.txt": "x"})
        (self.dest / "sync.toml").write_text(
            f'source = "{(self.dest / "inner").as_posix()}"\n', encoding="utf-8")
        result = self.sync()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("nested", result.stderr)


class TestFirstRunConfirmation(CliCase):

    def test_without_yes_and_without_a_terminal_it_aborts(self):
        # confirm() returns False without a tty, so the destructive first run
        # must not happen by accident in a pipeline
        build(self.src, {"a.txt": "x"})
        build(self.dest, {"stale.txt": "old"})
        result = run_cli(self.dest, "--source", str(self.src))
        self.assertEqual(0, result.returncode)
        self.assertIn("Aborted", result.stdout)
        self.assertEqual("old", (self.dest / "stale.txt").read_text(
            encoding="utf-8"))

    def test_dry_run_needs_no_confirmation(self):
        build(self.src, {"a.txt": "x"})
        result = run_cli(self.dest, "--source", str(self.src), "-n")
        self.assertEqual(0, result.returncode)
        self.assertTrue(summary(result.stdout).startswith("[dry-run]"))


class TestFailureReporting(CliCase):

    def test_a_blocking_folder_is_reported_and_exits_one(self):
        # the destination folder holds an excluded entry, so it cannot be
        # removed to make room for the source file of the same name
        build(self.src, {"x": "a file", "ok.txt": "copied anyway"})
        build(self.dest, {"x/keep.log": "excluded"})
        self.init()
        result = self.sync("-y")
        self.assertEqual(1, result.returncode)
        self.assertIn("cannot copy x", result.stderr)
        self.assertIn("errors 1", summary(result.stdout))
        # the rest of the mirror still went through
        self.assertDest({"ok.txt": "copied anyway", "x/": DIR,
                         "x/keep.log": "excluded"})


class TestJunctionRegression(CliCase):

    @needs_junctions
    def test_a_junction_in_the_destination_never_costs_outside_files(self):
        """The data-loss bug fixed in 57f3d91.

        Path.is_symlink() is False for a junction, so the walk descended into
        it and recorded the linked-to files as deletable. sync then deleted
        files outside the destination tree, reporting errors 0 and exit 0.
        """
        build(self.src, {"a.txt": "x"})
        build(self.tmp, {"outside/precious.txt": "must survive"})
        make_junction(self.dest / "j", self.tmp / "outside")
        self.init()
        result = self.sync("-y")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("must survive",
                         (self.tmp / "outside" / "precious.txt").read_text(
                             encoding="utf-8"))
        self.assertDest({"a.txt": "x"})
        self.assertIn("delete j", operations(result.stdout))

    @needs_junctions
    def test_a_junction_in_the_source_is_copied_as_a_real_folder(self):
        build(self.tmp, {"outside/x.txt": "content"})
        make_junction(self.src / "j", self.tmp / "outside")
        build(self.src, {"a.txt": "x"})
        self.init()
        self.sync("-y")
        self.assertDest({"a.txt": "x", "j/": DIR, "j/x.txt": "content"})


class TestCustomConfigPath(CliCase):

    def test_config_can_live_elsewhere(self):
        build(self.src, {"a.txt": "x"})
        other = self.tmp / "other.toml"
        result = run_cli(self.dest, "-c", str(other), "--source", str(self.src),
                         "--init")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(other.is_file())
        self.assertFalse((self.dest / "sync.toml").exists())
        run_cli(self.dest, "-c", str(other), "-y")
        self.assertEqual({"a.txt": "x"}, snapshot(self.dest))


if __name__ == "__main__":
    unittest.main()
