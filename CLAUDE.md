# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`sync` is a CLI that mirrors a source folder into the current working directory. One-way
mirror, not a merge: files in the destination that are not in the source (and not excluded) get
**deleted**. Read `README.md` for the user-facing behavior — do not duplicate that content here.

## Layout

| File | Role |
| --- | --- |
| `src/matching.py` | Glob matching, `.gitignore` style. |
| `src/paths.py` | Link detection, path resolution, sort keys, `launcher_paths()`. |
| `src/config.py` | `sync.toml`: constants, template, interactive setup, loading. |
| `src/scanning.py` | The two walkers, `scan_source` and `scan_dest`. |
| `src/syncing.py` | `sync()`: the four phases. |
| `src/cli.py` | Argument parsing and `main()`. |
| `src/__main__.py` | Entry point of the zipapp, and of `python src`. |
| `build.py` | Generates `dist/sync.pyz` + `dist/sync.bat`. The whole toolchain. |
| `tests/` | Unit and end-to-end suite. Not shipped. |
| `README.md` | User documentation. |

`src/` is the archive root, so the modules are **flat and imported absolutely**
(`from matching import Matcher`), never as a package with relative imports. Keep new code inside
the module that matches it.

## Hard constraints

- **Standard library only.** No dependencies, ever — `python build.py` must remain the entire
  build. Do not add a `requirements.txt`, a `pyproject.toml`, or a packaging layout unless
  explicitly asked.
- **One file on the `PATH`.** What gets installed is `dist/sync.pyz` plus its `.bat` wrapper,
  both generated. Nothing in `dist/` is edited by hand, and `dist/` is gitignored.
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
- **`protected`** holds what `launcher_paths()` returns plus the config file, relative to the
  destination. It is what stops the tool from deleting itself, its wrapper, or its own
  `sync.toml`. Any new deletion path must honor it.
- **`launcher_paths()` reads `sys.argv[0]`, and it has to.** Inside a zipapp `__file__` points
  *into* the archive (`...\sync.pyz\syncing.py`), a path that does not exist, so the old
  `SCRIPT_PATH = Path(__file__).resolve()` would protect nothing. `sync.bat` is protected too:
  with `dist/` on the `PATH`, a run inside it would otherwise delete the wrapper and leave the
  tool unlaunchable. The negative control is worth knowing — with the protection disabled, a run
  deletes both files and still exits 0.
- **`src/__main__.py` is written by hand on purpose.** The one
  `zipapp.create_archive(main="cli:main")` generates calls `main()` and throws the return value
  away, so every run would exit 0 — including the ones that report errors. Never switch
  `build.py` to the `main=` argument.
- **Exclusions are symmetric**: an excluded entry is neither copied nor deleted. `scan_dest()`
  applies the same `Matcher` as `scan_source()` — that is the mechanism, not an accident.
- **A failing `rmdir` is expected**, not an error: it means the folder still holds excluded or
  protected entries and must be kept. It is intentionally not counted in `stats["errors"]`.
  But phases 3 and 4 must then refuse to write at that path — both carry an `in_the_way` check
  for it, and both report an error rather than forcing their way through:
  - phase 3, because `shutil.copy2` given a directory copies *into* it rather than failing,
    which used to leave a file the source never had while still reporting success;
  - phase 4, because it used to `shutil.rmtree` the folder to make room for the link, deleting
    the very entries the exclusion promised to spare.

  A real folder surviving into phase 3 or 4 always means phase 1 could not remove it: everything
  else under it was already deleted, so the `rmdir` would have succeeded.
- **A deletion is only legal where the source was actually read.** `scan_source()` returns an
  `unscanned` set — folders whose `scandir` failed (the root as `""`) and link cycles it refused
  to walk — and phase 1 skips every destination entry under it. "Absent from the source" and
  "we could not look" produce the same empty scan, and acting on the second wipes files the
  source still holds: the source stays intact, so the destination copy is the only one lost.
  Any new source-side reason to stop walking must join that set.
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

`-t .` puts the repo root on `sys.path` (which is how `tests/test_e2e.py` imports `build`), and
`tests/__init__.py` prepends `src/` so the test modules can import `matching`, `paths`, and the
rest with no install step and no build. 156 tests. On Windows one case-sensitivity test skips;
the symlink tests skip too unless developer mode is on.

`run_cli()` spawns `python src`, not the built archive: running a directory holding a
`__main__.py` behaves exactly like running the `.pyz`, so the e2e tests stay honest without a
build step. The one test that does build is the self-protection one, which needs the real
artifact.

**Keep it green.** The suite was written against the behaviour as of `b5a83c0`, deliberately
without touching the code, so that the split into modules could be validated by re-running it.
Same rule from here on.

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

Beyond the suite, build and exercise the real artifact — the dry run first, the tool deletes
files.

```powershell
python build.py
$t = Join-Path $env:TEMP "synctest"
Remove-Item -Recurse -Force $t -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$t\src\sub", "$t\src\node_modules", "$t\dst" | Out-Null
Set-Content "$t\src\a.txt" "hello" -Encoding utf8
Set-Content "$t\src\sub\b.txt" "world" -Encoding utf8
Set-Content "$t\src\node_modules\skip.txt" "x" -Encoding utf8
Set-Content "$t\dst\stale.txt" "old" -Encoding utf8
Push-Location "$t\dst"
$sync = "D:\MyProjects\Python\sync_folders\dist\sync.pyz"
python $sync --init --source "$t\src"
python $sync -n
python $sync -y
Pop-Location
Get-ChildItem -Recurse "$t\dst" | Select-Object -ExpandProperty FullName
Remove-Item -Recurse -Force $t
```

Expected: `a.txt` and `sub/b.txt` copied, `node_modules/` skipped, `stale.txt` deleted,
`sync.toml` kept.

Also worth exercising when touching the relevant code: a second run must be a no-op (mtime
check), `--init` on an existing config must prompt before overwriting, and a non-TTY run
without `--source` must exit with an error.

One more check that only the built archive can give, because it is the `__main__.py` being
tested: a run that reports errors must exit `1`. The cheapest recipe — the source holds a file
`x`, the destination a folder `x/` holding an *excluded* entry (`x/keep.log`), so phase 1 cannot
remove it and phase 3 refuses to write through it.

## Local environment note

On this machine the `py` launcher is not installed, so `sync.bat` always takes the `python`
fallback branch. Invoke `dist\sync.pyz` through `python` directly when testing; the `py -3`
branch of the wrapper cannot be exercised here.
