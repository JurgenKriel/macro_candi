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
from typing import Iterable, Mapping, Optional, Union

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


def resolve_channel(
    zattrs_path: Union[str, Path], which: str, colors: Optional[Mapping[str, str]] = None
) -> int:
    """Return the C-axis index whose omero color matches ``which``.

    ``colors`` maps role -> hex (normally the manifest's ``channels`` block); it
    defaults to the reference-experiment ``COLORS``.

    ``which`` is one of ``COLORS`` ("candida", "macrophage"). Matching is by
    color hex (case-insensitive), never by index. Raises ``ValueError`` if the
    target color is absent or appears more than once (fail loud — never silently
    default to index 0).
    """
    colors = COLORS if colors is None else colors
    if which not in colors:
        raise ValueError(f"unknown channel {which!r}; expected one of {sorted(colors)}")
    omero = _load_channels(zattrs_path)
    target = str(colors[which]).upper()
    hits = [i for i, c in enumerate(omero) if c["color"].upper() == target]
    if len(hits) != 1:
        raise ValueError(
            f"{which}/{target}: expected 1 channel, got {hits} in "
            f"{[c['color'] for c in omero]}"
        )
    return hits[0]


def discard_indices(
    zattrs_path: Union[str, Path], discard: Optional[Iterable[str]] = None
) -> list[int]:
    """Return the indices whose color is in ``DISCARD`` (lysed channels).

    Callers can use this to assert the lysed channel is excluded from any read.
    """
    omero = _load_channels(zattrs_path)
    drop = DISCARD if discard is None else {str(c).upper() for c in discard}
    return [i for i, c in enumerate(omero) if c["color"].upper() in drop]
