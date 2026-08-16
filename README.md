# sync

A single-file CLI that **mirrors a source folder into the current folder**.

One-way mirror: when it finishes, the destination is an exact copy of the source, minus the
exclusions. This is not a merge — files present in the destination but not in the source are
**deleted**.

Standard library only, no dependencies. Requires **Python 3.11+** (the `tomllib` module).

## Installation

Copy `sync.py` and `sync.bat` into the same folder, inside a directory on the Windows `PATH`.
From then on `sync` can be invoked from any directory: the destination is always the folder
the terminal is currently in.

`sync.bat` is a thin wrapper: it prefers the `py -3` launcher and falls back to `python` when
the launcher is not available.

On Linux/macOS the `.bat` is not needed: make `sync.py` executable (it already carries the
shebang) and link it into a folder on the `PATH`.

```sh
chmod +x sync.py
ln -s "$PWD/sync.py" ~/.local/bin/sync
```

## Usage

```
sync                      # synchronize (first run asks for source and confirmation)
sync --init --source PATH # only generate sync.toml, without synchronizing
sync -n                   # dry run: list the operations without performing them
sync -q                   # print only the final summary
sync -y                   # skip the first-run confirmation
sync -c other.toml        # use an alternative configuration
```

| Option | Description |
| --- | --- |
| `-c`, `--config PATH` | Configuration file (default: `./sync.toml`) |
| `--source PATH` | Source to use when creating the configuration without a prompt |
| `--init` | Create (or regenerate) the configuration and exit |
| `-n`, `--dry-run` | Show the operations without performing them |
| `-q`, `--quiet` | Print only the final summary |
| `-y`, `--yes` | Do not ask for confirmation on the first synchronization |

Exit code `1` if any I/O error occurred, `0` otherwise.

### First run

If no `sync.toml` exists in the current folder, the CLI asks for the source path and generates
the file with the default exclusions. Before synchronizing it asks for an explicit
confirmation, because the operation can delete files already present in the folder.

On every later run **no confirmation is asked**: the mirror proceeds directly. Use `-n` when
you want to check what would happen.

Without an interactive terminal (scripts, pipelines) the first run stops with an error: pass
`--source` to generate the configuration and `-y` to skip the confirmation.

## Configuration (`sync.toml`)

Every destination folder has its own `sync.toml`, which lives in the destination itself.

```toml
# Source folder: absolute, or relative to this file.
# ~ and environment variables are supported.
source = "C:\\path\\to\\the\\source"

# If true, symlinks are followed and copied as real files/folders.
# If false, they are recreated as symlinks (on Windows this may require
# developer mode or running as administrator).
follow_symlinks = false

exclude = [
    "node_modules/",
    "*.log",
]
```

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `source` | string | — | Required |
| `follow_symlinks` | boolean | `false` | |
| `exclude` | list of strings | `[]` | `.gitignore`-style globs |

Source and destination can be neither the same folder nor nested into one another: the check
runs both at the prompt and on every configuration load.

### Exclusion rules

Glob patterns, `.gitignore` style:

| Pattern | Meaning |
| --- | --- |
| `*` | Any sequence, excluding `/` |
| `?` | Any single character, excluding `/` |
| `**` | Any sequence, `/` included |
| `[abc]` `[!abc]` | Character class |
| `name/` | Matches folders only |
| `/name` | Anchored to the source root |
| `name` | Without `/`, matches at any depth |

Matching is case-insensitive on Windows. The `!pattern` negation of `.gitignore` is **not**
supported.

Excluded entries are ignored **in both directions**: they are not copied and, if present in
the destination, they are not deleted either.

### Default exclusions

The `sync.toml` generated on the first run ships with a commented set of exclusions:

- `no_sync/` — scratch folder, for whatever must never be copied
- build output (`dist/`, `build/`, `target/`, `bin/`, `obj/`, `coverage/`, …)
- installed dependencies (`node_modules/`, `vendor/`, `Pods/`, `packages/`, …)
- Python virtual environments (`.venv/`, `venv/`, `env/`, …)
- version control (`.git/`, `.svn/`, `.hg/`, `.bzr/`)
- tool and framework caches (`__pycache__/`, `.pytest_cache/`, `.next/`, `.gradle/`, …)
- IDE settings and system metadata (`.idea/`, `.vscode/`, `.DS_Store`, `Thumbs.db`, …)
- temporary and compiled files (`*.log`, `*.tmp`, `*.pyc`, `*.class`, `*.o`, …)

> If the source holds binaries that must be copied, remove `bin/` and `packages/` from the list.

## How the synchronization works

The order of operations is deliberate:

1. **Deletions** — foreign files first, then folders in order of decreasing depth. Doing this
   first clears file/folder conflicts. A folder that cannot be removed because it is not empty
   is kept: it holds excluded or protected entries.
2. **Folder creation** — empty ones included, so empty source directories are preserved.
3. **File copy** via `shutil.copy2` (metadata included). A file is skipped when size and
   modification time match, with a 1-second tolerance on the mtime for filesystems with coarse
   granularity. Content hashes are not compared.
4. **Symlinks** recreated as such when `follow_symlinks = false`.

The script itself and the `sync.toml` file are protected: they are never deleted, even when
they sit inside the destination folder.

I/O errors do not stop the run: they are printed to `stderr`, counted in the final summary and
they set the exit code to `1`.

A summary is printed at the end:

```
Copied 12, updated 3, folders created 5, links 0, deleted 2, errors 0.
```

In dry-run mode the line is prefixed with `[dry-run]`.

## Warnings

- **The operation is destructive.** Anything in the destination that is neither present in the
  source nor excluded gets deleted. The first time you use it in a new folder, always run
  `sync -n` to check.
- File comparison relies on size and mtime, not on content: a change that preserves both is
  not detected.
- On Windows, creating symlinks may require developer mode or running as administrator.
