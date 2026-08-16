"""Glob matching: documented semantics, plus fast path == regex loop.

`Matcher` short-circuits plain names and `*.ext` patterns into a set lookup and
an `str.endswith`. A mismatch there would silently exclude or include files, so
the differential test below compares it against the naive regex loop over every
pattern/path/kind combination.
"""

import os
import unittest

import sync


class ReferenceMatcher:
    """The flat regex loop: one rule after another, no fast path."""

    def __init__(self, patterns):
        self._rules = []
        for pattern in patterns:
            parsed = sync._parse(pattern)
            if parsed is None:
                continue
            body, dir_only, anchored = parsed
            self._rules.append((sync._build_regex(body, anchored), dir_only))

    def __call__(self, rel, is_dir):
        for regex, dir_only in self._rules:
            if dir_only and not is_dir:
                continue
            if regex.match(rel):
                return True
        return False


DEFAULT_PATTERNS = [p for _, group in sync.DEFAULT_EXCLUDES for p in group]

ADVERSARIAL_PATTERNS = [
    "/bin", "/bin/", "a/b", "a/b/", "**/x", "x/**", "a/**/b",
    "*", "**", "*.*", "?abc", "a?c", "[ab]c", "[!a]bc", "[]]x", "x[",
    "*.egg-info/", "*node_modules", "node*modules", "**.log",
    "Node_Modules/", "THUMBS.DB", "desktop.ini", "a.b.c",
    "", "   ", "#comment", "# spaced comment", "/", "//", "///",
    "  spaced  ", "a\\b", "\\", "café/", "ÄÖÜ", "ß.log", "with space.txt",
    "-dash", "+plus", "(paren)", "a$b", "a^b", "a|b", "a.b",
]

NAMES = [
    "node_modules", "Node_Modules", "NODE_MODULES", "node_modulesx",
    "xnode_modules", ".git", ".GIT", "git", "dist", "build", "bin", "obj",
    "a.log", "A.LOG", ".log", "log", "x.log.bak", "x.tmp", "x.pyc", "x.o",
    "pkg.egg-info", "x.egg-info", "egg-info", "Thumbs.db", "THUMBS.DB",
    "desktop.ini", ".DS_Store", "a", "ab", "abc", "a.b.c", "café", "ÄÖÜ",
    "ß.log", "with space.txt", "-dash", "(paren)", "a$b", "a.b", "x[",
    "]", "[ab]c", "ac", "bc",
]

PREFIXES = ["", "sub/", "a/b/", "a/b/c/", "node_modules/", "dist/sub/"]

PATHS = [prefix + name for prefix in PREFIXES for name in NAMES]

WINDOWS = os.name == "nt"


class TestPatternSemantics(unittest.TestCase):
    """The syntax table documented in README.md."""

    def assertMatch(self, pattern, rel, is_dir=False):
        self.assertTrue(sync.Matcher([pattern])(rel, is_dir),
                        f"{pattern!r} should match {rel!r} (is_dir={is_dir})")

    def assertNoMatch(self, pattern, rel, is_dir=False):
        self.assertFalse(sync.Matcher([pattern])(rel, is_dir),
                         f"{pattern!r} should not match {rel!r} (is_dir={is_dir})")

    def test_bare_name_matches_at_any_depth(self):
        for rel in ("node_modules", "a/node_modules", "a/b/c/node_modules"):
            with self.subTest(rel=rel):
                self.assertMatch("node_modules", rel)

    def test_bare_name_does_not_match_a_substring(self):
        for rel in ("xnode_modules", "node_modulesx", "a/node_modules_x"):
            with self.subTest(rel=rel):
                self.assertNoMatch("node_modules", rel)

    def test_star_does_not_cross_a_slash(self):
        self.assertMatch("*.log", "a.log")
        self.assertMatch("*.log", "sub/a.log")
        self.assertNoMatch("a*c", "a/c")

    def test_double_star_crosses_slashes(self):
        self.assertMatch("a/**/b", "a/b")
        self.assertMatch("a/**/b", "a/x/b")
        self.assertMatch("a/**/b", "a/x/y/b")

    def test_question_mark_is_one_non_slash_character(self):
        self.assertMatch("a?c", "abc")
        self.assertNoMatch("a?c", "ac")
        self.assertNoMatch("a?c", "abbc")
        self.assertNoMatch("a?c", "a/c")

    def test_character_class(self):
        self.assertMatch("[ab]c", "ac")
        self.assertMatch("[ab]c", "bc")
        self.assertNoMatch("[ab]c", "cc")

    def test_negated_character_class(self):
        self.assertNoMatch("[!a]c", "ac")
        self.assertMatch("[!a]c", "bc")

    def test_trailing_slash_means_directories_only(self):
        self.assertMatch("build/", "build", is_dir=True)
        self.assertNoMatch("build/", "build", is_dir=False)

    def test_leading_slash_anchors_to_the_root(self):
        self.assertMatch("/bin", "bin")
        self.assertNoMatch("/bin", "a/bin")

    def test_pattern_with_inner_slash_is_anchored(self):
        self.assertMatch("a/b", "a/b")
        self.assertNoMatch("a/b", "x/a/b")

    def test_blank_lines_and_comments_are_ignored(self):
        for pattern in ("", "   ", "#comment", "# spaced", "/"):
            with self.subTest(pattern=pattern):
                matcher = sync.Matcher([pattern])
                self.assertFalse(matcher("anything", False))
                self.assertFalse(matcher("anything", True))

    def test_surrounding_whitespace_is_stripped(self):
        self.assertMatch("  build/  ", "build", is_dir=True)

    @unittest.skipUnless(WINDOWS, "matching is case-insensitive on Windows only")
    def test_case_insensitive_on_windows(self):
        self.assertMatch("node_modules/", "NODE_MODULES", is_dir=True)
        self.assertMatch("*.log", "A.LOG")

    @unittest.skipIf(WINDOWS, "matching is case-sensitive off Windows")
    def test_case_sensitive_elsewhere(self):
        self.assertNoMatch("node_modules/", "NODE_MODULES", is_dir=True)


class TestDefaultExcludes(unittest.TestCase):
    """The shipped exclude list, as users actually meet it."""

    def setUp(self):
        self.matcher = sync.Matcher(DEFAULT_PATTERNS)

    def test_dependency_and_vcs_folders_are_excluded(self):
        for name in ("node_modules", ".git", "dist", "build", "__pycache__",
                     ".venv", "target", ".idea"):
            with self.subTest(name=name):
                self.assertTrue(self.matcher(name, True))
                self.assertTrue(self.matcher("a/b/" + name, True))

    def test_temporary_files_are_excluded(self):
        for name in ("x.log", "x.tmp", "x.pyc", "x.o", "Thumbs.db", ".DS_Store"):
            with self.subTest(name=name):
                self.assertTrue(self.matcher(name, False))

    def test_egg_info_excludes_the_folder_but_not_a_file(self):
        # '*.egg-info/' is dir_only: a file with that name must be copied
        self.assertTrue(self.matcher("pkg.egg-info", True))
        self.assertFalse(self.matcher("pkg.egg-info", False))

    def test_ordinary_content_is_kept(self):
        for rel in ("a.txt", "src/main.py", "docs/readme.md", "logo.png"):
            with self.subTest(rel=rel):
                self.assertFalse(self.matcher(rel, False))


class TestFastPathEquivalence(unittest.TestCase):
    """The fast path must be indistinguishable from the regex loop."""

    def assertSameAsReference(self, patterns):
        fast = sync.Matcher(patterns)
        ref = ReferenceMatcher(patterns)
        for rel in PATHS:
            for is_dir in (True, False):
                if fast(rel, is_dir) != ref(rel, is_dir):
                    self.fail(f"patterns={patterns!r} rel={rel!r} "
                              f"is_dir={is_dir}: fast={fast(rel, is_dir)} "
                              f"regex={ref(rel, is_dir)}")

    def test_default_configuration(self):
        self.assertSameAsReference(DEFAULT_PATTERNS)

    def test_each_pattern_alone(self):
        for pattern in DEFAULT_PATTERNS + ADVERSARIAL_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertSameAsReference([pattern])

    def test_mixed_lists(self):
        for i in range(0, len(ADVERSARIAL_PATTERNS), 4):
            chunk = ADVERSARIAL_PATTERNS[i:i + 4] + DEFAULT_PATTERNS[:6]
            with self.subTest(chunk=chunk):
                self.assertSameAsReference(chunk)


class TestFastPathCoverage(unittest.TestCase):
    """Equality proves nothing if the fast path is never taken."""

    def test_defaults_need_no_regex_at_all(self):
        m = sync.Matcher(DEFAULT_PATTERNS)
        self.assertEqual([], m._re_any)
        self.assertEqual([], m._re_dir)

    def test_defaults_populate_every_bucket(self):
        m = sync.Matcher(DEFAULT_PATTERNS)
        self.assertTrue(m._lit_any)
        self.assertTrue(m._lit_dir)
        self.assertTrue(m._suf_any)
        self.assertTrue(m._suf_dir)

    def test_real_globs_still_become_regexes(self):
        for pattern in ("a/b*/c", "?abc", "[ab]c", "a/**/b", "/bin"):
            with self.subTest(pattern=pattern):
                m = sync.Matcher([pattern])
                self.assertTrue(m._re_any or m._re_dir,
                                f"{pattern!r} was wrongly taken as a fast rule")

    @unittest.skipUnless(WINDOWS, "the ASCII guard only applies when folding")
    def test_non_ascii_falls_back_to_regex_on_windows(self):
        # str.lower() does not reproduce re.IGNORECASE's Unicode case folding
        m = sync.Matcher(["café/"])
        self.assertTrue(m._re_dir)


if __name__ == "__main__":
    unittest.main()
