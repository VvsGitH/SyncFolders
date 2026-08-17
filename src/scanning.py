"""The two walkers: one over the source, one over the destination.

Both are `os.scandir` with an explicit stack, so every entry is classified from
the `DirEntry` the OS already handed back.
"""

import os
import sys
from pathlib import Path

from matching import Matcher
from paths import _stat_is_link, entry_is_link


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
    """Returns (dirs, files, links, errors, unscanned), keyed by relative posix paths.

    `files` maps to (path, size, mtime), all captured during the scan, so the
    copy phase needs no further stat on the source.

    `unscanned` holds the folders the walk could not list (the root itself as
    `""`). What lives under them is unknown, not absent, and `sync()` needs to
    tell the two apart before it deletes anything.
    """
    dirs: set[str] = set()
    files: dict[str, tuple[str, int, float]] = {}
    links: dict[str, str] = {}
    unscanned: set[str] = set()
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
            unscanned.add(rel_root)
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
                        # not walked, so the same rule as an unreadable folder:
                        # the destination copy is unverified, not superfluous
                        unscanned.add(rel)
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

    return dirs, files, links, errors, unscanned


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
