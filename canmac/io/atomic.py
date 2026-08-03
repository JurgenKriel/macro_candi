"""Atomic write helpers: temp -> os.replace + a ``.done`` sentinel written last.

Every parent-pipeline script writes in place (``open(out, "w")`` /
``zarr.open(mode="w")``), so a SLURM kill or a walltime hit leaves a
present-but-incomplete artifact that downstream stages then treat as done. This
module is the deliberate correction (ARCHITECTURE.md Anti-Pattern 1, RESEARCH
"Don't Hand-Roll"):

* payloads are written to a ``<path>.tmp.<pid>`` sibling in the *same* directory,
  ``flush`` + ``os.fsync``'d, then atomically renamed into place with
  ``os.replace`` (atomic on POSIX within one filesystem);
* the ``<path>.done`` sentinel is written **last**, after the payload is durable,
  so an interrupted job never leaves a ``.done`` next to a partial artifact.

Consumers gate on :func:`is_done`, not on mere file existence.
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Union

PathLike = Union[str, os.PathLike]


def _tmp_sibling(path: Path) -> Path:
    """A temp sibling in the same directory (same filesystem -> atomic rename)."""
    return path.with_name(f"{path.name}.tmp.{os.getpid()}")


def atomic_write_text(path: PathLike, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp sibling -> fsync -> os.replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_sibling(path)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic rename into place
    finally:
        # Never leave a .tmp.<pid> leftover that could shadow the final path.
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: PathLike, obj: Any) -> None:
    """Serialize ``obj`` as indented JSON and write it atomically."""
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=False))


def done_path(path: PathLike) -> Path:
    """Return the sibling ``<path>.done`` sentinel path."""
    path = Path(path)
    return path.with_name(f"{path.name}.done")


def write_done(path: PathLike) -> None:
    """Create the ``<path>.done`` sentinel. Call this LAST, after the payload."""
    atomic_write_text(done_path(path), "")


def is_done(path: PathLike) -> bool:
    """Return whether the ``<path>.done`` sentinel exists."""
    return done_path(path).exists()


@contextmanager
def atomic_dir(final_dir: PathLike) -> Iterator[Path]:
    """Context manager for atomic *directory* outputs (e.g. a Zarr store).

    Yields a temporary directory ``<final_dir>.tmp.<pid>``; write everything
    there. On clean exit the temp dir is ``os.replace``'d into ``final_dir``
    (removing any prior final dir first). On error the temp dir is removed so no
    partial directory is left behind. The caller writes ``write_done(final_dir)``
    after the ``with`` block if a sentinel is wanted.
    """
    final = Path(final_dir)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_sibling(final)
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        yield tmp
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    else:
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)  # atomic rename into place
