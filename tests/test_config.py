"""Config generation, loading and validation, plus the interactive prompts."""

import io
import tomllib
import unittest
from unittest import mock

import sync

from .support import DIR, TreeCase, build


def tty(answers=()):
    """Patches stdin to look interactive and feed `answers` to input()."""
    stdin = mock.MagicMock()
    stdin.isatty.return_value = True
    return mock.patch("sys.stdin", stdin), mock.patch(
        "builtins.input", side_effect=list(answers))


class TestRenderConfig(unittest.TestCase):

    def setUp(self):
        self.text = sync.render_config(r"C:\some\source")
        self.data = tomllib.loads(self.text)

    def test_is_valid_toml(self):
        self.assertIn("source", self.data)

    def test_source_round_trips_including_backslashes(self):
        self.assertEqual(r"C:\some\source", self.data["source"])

    def test_follow_symlinks_defaults_to_false(self):
        self.assertIs(False, self.data["follow_symlinks"])

    def test_contains_every_default_exclusion(self):
        expected = [p for _, group in sync.DEFAULT_EXCLUDES for p in group]
        self.assertEqual(sorted(expected), sorted(self.data["exclude"]))

    def test_keeps_the_explanatory_comments(self):
        for _, comment in [(0, c) for c, _ in sync.DEFAULT_EXCLUDES]:
            self.assertIn(comment, self.text)


class TestLoadConfig(TreeCase):

    def load(self, body: str):
        path = self.dest / "sync.toml"
        path.write_text(body, encoding="utf-8")
        return sync.load_config(path, self.dest)

    def assertRejects(self, body: str, fragment: str):
        with self.assertRaises(SystemExit) as caught:
            self.load(body)
        self.assertIn(fragment, str(caught.exception))

    def test_accepts_a_valid_configuration(self):
        src, matcher, follow = self.load(
            f'source = {self.src.as_posix()!r}\n'
            'exclude = ["*.log"]\n'
            'follow_symlinks = true\n')
        self.assertEqual(self.src, src)
        self.assertIs(True, follow)
        self.assertTrue(matcher("a.log", False))

    def test_exclude_and_follow_are_optional(self):
        src, matcher, follow = self.load(f'source = {self.src.as_posix()!r}\n')
        self.assertEqual(self.src, src)
        self.assertIs(False, follow)
        self.assertFalse(matcher("anything", False))

    def test_source_relative_to_the_config_file(self):
        build(self.tmp, {"other/": DIR})
        src, _, _ = self.load('source = "../other"\n')
        self.assertEqual(self.tmp / "other", src)

    def test_rejects_malformed_toml(self):
        self.assertRejects("source = ", "invalid TOML")

    def test_rejects_missing_source(self):
        self.assertRejects('exclude = []\n', "'source' key is required")

    def test_rejects_non_string_source(self):
        self.assertRejects("source = 42\n", "'source' key is required")

    def test_rejects_blank_source(self):
        self.assertRejects('source = "   "\n', "'source' key is required")

    def test_rejects_non_list_exclude(self):
        self.assertRejects(f'source = {self.src.as_posix()!r}\nexclude = "*.log"\n',
                           "'exclude' must be a list of strings")

    def test_rejects_non_string_inside_exclude(self):
        self.assertRejects(f'source = {self.src.as_posix()!r}\nexclude = [1, 2]\n',
                           "'exclude' must be a list of strings")

    def test_rejects_non_boolean_follow_symlinks(self):
        self.assertRejects(
            f'source = {self.src.as_posix()!r}\nfollow_symlinks = "yes"\n',
            "'follow_symlinks' must be true or false")

    def test_rejects_a_source_that_does_not_exist(self):
        self.assertRejects(f'source = {(self.tmp / "nope").as_posix()!r}\n',
                           "source folder does not exist")

    def test_rejects_source_equal_to_destination(self):
        self.assertRejects(f'source = {self.dest.as_posix()!r}\n',
                           "source and destination are the same")

    def test_rejects_nested_source(self):
        build(self.dest, {"inner/": DIR})
        self.assertRejects(f'source = {(self.dest / "inner").as_posix()!r}\n',
                           "cannot be nested")

    def test_rejects_an_unreadable_config(self):
        with self.assertRaises(SystemExit) as caught:
            sync.load_config(self.dest / "missing.toml", self.dest)
        self.assertIn("cannot read", str(caught.exception))


class TestCreateConfig(TreeCase):

    def test_writes_a_loadable_config_from_an_explicit_source(self):
        path = self.dest / "sync.toml"
        with mock.patch("sys.stdout", new=io.StringIO()):
            sync.create_config(path, self.dest, str(self.src))
        src, _, _ = sync.load_config(path, self.dest)
        self.assertEqual(self.src, src)

    def test_resolves_a_relative_source_against_the_destination(self):
        build(self.tmp, {"other/": DIR})
        path = self.dest / "sync.toml"
        with mock.patch("sys.stdout", new=io.StringIO()):
            sync.create_config(path, self.dest, "../other")
        self.assertEqual(self.tmp / "other",
                         sync.load_config(path, self.dest)[0])

    def test_asks_when_no_source_is_given(self):
        path = self.dest / "sync.toml"
        stdin_patch, input_patch = tty([str(self.src)])
        with stdin_patch, input_patch, mock.patch("sys.stdout", new=io.StringIO()):
            sync.create_config(path, self.dest, None)
        self.assertEqual(self.src, sync.load_config(path, self.dest)[0])


class TestAskSource(TreeCase):

    def ask(self, answers):
        stdin_patch, input_patch = tty(answers)
        with stdin_patch, input_patch, mock.patch("sys.stdout", new=io.StringIO()):
            return sync.ask_source(self.dest)

    def test_accepts_a_valid_folder(self):
        self.assertEqual(str(self.src), self.ask([str(self.src)]))

    def test_strips_surrounding_quotes(self):
        self.assertEqual(str(self.src), self.ask([f'"{self.src}"']))

    def test_retries_on_a_missing_folder(self):
        self.assertEqual(str(self.src),
                         self.ask([str(self.tmp / "nope"), str(self.src)]))

    def test_retries_on_an_empty_answer(self):
        self.assertEqual(str(self.src), self.ask(["", str(self.src)]))

    def test_retries_when_source_and_destination_overlap(self):
        build(self.dest, {"inner/": DIR})
        self.assertEqual(str(self.src),
                         self.ask([str(self.dest / "inner"), str(self.src)]))

    def test_aborts_on_end_of_input(self):
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True
        with mock.patch("sys.stdin", stdin), \
                mock.patch("builtins.input", side_effect=EOFError), \
                mock.patch("sys.stdout", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                sync.ask_source(self.dest)

    def test_refuses_to_prompt_without_a_terminal(self):
        with mock.patch("sys.stdin", new=io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                sync.ask_source(self.dest)
        self.assertIn("--source", str(caught.exception))


class TestConfirm(unittest.TestCase):

    def answer(self, text):
        stdin_patch, input_patch = tty([text])
        with stdin_patch, input_patch:
            return sync.confirm("Proceed?")

    def test_accepts_y_and_yes(self):
        self.assertTrue(self.answer("y"))
        self.assertTrue(self.answer("yes"))
        self.assertTrue(self.answer("  YES  "))

    def test_rejects_everything_else(self):
        for text in ("", "n", "no", "s", "si", "maybe"):
            with self.subTest(text=text):
                self.assertFalse(self.answer(text))

    def test_is_false_without_a_terminal(self):
        with mock.patch("sys.stdin", new=io.StringIO()):
            self.assertFalse(sync.confirm("Proceed?"))

    def test_is_false_on_end_of_input(self):
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True
        with mock.patch("sys.stdin", stdin), \
                mock.patch("builtins.input", side_effect=KeyboardInterrupt):
            self.assertFalse(sync.confirm("Proceed?"))


if __name__ == "__main__":
    unittest.main()
