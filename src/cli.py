"""
sync - Mirrors a source folder into the current folder.

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
import sys
from pathlib import Path

from config import CONFIG_NAME, confirm, create_config, load_config
from syncing import sync


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
