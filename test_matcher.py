#!/usr/bin/env python3
"""Differential test: Matcher's fast path must equal the plain regex loop.

`Matcher` short-circuits plain names and `*.ext` patterns into a set lookup and
an `str.endswith`, instead of running every rule through `re`. A mismatch there
would silently exclude or include files, so this compares it against the naive
implementation on every pattern/path/kind combination.

The oracle is built from `sync._parse` and `sync._build_regex`, i.e. from the
very code that generates the regexes. What is under test is the classification
and the dispatch, not the glob translation.

Usage: python test_matcher.py     (exit 0 = the two agree everywhere)
"""

import sys

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
    # anchored: must never take the fast path
    "/bin", "/bin/", "a/b", "a/b/", "**/x", "x/**", "a/**/b",
    # globs of every shape
    "*", "**", "*.*", "?abc", "a?c", "[ab]c", "[!a]bc", "[]]x", "x[",
    "*.egg-info/", "*node_modules", "node*modules", "**.log",
    # literals and case
    "Node_Modules/", "THUMBS.DB", "desktop.ini", "a.b.c",
    # lines that must be ignored
    "", "   ", "#comment", "# spaced comment", "/", "//", "///",
    # odd but legal
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


def paths():
    for prefix in PREFIXES:
        for name in NAMES:
            yield prefix + name


def check(patterns, label):
    fast = sync.Matcher(patterns)
    ref = ReferenceMatcher(patterns)
    failures = []
    checks = 0
    for rel in paths():
        for is_dir in (True, False):
            checks += 1
            got = fast(rel, is_dir)
            want = ref(rel, is_dir)
            if got != want:
                failures.append((label, patterns, rel, is_dir, got, want))
    return checks, failures


def check_coverage() -> list[str]:
    """The fast path must actually be taken, or equality proves nothing.

    Every shipped default is a plain name or a plain extension, so the regex
    lists must come out empty. If a future default needs real globbing this
    check is what will say so.
    """
    m = sync.Matcher(DEFAULT_PATTERNS)
    problems = []
    if m._re_any or m._re_dir:
        problems.append(
            f"{len(m._re_any) + len(m._re_dir)} default patterns still fall "
            f"back to a regex, the fast path is not covering them")
    if not m._lit_dir or not m._suf_any or not m._lit_any or not m._suf_dir:
        problems.append("a fast-path bucket is empty, the classifier is not "
                        "splitting the defaults as expected")
    # a pattern that genuinely needs globbing must NOT be short-circuited
    if not sync.Matcher(["a/b*/c"])._re_any:
        problems.append("an anchored glob was wrongly taken as a fast rule")
    return problems


def main() -> int:
    total = 0
    failures = []

    # the shipped configuration, as a whole
    n, f = check(DEFAULT_PATTERNS, "defaults")
    total += n
    failures += f

    # every pattern on its own, so a failure names the culprit directly
    for pattern in DEFAULT_PATTERNS + ADVERSARIAL_PATTERNS:
        n, f = check([pattern], f"single:{pattern!r}")
        total += n
        failures += f

    # mixed lists: fast-path and regex rules must coexist
    for i in range(0, len(ADVERSARIAL_PATTERNS), 4):
        chunk = ADVERSARIAL_PATTERNS[i:i + 4] + DEFAULT_PATTERNS[:6]
        n, f = check(chunk, f"mixed:{i}")
        total += n
        failures += f

    for label, patterns, rel, is_dir, got, want in failures[:20]:
        print(f"MISMATCH [{label}] rel={rel!r} is_dir={is_dir} "
              f"fast={got} regex={want} patterns={patterns!r}",
              file=sys.stderr)

    problems = check_coverage()
    for problem in problems:
        print(f"COVERAGE: {problem}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} mismatches out of {total} checks.",
              file=sys.stderr)
        return 1
    if problems:
        return 1
    print(f"OK: fast path matches the regex loop on all {total} checks, "
          f"and covers every shipped default.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
