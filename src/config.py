"""The `sync.toml` configuration: template, interactive setup, loading."""

import json
import sys
from pathlib import Path

from matching import Matcher
from paths import overlaps, resolve_path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    sys.exit("Error: Python 3.11+ is required (module 'tomllib').")

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
