#!/usr/bin/env python3
"""
sync.py - Mirrors a source folder into the current folder.

Meant to be installed on the PATH and invoked from any folder: the
destination is always the directory the terminal is currently in.

On the first run in a folder, if `sync.toml` does not exist, the CLI asks
for the source folder path and generates the file with the default
exclusions (build folders, dependencies and development metadata).

The result is an exact copy of the source, minus the exclusions: files
already present are replaced, files no longer present in the source are
deleted. Excluded entries are ignored in both directions: they are not
copied and, if present in the destination, they are not deleted either.
`sync.toml` is never deleted.

Usage:
    sync [-c CONFIG] [-n] [-q] [-y] [--init] [--source PATH]
"""

import argparse
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("Error: Python 3.11+ is required (module 'tomllib').")

SCRIPT_PATH = Path(__file__).resolve()
CONFIG_NAME = "sync.toml"

# Default exclusions, written to the toml generated on the first run.
DEFAULT_EXCLUDES: list[tuple[str, list[str]]] = [
    ("Scratch folder: put here whatever must never be copied", [
        "no_sync/",
    ]),
    ("Build output and artifacts", [
        "dist/",
        "build/",
        "out/",
        "output/",
        "target/",          # Rust, Maven
        "bin/",             # .NET, Go - remove if the source holds binaries to copy
        "obj/",             # .NET
        "Debug/",
        "Release/",
        "coverage/",
        "htmlcov/",
        "*.egg-info/",
        "DerivedData/",     # Xcode
    ]),
    ("Installed dependencies", [
        "node_modules/",
        "bower_components/",
        "jspm_packages/",
        "vendor/",          # Composer, Go
        "Pods/",            # CocoaPods
        "packages/",        # NuGet
    ]),
    ("Python virtual environments", [
        ".venv/",
        "venv/",
        "env/",
        ".env/",
        "virtualenv/",
    ]),
    ("Version control", [
        ".git/",
        ".svn/",
        ".hg/",
        ".bzr/",
    ]),
    ("Tool and framework caches", [
        "__pycache__/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        ".tox/",
        ".nox/",
        ".cache/",
        ".parcel-cache/",
        ".turbo/",
        ".next/",
        ".nuxt/",
        ".svelte-kit/",
        ".angular/",
        ".gradle/",
        ".terraform/",
        ".sass-cache/",
    ]),
    ("IDE settings and system metadata", [
        ".idea/",
        ".vs/",
        ".vscode/",
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
    ]),
    ("Temporary and compiled files", [
        "*.log",
        "*.tmp",
        "*.swp",
        "*.pyc",
        "*.pyo",
        "*.class",
        "*.o",
    ]),
]

CONFIG_HEADER = """\
# sync configuration for this folder.
# The destination is the folder holding this file.

# Source folder: absolute, or relative to this file.
# ~ and environment variables are supported.
source = {source}

# If true, symlinks are followed and copied as real files/folders.
# If false, they are recreated as symlinks (on Windows this may require
# developer mode or running as administrator).
follow_symlinks = {follow}

# Exclusion rules: glob patterns, .gitignore style.
#   *              any sequence, excluding "/"
#   ?              any single character, excluding "/"
#   **             any sequence, "/" included
#   [abc] [!abc]   character class
#   name/          matches folders only
#   /name          anchored to the source root
#   name           without "/", matches at any depth
#
# Excluded entries are not copied and, if present in the destination,
# they are not deleted either.
exclude = [
"""


# --------------------------------------------------------------------------
# Glob matching (.gitignore style)
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------

def resolve_path(raw: str, base: Path) -> Path:
    """Expands ~ and environment variables, then resolves against `base`."""
    p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _stat_is_link(st: os.stat_result) -> bool:
    """True for symlinks and, on Windows, for junctions/reparse points.

    `is_symlink()` returns False for a directory junction, so relying on it
    alone would let the walk descend into the junction and let the destination
    scan treat the linked-to folder as part of the destination.
    """
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def is_link(path: Path) -> bool:
    """`_stat_is_link` for a path we hold no directory entry for."""
    try:
        return _stat_is_link(path.lstat())
    except OSError:
        return False


def entry_is_link(entry: os.DirEntry) -> bool:
    """Same test on scandir's cached data, without an extra syscall.

    `is_symlink()` is free on both platforms (it comes from d_type on POSIX);
    the reparse-point check only matters on Windows, where scandir already
    carries `st_file_attributes` in the listing.
    """
    try:
        if entry.is_symlink():
            return True
        if os.name != "nt":
            return False
        return _stat_is_link(entry.stat(follow_symlinks=False))
    except OSError:
        return False


def overlaps(src: Path, dest: Path) -> bool:
    """True if source and destination are the same folder or nested."""
    return src == dest or src in dest.parents or dest in src.parents


def by_depth(rel: str) -> tuple[int, str]:
    """Sort key ordering folders by nesting level, then by name.

    The name breaks ties so the reported order does not depend on set
    iteration, which would make two runs print the same work differently.
    """
    return rel.count("/"), rel


# --------------------------------------------------------------------------
# Interactive configuration setup
# --------------------------------------------------------------------------

def render_config(source: str) -> str:
    parts = [CONFIG_HEADER.format(source=json.dumps(source), follow="false")]
    blocks = []
    for comment, patterns in DEFAULT_EXCLUDES:
        rows = [f"    # {comment}"]
        rows += [f"    {json.dumps(p)}," for p in patterns]
        blocks.append("\n".join(rows))
    parts.append("\n\n".join(blocks))
    parts.append("\n]\n")
    return "".join(parts)


def ask_source(dest: Path) -> str:
    """Asks the user for the source path and validates it."""
    if not sys.stdin.isatty():
        sys.exit(f"Error: '{CONFIG_NAME}' not found in {dest}. "
                 "Create it, or use --source in non-interactive mode.")
    print(f"No '{CONFIG_NAME}' in this folder.")
    print(f"Destination: {dest}")
    while True:
        try:
            raw = input("Source folder path: ")
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nAborted.")
        raw = raw.strip().strip('"').strip("'")
        if not raw:
            continue
        try:
            candidate = resolve_path(raw, dest)
        except OSError:
            print("  Invalid path, try again.")
            continue
        if not candidate.is_dir():
            print(f"  '{candidate}' is not an existing folder, try again.")
            continue
        if overlaps(candidate, dest):
            print("  Source and destination cannot be the same or nested.")
            continue
        return str(candidate)


def create_config(path: Path, dest: Path, source: str | None) -> None:
    if source is None:
        source = ask_source(dest)
    else:
        try:
            source = str(resolve_path(source, dest))
        except OSError:
            sys.exit(f"Error: invalid source path: {source}")
    try:
        path.write_text(render_config(source), encoding="utf-8")
    except OSError as exc:
        sys.exit(f"Error: cannot write {path} ({exc}).")
    print(f"Created {path} with the default exclusions.")
    print("Open it if you want to customize the rules.\n")


def confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


# --------------------------------------------------------------------------
# Configuration loading
# --------------------------------------------------------------------------

def load_config(path: Path, dest: Path) -> tuple[Path, Matcher, bool]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        sys.exit(f"Error: cannot read {path} ({exc}).")
    except tomllib.TOMLDecodeError as exc:
        sys.exit(f"Error: invalid TOML configuration ({exc}).")

    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        sys.exit("Error: the 'source' key is required and must be a string.")

    exclude = data.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(p, str) for p in exclude):
        sys.exit("Error: 'exclude' must be a list of strings.")

    follow = data.get("follow_symlinks", False)
    if not isinstance(follow, bool):
        sys.exit("Error: 'follow_symlinks' must be true or false.")

    try:
        src = resolve_path(source, path.parent)
    except OSError:
        sys.exit(f"Error: invalid source path: {source}")

    if not src.is_dir():
        sys.exit(f"Error: the source folder does not exist: {src}")
    if src == dest:
        sys.exit("Error: source and destination are the same.")
    if overlaps(src, dest):
        sys.exit("Error: source and destination cannot be nested.")

    return src, Matcher(exclude), follow


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def _listdir(root: str, rel: str) -> list[os.DirEntry] | None:
    """Directory entries of `root`, or None (reported) if it cannot be read.

    An unreadable folder is not fatal, but it must not pass unnoticed: the
    scan would look complete while missing whatever lives under it.
    """
    try:
        with os.scandir(root) as it:
            return list(it)
    except OSError as exc:
        print(f"  ! cannot read {rel or '.'}: {exc}", file=sys.stderr)
        return None


def scan_source(src: Path, excluded: Matcher, follow: bool):
    """Returns (dirs, files, links, errors) keyed by relative posix paths.

    `files` maps to (path, size, mtime), all captured during the scan, so the
    copy phase needs no further stat on the source.
    """
    dirs: set[str] = set()
    files: dict[str, tuple[str, int, float]] = {}
    links: dict[str, str] = {}
    errors = 0

    # Explicit stack rather than recursion: deep trees must not hit the
    # recursion limit. Traversal order is irrelevant, sync() sorts everything.
    # The third element is the real (link-free, case-normalised) path of the
    # folder being listed, carried along only to catch link cycles.
    stack: list[tuple[str, str, str]] = [
        (str(src), "", os.path.normcase(os.path.realpath(src)))]
    while stack:
        root, rel_root, real_root = stack.pop()
        listed = _listdir(root, rel_root)
        if listed is None:
            errors += 1
            continue

        prefix = rel_root + "/" if rel_root else ""
        for entry in listed:
            rel = prefix + entry.name
            try:
                is_dir = entry.is_dir()  # follows links, as os.walk did
            except OSError:
                is_dir = False
            if excluded(rel, is_dir):
                continue
            # is_symlink(), not entry_is_link(): a junction in the source stays
            # real content, so it is copied as a plain folder.
            if not follow and entry.is_symlink():
                try:
                    links[rel] = os.readlink(entry.path)
                except OSError as exc:
                    print(f"  ! cannot read the link {rel}: {exc}",
                          file=sys.stderr)
                    errors += 1
            elif is_dir:
                # A folder we descend into through a link may point back at one
                # of its own ancestors. Following it would walk the same subtree
                # again and again -- until the path limit, not forever, but long
                # enough to copy dozens of duplicates into the destination. Only
                # a link can close a cycle, so only links pay for the realpath.
                if entry_is_link(entry):
                    real = os.path.normcase(os.path.realpath(entry.path))
                    if real_root == real or real_root.startswith(real + os.sep):
                        print(f"  ! link cycle, skipped: {rel}", file=sys.stderr)
                        errors += 1
                        continue
                else:
                    real = real_root + os.sep + os.path.normcase(entry.name)
                dirs.add(rel)
                stack.append((entry.path, rel, real))
            else:
                try:
                    st = entry.stat()
                    files[rel] = (entry.path, st.st_size, st.st_mtime)
                except OSError:
                    # e.g. a broken symlink with follow_symlinks = true: keep it
                    # with a size that never matches, so the copy is attempted
                    # and fails loudly instead of silently dropping the file.
                    files[rel] = (entry.path, -1, 0.0)

    return dirs, files, links, errors


def entry_info(entry: os.DirEntry) -> tuple[int, float, bool]:
    """(size, mtime, is_link) from scandir's cached data."""
    is_link = entry_is_link(entry)
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return -1, 0.0, is_link
    return st.st_size, st.st_mtime, is_link


def lstat_info(path: Path) -> tuple[int, float, bool] | None:
    """Same triple for a path we hold no directory entry for, None if absent."""
    try:
        st = path.lstat()
    except OSError:
        return None
    return st.st_size, st.st_mtime, _stat_is_link(st)


def scan_dest(dest: Path, excluded: Matcher, protected: set[str]):
    """Returns (dirs, entries, errors) present in the destination.

    `entries` maps each relative path to (size, mtime, is_link), so the copy
    phase can decide without stat-ing the destination all over again.
    """
    dirs: set[str] = set()
    entries: dict[str, tuple[int, float, bool]] = {}
    errors = 0

    stack: list[tuple[str, str]] = [(str(dest), "")]
    while stack:
        root, rel_root = stack.pop()
        listed = _listdir(root, rel_root)
        if listed is None:
            errors += 1
            continue

        prefix = rel_root + "/" if rel_root else ""
        for entry in listed:
            rel = prefix + entry.name
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            if rel in protected or excluded(rel, is_dir):
                continue  # excluded: we leave it alone
            size, mtime, is_link = entry_info(entry)
            if is_dir and not is_link:
                dirs.add(rel)
                stack.append((entry.path, rel))
            else:
                # files, and links to either a file or a folder: a link is one
                # entry, never something we descend into
                entries[rel] = (size, mtime, is_link)

    return dirs, entries, errors


# --------------------------------------------------------------------------
# Synchronization
# --------------------------------------------------------------------------

def needs_copy(size: int, mtime: float, dst_size: int, dst_mtime: float) -> bool:
    """True when size or mtime differ, within a 1s tolerance on the mtime.

    Both sides come from their own scan, so an up-to-date run performs no stat
    of its own at all.
    """
    return size != dst_size or abs(mtime - dst_mtime) > 1


def sync(src: Path, dest: Path, excluded: Matcher, follow: bool,
         config_path: Path, dry_run: bool, verbose: bool) -> int:
    protected = set()
    for p in (SCRIPT_PATH, config_path):
        try:
            protected.add(p.resolve().relative_to(dest).as_posix())
        except (ValueError, OSError):
            pass

    src_dirs, src_files, src_links, src_errors = scan_source(src, excluded, follow)
    dst_dirs, dst_entries, dst_errors = scan_dest(dest, excluded, protected)

    wanted_entries = src_files.keys() | src_links.keys()
    stats = {"copied": 0, "updated": 0, "links": 0, "folders": 0,
             "deleted": 0, "errors": src_errors + dst_errors}

    def log(action: str, rel: str) -> None:
        if verbose:
            print(f"  {action:<12} {rel}")

    failed: set[str] = set()

    def fail(action: str, rel: str, exc: OSError) -> None:
        print(f"  ! cannot {action} {rel}: {exc}", file=sys.stderr)
        stats["errors"] += 1
        failed.add(rel)

    def blocked(rel: str) -> bool:
        """True when `rel` sits under an entry we could not remove.

        Writing there would go through a leftover link and land outside the
        destination, so those entries are skipped (the failure is already
        reported and reflected in the exit code).
        """
        return any(rel == f or rel.startswith(f + "/") for f in failed)

    # Entries that are gone once phase 1 is over. In a dry run nothing is
    # removed, so this is what lets the preview tell an empty folder from one
    # that will survive holding excluded or protected entries.
    removed: set[str] = set()

    def would_survive(rel: str) -> bool:
        """Dry-run stand-in for the failing rmdir: does anything stay behind?"""
        try:
            with os.scandir(dest / rel) as it:
                return any(rel + "/" + e.name not in removed for e in it)
        except OSError:
            return False

    # 1) Deletions (first, to clear file/folder conflicts)
    for rel in sorted(dst_entries.keys() - wanted_entries):
        target = dest / rel
        log("delete", rel)
        if not dry_run:
            try:
                target.unlink()
            except OSError as exc:
                fail("delete", rel, exc)
                continue
        removed.add(rel)
        stats["deleted"] += 1

    for rel in sorted(dst_dirs - src_dirs, key=by_depth, reverse=True):
        target = dest / rel
        log("delete dir", rel)
        if dry_run:
            kept = would_survive(rel)
        else:
            try:
                target.rmdir()
                kept = False
            except OSError:
                kept = True
        if kept:
            # not empty: it holds excluded or protected entries
            if verbose:
                print(f"  ~ folder not empty, kept: {rel}")
            continue
        removed.add(rel)
        stats["deleted"] += 1

    # 2) Folder creation (empty ones included; scan_dest already told us
    #    which ones exist, so no need to stat them again)
    for rel in sorted(src_dirs - dst_dirs, key=by_depth):
        if blocked(rel):
            continue
        target = dest / rel
        log("create dir", rel)
        if not dry_run:
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                fail("create", rel, exc)
                continue
        stats["folders"] += 1

    # 3) File copy
    for rel in sorted(src_files):
        if blocked(rel):
            continue
        if rel in protected:
            # The config and the script are spared from the deletion phase;
            # letting a source file of the same name land on top of them would
            # give the protection away, replacing the very sync.toml that drives
            # the run (the next one would then mirror a different source).
            if verbose:
                print(f"  ~ protected, not overwritten: {rel}")
            continue
        src_path, src_size, src_mtime = src_files[rel]
        target = dest / rel
        info = dst_entries.get(rel)
        in_the_way = False
        if info is None:
            # Not seen by the destination scan: absent, a real folder, or
            # protected. Only reached for entries we are about to write, so
            # this stat never shows up on an up-to-date run.
            info = lstat_info(target)
            # scan_dest files real folders under dst_dirs and never here, so
            # something still standing at this point is a folder phase 1 could
            # not remove. shutil.copy2 would not refuse it: given a directory
            # it copies *into* it, leaving the mirror with a file the source
            # does not have, and reporting success.
            in_the_way = (info is not None and not info[2] and target.is_dir())
        dst_is_link = info is not None and info[2]
        existed = info is not None and not dst_is_link
        if existed and not in_the_way \
                and not needs_copy(src_size, src_mtime, info[0], info[1]):
            continue
        log("update" if existed else "copy", rel)
        if in_the_way:
            fail("copy", rel, IsADirectoryError(
                21, "a folder is in the way and could not be removed"))
            continue
        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if dst_is_link:
                    target.unlink()
                shutil.copy2(src_path, target)
            except OSError as exc:
                fail("copy", rel, exc)
                continue
        stats["updated" if existed else "copied"] += 1

    # 4) Symlinks recreated as such
    for rel in sorted(src_links):
        if blocked(rel):
            continue
        if rel in protected:
            if verbose:
                print(f"  ~ protected, not overwritten: {rel}")
            continue
        target = dest / rel
        want = src_links[rel]
        target_is_link = is_link(target)
        if target_is_link:
            # `is_link` is true for every reparse point, and not all of them
            # answer readlink (cloud placeholders, app execution aliases): treat
            # an unreadable one as "not what we want" rather than crashing.
            try:
                current = os.readlink(target)
            except OSError:
                current = None
            if current == want:
                continue
        # A real folder still standing here is one phase 1 could not remove,
        # which only happens when it holds excluded or protected entries:
        # everything else under it was already deleted and the rmdir would
        # have succeeded. Clearing it to make room would delete exactly what
        # the exclusion promised to spare.
        in_the_way = not target_is_link and target.is_dir()
        log("link", rel)
        if in_the_way:
            fail("create link", rel, IsADirectoryError(
                21, "a folder is in the way and could not be removed"))
            continue
        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target_is_link:
                    # covers junctions too: unlink removes the link, not its target
                    target.unlink()
                elif target.exists():
                    target.unlink()
                os.symlink(want, target)
            except OSError as exc:
                fail("create link", rel, exc)
                continue
        stats["links"] += 1

    prefix = "[dry-run] " if dry_run else ""
    print(
        f"{prefix}Copied {stats['copied']}, updated {stats['updated']}, "
        f"folders created {stats['folders']}, links {stats['links']}, "
        f"deleted {stats['deleted']}, errors {stats['errors']}."
    )
    return 1 if stats["errors"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sync",
        description="Mirrors a source folder into the current folder.",
    )
    parser.add_argument("-c", "--config", type=Path, default=None,
                        help=f"configuration file (default: ./{CONFIG_NAME})")
    parser.add_argument("--source", default=None,
                        help="source to use when creating the configuration without a prompt")
    parser.add_argument("--init", action="store_true",
                        help="create (or regenerate) the configuration and exit")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="show the operations without performing them")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="print only the final summary")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="do not ask for confirmation on the first synchronization")
    args = parser.parse_args()

    dest = Path.cwd().resolve()
    config_path = (args.config if args.config else dest / CONFIG_NAME).resolve()

    if args.init:
        if config_path.is_file() and not confirm(f"{config_path} already exists. Overwrite?"):
            # Without a terminal `confirm` declines before printing anything, so
            # say why nothing happened instead of exiting 0 in silence.
            print(f"Kept the existing {config_path}.", file=sys.stderr)
            return 0
        create_config(config_path, dest, args.source)
        return 0

    first_run = not config_path.is_file()
    if first_run:
        create_config(config_path, dest, args.source)

    src, excluded, follow = load_config(config_path, dest)

    if not args.quiet:
        print(f"Source:      {src}")
        print(f"Destination: {dest}")

    if first_run and not args.yes and not args.dry_run:
        if not confirm("Proceed with the synchronization? Foreign files "
                       "in this folder will be deleted."):
            print("Aborted. Run 'sync' again whenever you want.")
            return 0

    return sync(src, dest, excluded, follow, config_path,
                dry_run=args.dry_run, verbose=not args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
