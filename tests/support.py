"""Shared helpers for the sync test suite.

Trees are described as flat dicts so a test reads as data, not as a sequence
of mkdir/write_text calls, and `snapshot` gives back the same shape so a
failure shows two dicts side by side instead of a bare False.
"""

import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_PY = REPO_ROOT / "sync.py"

DIR = None  # value marking a directory entry in a tree spec


def build(root: Path, spec: dict) -> None:
    """Creates a tree under `root`.

    A key ending in "/" is a directory (its value is ignored), anything else is
    a file whose value is its text content. Parent directories are implied.

        build(src, {"a.txt": "hello", "sub/b.txt": "world", "empty/": DIR})
    """
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in spec.items():
        target = root / rel
        if rel.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def snapshot(root: Path, skip: tuple[str, ...] = ()) -> dict:
    """The inverse of `build`: the tree as a dict, for assertEqual.

    Directories appear as "name/" -> DIR, files as "name" -> content. Links are
    reported as "name" -> "<link:target>" so they never look like a real file.
    """
    out: dict = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel in skip:
            continue
        if path.is_symlink() or _is_reparse(path):
            out[rel] = f"<link:{os.readlink(path)}>"
        elif path.is_dir():
            out[rel + "/"] = DIR
        else:
            try:
                out[rel] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                out[rel] = "<unreadable>"
    return out


def _is_reparse(path: Path) -> bool:
    """Windows junctions are not symlinks but must not be walked into."""
    if os.name != "nt":
        return False
    try:
        st = path.lstat()
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def run_cli(cwd: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """Runs sync.py as a real subprocess, the way a user would."""
    return subprocess.run(
        [sys.executable, str(SYNC_PY), *args],
        cwd=str(cwd), input=stdin, capture_output=True, text=True,
    )


def summary(stdout: str) -> str:
    """The final 'Copied N, updated N, ...' line."""
    for line in reversed(stdout.strip().splitlines()):
        if "Copied" in line:
            return line.strip()
    return ""


def operations(stdout: str) -> list[str]:
    """The indented per-entry action lines, normalised to 'action rel'."""
    ops = []
    for line in stdout.splitlines():
        if line.startswith("  ") and "Copied" not in line and not line.startswith("  !"):
            ops.append(" ".join(line.split()))
    return ops


# --------------------------------------------------------------------------
# Capability probes: these must be checked, never assumed
# --------------------------------------------------------------------------

def make_junction(link: Path, target: Path) -> None:
    """Windows directory junction. Works without administrator rights."""
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)


def _probe_symlinks() -> bool:
    """Creating a symlink needs developer mode or admin on Windows."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "target"
        target.mkdir()
        try:
            (Path(tmp) / "link").symlink_to(target, target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            return False


SYMLINKS_OK = _probe_symlinks()
JUNCTIONS_OK = os.name == "nt"

needs_symlinks = unittest.skipUnless(
    SYMLINKS_OK, "creating symlinks requires developer mode or admin rights")
needs_junctions = unittest.skipUnless(
    JUNCTIONS_OK, "junctions are a Windows feature")


def failing_scandir(*bad_paths: Path):
    """Patches os.scandir so listing `bad_paths` raises PermissionError.

    Preferred over a real deny ACL / chmod: it is deterministic, works the same
    on every platform, needs no privileges, and cannot leave the filesystem in
    a state that outlives the test.
    """
    bad = {str(Path(p)) for p in bad_paths}
    real = os.scandir

    def fake(path="."):
        if str(path) in bad:
            raise PermissionError(13, "Permission denied")
        return real(path)

    return unittest.mock.patch("os.scandir", fake)


def failing_unlink(*bad_paths: Path):
    """Patches Path.unlink so removing `bad_paths` raises PermissionError.

    Same reasoning as `failing_scandir`: a real read-only ACL would be
    platform-specific and could outlive the test.
    """
    bad = {str(Path(p)) for p in bad_paths}
    real = Path.unlink

    def fake(self, missing_ok=False):
        if str(self) in bad:
            raise PermissionError(13, "Permission denied")
        return real(self, missing_ok=missing_ok)

    return unittest.mock.patch.object(Path, "unlink", fake)


class TreeCase(unittest.TestCase):
    """Base class giving each test an isolated src/dest pair."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.src = self.tmp / "src"
        self.dest = self.tmp / "dest"
        self.src.mkdir()
        self.dest.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def write_config(self, source: Path | None = None, **keys) -> Path:
        """Writes a sync.toml into dest and returns its path."""
        import json
        source = self.src if source is None else source
        lines = [f"source = {json.dumps(str(source))}"]
        for key, value in keys.items():
            lines.append(f"{key} = {json.dumps(value)}")
        path = self.dest / "sync.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
