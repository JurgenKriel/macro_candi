"""Channel resolution by omero color (D-03, IO-01).

The OME-Zarr ``omero.channels`` block carries a per-channel ``color`` hex string.
Downstream analysis selects the Candida (ch2) and macrophage (ch3) channels by
that color, NOT by index: the observed on-disk order here is
``[magenta/lysed, red/Candida, green/macrophage]`` but a different converter run
could reorder it, and selecting the wrong channel is a silent, catastrophic
failure (analysing the wrong biology). The lysed channel (``FF00FF``) is excluded
from every read.

Parsing is stdlib ``json`` only (RESEARCH Pattern 1 — do not depend on the
churning ome-zarr-py object model for authoritative values).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

# Target colors, resolved against omero.channels[*].color (case-insensitive).
COLORS = {
    "candida": "FF0000",  # ch2 (red)
    "macrophage": "00FF00",  # ch3 (green)
}
# Colors that must never be returned (lysed cells, label T1-LLS1_3).
DISCARD = {"FF00FF"}


def _load_channels(zattrs_path: Union[str, Path]) -> list[dict]:
    with open(zattrs_path) as f:
        return json.load(f)["omero"]["channels"]


def resolve_channel(zattrs_path: Union[str, Path], which: str) -> int:
    """Return the C-axis index whose omero color matches ``which``.

    ``which`` is one of ``COLORS`` ("candida", "macrophage"). Matching is by
    color hex (case-insensitive), never by index. Raises ``ValueError`` if the
    target color is absent or appears more than once (fail loud — never silently
    default to index 0).
    """
    if which not in COLORS:
        raise ValueError(f"unknown channel {which!r}; expected one of {sorted(COLORS)}")
    omero = _load_channels(zattrs_path)
    target = COLORS[which]
    hits = [i for i, c in enumerate(omero) if c["color"].upper() == target]
    if len(hits) != 1:
        raise ValueError(
            f"{which}/{target}: expected 1 channel, got {hits} in "
            f"{[c['color'] for c in omero]}"
        )
    return hits[0]


def discard_indices(zattrs_path: Union[str, Path]) -> list[int]:
    """Return the indices whose color is in ``DISCARD`` (lysed channels).

    Callers can use this to assert the lysed channel is excluded from any read.
    """
    omero = _load_channels(zattrs_path)
    return [i for i, c in enumerate(omero) if c["color"].upper() in DISCARD]
