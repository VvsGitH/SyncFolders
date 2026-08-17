"""The mirroring itself.

The order of the phases is deliberate: deletions -> folder creation -> file
copy -> symlinks. Deleting first clears file/folder conflicts.
"""

import os
import shutil
import sys
from pathlib import Path

from matching import Matcher
from paths import by_depth, is_link, launcher_paths
from scanning import lstat_info, scan_dest, scan_source


def needs_copy(size: int, mtime: float, dst_size: int, dst_mtime: float) -> bool:
    """True when size or mtime differ, within a 1s tolerance on the mtime.

    Both sides come from their own scan, so an up-to-date run performs no stat
    of its own at all.
    """
    return size != dst_size or abs(mtime - dst_mtime) > 1


def sync(src: Path, dest: Path, excluded: Matcher, follow: bool,
         config_path: Path, dry_run: bool, verbose: bool) -> int:
    protected = set()
    for p in (*launcher_paths(), config_path):
        try:
            protected.add(p.resolve().relative_to(dest).as_posix())
        except (ValueError, OSError):
            pass

    src_dirs, src_files, src_links, src_errors, unscanned = \
        scan_source(src, excluded, follow)
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

    def unverified(rel: str) -> bool:
        """True when `rel` sits under a source folder the scan never listed.

        Absent from `src_files` then means "we could not look", not "it is not
        there". Deleting on that basis would wipe the destination copies of
        files that are still perfectly present in the source, which is the one
        mistake a mirror must never make: the source is intact, the destination
        is not, and nothing brings it back. So the whole subtree is left as it
        stands -- stale at worst -- and the run exits 1 to say the mirror is
        incomplete.
        """
        return any(u == "" or rel == u or rel.startswith(u + "/")
                   for u in unscanned)

    def keep_unverified(rel: str) -> None:
        if verbose:
            print(f"  ~ source not scanned, kept: {rel}")

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
        if unverified(rel):
            keep_unverified(rel)
            continue
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
        if unverified(rel):
            keep_unverified(rel)
            continue
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
            # The config and the tool itself are spared from the deletion phase;
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
