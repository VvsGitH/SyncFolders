"""Path helpers: link detection, resolution and sort keys."""

import os
import stat
import sys
from pathlib import Path


def resolve_path(raw: str, base: Path) -> Path:
    """Expands ~ and environment variables, then resolves against `base`."""
    p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def launcher_paths() -> list[Path]:
    """The tool's own files, which a run must never delete from a destination.

    Inside a zipapp `__file__` points *into* the archive
    (`...\\sync.pyz\\syncing.py`), a path that does not exist, so the only
    honest answer comes from argv[0]: the archive itself, plus the `sync.bat`
    sitting next to it. Both are needed -- with the dist folder on the PATH,
    running `sync` inside it would otherwise delete the wrapper and leave the
    tool unlaunchable.

    A path in the list that does not exist is harmless: it just hides an entry
    the destination does not hold.
    """
    raw = sys.argv[0]
    if not raw:
        return []
    try:
        app = Path(raw).resolve()
    except OSError:
        return []
    if app.suffix.lower() == ".pyz":
        return [app, app.with_name("sync.bat")]
    return [app]


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
