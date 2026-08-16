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


def _compile(pattern: str):
    """Returns (regex, dir_only), or None if the line must be ignored."""
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
    regex = _translate(raw)
    if not anchored:
        regex = r"(?:.*/)?" + regex
    flags = re.IGNORECASE if os.name == "nt" else 0
    return re.compile("^" + regex + "$", flags), dir_only


class Matcher:
    """Checks whether a relative (posix) path is excluded."""

    def __init__(self, patterns: list[str]) -> None:
        self._rules = [r for r in (_compile(p) for p in patterns) if r]

    def __call__(self, rel: str, is_dir: bool) -> bool:
        for regex, dir_only in self._rules:
            if dir_only and not is_dir:
                continue
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


def is_link(path: Path) -> bool:
    """True for symlinks and, on Windows, for junctions/reparse points.

    `Path.is_symlink()` returns False for a directory junction, so relying on
    it alone would let `os.walk` descend into the junction and let the
    destination scan treat the linked-to folder as part of the destination.
    """
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def overlaps(src: Path, dest: Path) -> bool:
    """True if source and destination are the same folder or nested."""
    return src == dest or src in dest.parents or dest in src.parents


def depth(rel: str) -> int:
    """Nesting level of a relative posix path, used to order folder work."""
    return rel.count("/")


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

def scan_source(src: Path, excluded: Matcher, follow: bool):
    """Returns (dirs, files, links) keyed by relative posix paths."""
    dirs: set[str] = set()
    files: dict[str, Path] = {}
    links: dict[str, str] = {}

    for root, dirnames, filenames in os.walk(src, topdown=True, followlinks=follow):
        root_path = Path(root)
        rel_root = root_path.relative_to(src)
        kept: list[str] = []
        for name in sorted(dirnames):
            rel = (rel_root / name).as_posix()
            if excluded(rel, True):
                continue
            full = root_path / name
            if not follow and full.is_symlink():
                links[rel] = os.readlink(full)
                continue
            dirs.add(rel)
            kept.append(name)
        dirnames[:] = kept

        for name in sorted(filenames):
            rel = (rel_root / name).as_posix()
            if excluded(rel, False):
                continue
            full = root_path / name
            if not follow and full.is_symlink():
                links[rel] = os.readlink(full)
            else:
                files[rel] = full

    return dirs, files, links


def scan_dest(dest: Path, excluded: Matcher, protected: set[str]):
    """Returns (dirs, entries) present in the destination and manageable."""
    dirs: set[str] = set()
    entries: set[str] = set()

    for root, dirnames, filenames in os.walk(dest, topdown=True):
        root_path = Path(root)
        rel_root = root_path.relative_to(dest)
        kept: list[str] = []
        for name in sorted(dirnames):
            rel = (rel_root / name).as_posix()
            if rel in protected or excluded(rel, True):
                continue  # excluded: we leave it alone
            full = root_path / name
            if is_link(full):
                entries.add(rel)  # link to a folder: handled as a single entry
                continue
            dirs.add(rel)
            kept.append(name)
        dirnames[:] = kept

        for name in sorted(filenames):
            rel = (rel_root / name).as_posix()
            if rel in protected or excluded(rel, False):
                continue
            entries.add(rel)

    return dirs, entries


# --------------------------------------------------------------------------
# Synchronization
# --------------------------------------------------------------------------

def needs_copy(src_file: Path, dst_stat: os.stat_result) -> bool:
    """True when size or mtime differ; `dst_stat` is the destination lstat."""
    try:
        s = src_file.stat()
    except OSError:
        return True
    return s.st_size != dst_stat.st_size or abs(s.st_mtime - dst_stat.st_mtime) > 1


def sync(src: Path, dest: Path, excluded: Matcher, follow: bool,
         config_path: Path, dry_run: bool, verbose: bool) -> int:
    protected = set()
    for p in (SCRIPT_PATH, config_path):
        try:
            protected.add(p.resolve().relative_to(dest).as_posix())
        except (ValueError, OSError):
            pass

    src_dirs, src_files, src_links = scan_source(src, excluded, follow)
    dst_dirs, dst_entries = scan_dest(dest, excluded, protected)

    wanted_entries = src_files.keys() | src_links.keys()
    stats = {"copied": 0, "updated": 0, "links": 0, "folders": 0,
             "deleted": 0, "errors": 0}

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

    # 1) Deletions (first, to clear file/folder conflicts)
    for rel in sorted(dst_entries - wanted_entries):
        target = dest / rel
        log("delete", rel)
        if not dry_run:
            try:
                target.unlink()
            except OSError as exc:
                fail("delete", rel, exc)
                continue
        stats["deleted"] += 1

    for rel in sorted(dst_dirs - src_dirs, key=depth, reverse=True):
        target = dest / rel
        log("delete dir", rel)
        if not dry_run:
            try:
                target.rmdir()
            except OSError:
                # not empty: it holds excluded or protected entries
                if verbose:
                    print(f"  ~ folder not empty, kept: {rel}")
                continue
        stats["deleted"] += 1

    # 2) Folder creation (empty ones included; scan_dest already told us
    #    which ones exist, so no need to stat them again)
    for rel in sorted(src_dirs - dst_dirs, key=depth):
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
        source_file = src_files[rel]
        target = dest / rel
        try:
            dst_stat = os.lstat(target)
        except OSError:
            dst_stat = None
        dst_is_link = dst_stat is not None and stat.S_ISLNK(dst_stat.st_mode)
        existed = dst_stat is not None and not dst_is_link
        if existed and not needs_copy(source_file, dst_stat):
            continue
        log("update" if existed else "copy", rel)
        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if dst_is_link:
                    target.unlink()
                shutil.copy2(source_file, target)
            except OSError as exc:
                fail("copy", rel, exc)
                continue
        stats["updated" if existed else "copied"] += 1

    # 4) Symlinks recreated as such
    for rel in sorted(src_links):
        if blocked(rel):
            continue
        target = dest / rel
        want = src_links[rel]
        if target.is_symlink() and os.readlink(target) == want:
            continue
        log("link", rel)
        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if is_link(target):
                    # covers junctions too: unlink removes the link, not its target
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
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
