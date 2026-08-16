# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`sync` is a single-file CLI that mirrors a source folder into the current working directory.
One-way mirror, not a merge: files in the destination that are not in the source (and not
excluded) get **deleted**. Read `README.md` for the user-facing behavior — do not duplicate
that content here.

## Layout

| File | Role |
| --- | --- |
| `sync.py` | The entire tool. All logic lives here. |
| `sync.bat` | Windows wrapper; prefers the `py -3` launcher, falls back to `python`. |
| `tests/` | Unit and end-to-end suite. Not shipped: `sync.py` + `sync.bat` are what goes on the `PATH`. |
| `README.md` | User documentation. |

`sync.py` is organized in commented sections: glob matching, interactive config setup, config
loading, scanning, synchronization, `main()`. Keep new code inside the matching section.

## Hard constraints

- **Standard library only.** No dependencies, ever — the tool is meant to be dropped onto the
  `PATH` as two files. Do not add a `requirements.txt`, a `pyproject.toml`, or a package layout
  unless explicitly asked.
- **Single file.** `sync.py` must stay self-contained and directly runnable; do not split it
  into modules.
- **Python 3.11+** is the floor (`tomllib`). Native `str | None` / `list[tuple[...]]` hints are
  used directly; no `from __future__ import annotations` is needed at that floor.
- **Everything in English**: code, comments, docstrings, CLI output, config template, docs.
  The project was translated from Italian; do not reintroduce Italian strings.

## Conventions

- PEP 8, 4-space indent, formatted with `autopep8` (see commit `5fae635`).
- Errors that must stop the run use `sys.exit("Error: ...")`. Per-entry I/O failures do **not**
  stop the run: call the `fail()` helper inside `sync()` (prints `  ! ...` to `stderr` and
  increments `stats["errors"]`), then `continue`. The exit code is `1` when `stats["errors"]`
  is non-zero.
- Relative paths are handled as **posix strings** (`Path.as_posix()`) everywhere, on every
  platform. Exclusion matching, the scan dicts, and the `protected` set all rely on this.
- User-visible progress goes through the local `log()` helper in `sync()`, which is silenced by
  `--quiet`. Every mutation must be wrapped in `if not dry_run:` and still counted in `stats`,
  so `-n` reports exactly what a real run would do.

## Things that are easy to break

- **Operation order in `sync()`** is deliberate: deletions → folder creation → file copy →
  symlinks. Deleting first clears file/folder conflicts. Folders are deleted deepest-first and
  created shallowest-first. Do not reorder.
- **`protected`** holds `SCRIPT_PATH` and the config file, relative to the destination. It is
  what stops the tool from deleting itself or its own `sync.toml`. Any new deletion path must
  honor it.
- **Exclusions are symmetric**: an excluded entry is neither copied nor deleted. `scan_dest()`
  applies the same `Matcher` as `scan_source()` — that is the mechanism, not an accident.
- **A failing `rmdir` is expected**, not an error: it means the folder still holds excluded or
  protected entries and must be kept. It is intentionally not counted in `stats["errors"]`.
- `_parse()` + `_translate()` + `_build_regex()` implement `.gitignore` semantics by hand
  (anchoring, `dir_only`, `IGNORECASE` on Windows only). `Matcher` then short-circuits the
  common shapes: an unanchored pattern with no metacharacters is a set lookup on the last path
  segment, and `*` plus a plain tail is an `str.endswith`. Only what genuinely needs globbing
  stays a regex — with the shipped defaults, **none of it does**. `_fast_rule()` must stay
  conservative: anything doubtful falls back to the regex.
- Negation (`!pattern`) is **not** supported, and that is load-bearing here, not just a missing
  feature: exclusions being a plain OR is what makes it legal for `Matcher.__call__` to check
  its buckets in any order. Adding negation means last-match-wins semantics and a rewrite of
  the whole layout, not just a new branch.
- Any change to the matcher must keep `tests/test_matching.py` green. It compares the fast path
  against the plain regex loop over every pattern/path/kind combination and checks that the
  fast path is actually being taken — equality alone would also hold if everything silently
  fell back to regex.
- `needs_copy()` compares size and mtime with a 1-second tolerance, never content hashes.
  **Both sides come from their scan**, not from a fresh stat, so an up-to-date run issues no
  stat of its own. The consequence is deliberate: the run works on a snapshot, and a source
  file modified mid-run is picked up by the next run, not the current one.
- The two walkers are `os.scandir` with an explicit stack (not recursion, not `os.walk`), so
  every entry is classified from the `DirEntry` the OS already handed back. Never reach for
  `path.lstat()` inside them — `entry_is_link()` / `entry_info()` answer from cached data, free
  on Windows. Traversal order is meaningless: `sync()` sorts every phase, folders via
  `by_depth`.
- `entry_is_link()` also catches Windows junctions and is used on the **destination** side.
  `scan_source` deliberately uses the plain `entry.is_symlink()`, so a junction in the source
  stays real content and is copied as a plain folder.

## Verifying changes

Run the suite from the repo root:

```
python -m unittest discover -s tests -t . -v
```

`-t .` puts the root on `sys.path`, which is what lets the tests `import sync` with no install
step. Expect one skip on Windows (a case-sensitivity test) and one expected failure (a known
bug, documented in `tests/test_sync.py`). ~135 tests, ~25s — most of it is the e2e module
spawning real subprocesses.

**Keep it green.** The suite was written against the behaviour as of `b5a83c0`, deliberately
without touching `sync.py`, so that the coming split into modules can be validated by re-running
it: if it still passes, the refactor changed nothing.

Guidance for adding tests:

- `tests/support.py` has the shared helpers. Describe trees as dicts and compare with
  `snapshot()` — a failure then shows two dicts, not a bare `False`.
- Never make a directory unreadable with a real ACL or `chmod`: use `failing_scandir()`, which
  patches `os.scandir`. Deterministic, cross-platform, and it cannot leave the filesystem in a
  state that outlives the test.
- Symlink and junction support are probed at import (`needs_symlinks`, `needs_junctions`), never
  assumed — creating a symlink on Windows needs developer mode or admin rights.
- Anything touching the filesystem goes under `TreeCase`, which gives an isolated `src`/`dest`
  pair in a temp directory.

Beyond the suite, check the dry run first when trying things by hand — the tool deletes files.

```powershell
$t = Join-Path $env:TEMP "synctest"
Remove-Item -Recurse -Force $t -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$t\src\sub", "$t\src\node_modules", "$t\dst" | Out-Null
Set-Content "$t\src\a.txt" "hello" -Encoding utf8
Set-Content "$t\src\sub\b.txt" "world" -Encoding utf8
Set-Content "$t\src\node_modules\skip.txt" "x" -Encoding utf8
Set-Content "$t\dst\stale.txt" "old" -Encoding utf8
Push-Location "$t\dst"
python D:\MyProjects\Python\sync_folders\sync.py --init --source "$t\src"
python D:\MyProjects\Python\sync_folders\sync.py -n
python D:\MyProjects\Python\sync_folders\sync.py -y
Pop-Location
Get-ChildItem -Recurse "$t\dst" | Select-Object -ExpandProperty FullName
Remove-Item -Recurse -Force $t
```

Expected: `a.txt` and `sub/b.txt` copied, `node_modules/` skipped, `stale.txt` deleted,
`sync.toml` kept.

Also worth exercising when touching the relevant code: a second run must be a no-op (mtime
check), `--init` on an existing config must prompt before overwriting, and a non-TTY run
without `--source` must exit with an error.

## Local environment note

On this machine the `py` launcher is not installed, so `sync.bat` always takes the `python`
fallback branch. Invoke `sync.py` through `python` directly when testing; the `py -3` branch
of the wrapper cannot be exercised here.
