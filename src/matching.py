"""Glob matching, .gitignore style.

`_parse` + `_translate` + `_build_regex` implement the semantics by hand;
`Matcher` then short-circuits the shapes that need no globbing at all.
"""

import os
import re


def _translate(pattern: str) -> str:
    """Converts a glob pattern into a regex."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            if j - i >= 2:  # '**'
                if pattern[j:j + 1] == "/":
                    out.append(r"(?:.*/)?")
                    i = j + 1
                else:
                    out.append(r".*")
                    i = j
            else:
                out.append(r"[^/]*")
                i = j
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(r"\[")
                i += 1
            else:
                body = pattern[i + 1:j].replace("\\", "\\\\")
                if body[:1] in ("!", "^"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _parse(pattern: str) -> tuple[str, bool, bool] | None:
    """(body, dir_only, anchored), or None if the line must be ignored."""
    raw = pattern.strip()
    if not raw or raw.startswith("#"):
        return None
    dir_only = raw.endswith("/")
    raw = raw.rstrip("/")
    if raw.startswith("/"):
        anchored, raw = True, raw.lstrip("/")
    else:
        anchored = "/" in raw
    if not raw:
        return None
    return raw, dir_only, anchored


def _build_regex(body: str, anchored: bool):
    """Compiles a parsed pattern body into its matching regex."""
    regex = _translate(body)
    if not anchored:
        regex = r"(?:.*/)?" + regex
    flags = re.IGNORECASE if os.name == "nt" else 0
    return re.compile("^" + regex + "$", flags)


_META = re.compile(r"[*?\[\\]")


def _fast_rule(body: str, anchored: bool, fold: bool) -> tuple[str, str] | None:
    """('name', x) or ('suffix', x) when body needs no regex at all, else None.

    Only unanchored patterns qualify: for those `_build_regex` produces
    ^(?:.*/)?<body>$, which tests the last path segment and nothing else. So a
    body without metacharacters is an equality test on that segment, and a body
    that is `*` plus a plain tail is an endswith on it.

    Anything doubtful falls through to the regex, which is the behaviour this
    fast path has to reproduce exactly.
    """
    if anchored:
        return None
    if fold and not body.isascii():
        return None  # re.IGNORECASE case-folds further than str.lower()
    if not _META.search(body):
        return "name", body.lower() if fold else body
    if body[0] == "*" and not _META.search(body[1:]):
        tail = body[1:]
        return "suffix", tail.lower() if fold else tail
    return None


class Matcher:
    """Checks whether a relative (posix) path is excluded.

    Most real patterns (`node_modules/`, `.git/`, `*.log`) are plain names or
    extensions, so they are kept as a set lookup and an `str.endswith` over a
    tuple, both of which run in C. Only what genuinely needs globbing stays a
    regex. Rules are split by `dir_only` as well, keeping that test out of the
    hot loop.

    Evaluating the groups in any order is sound because exclusions are a plain
    OR: there is no `!pattern` negation. Adding one would make the semantics
    last-match-wins and this whole layout would have to be reworked.
    """

    def __init__(self, patterns: list[str]) -> None:
        self._fold = os.name == "nt"
        lit_any: set[str] = set()
        lit_dir: set[str] = set()
        suf_any: list[str] = []
        suf_dir: list[str] = []
        self._re_any: list = []
        self._re_dir: list = []

        for pattern in patterns:
            parsed = _parse(pattern)
            if parsed is None:
                continue
            body, dir_only, anchored = parsed
            fast = _fast_rule(body, anchored, self._fold)
            if fast is None:
                target = self._re_dir if dir_only else self._re_any
                target.append(_build_regex(body, anchored))
            elif fast[0] == "name":
                (lit_dir if dir_only else lit_any).add(fast[1])
            else:
                (suf_dir if dir_only else suf_any).append(fast[1])

        self._lit_any = lit_any
        self._lit_dir = lit_dir
        self._suf_any = tuple(suf_any)  # str.endswith walks a tuple in C
        self._suf_dir = tuple(suf_dir)

    def __call__(self, rel: str, is_dir: bool) -> bool:
        name = rel.rpartition("/")[2]
        if self._fold:
            name = name.lower()
        if name in self._lit_any or name.endswith(self._suf_any):
            return True
        for regex in self._re_any:
            if regex.match(rel):
                return True
        if is_dir:
            if name in self._lit_dir or name.endswith(self._suf_dir):
                return True
            for regex in self._re_dir:
                if regex.match(rel):
                    return True
        return False
