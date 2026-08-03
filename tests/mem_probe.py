"""Memory-profile proof that a per-timepoint read stays far below full-4D (IO-01 SC2).

Runnable script that reads EXACTLY ONE (t, c) volume through the lazy reader and
prints its nbytes. Under a peak-RSS measurement (``/usr/bin/time -v`` in the
streaming test) the resident set stays well under 2 GB — a single (t, c) uint16
volume is ~89 MB (301*401*369*2 B for ROI2), whereas materializing the full 4D
array would need ~32 GB. This script therefore never touches the 4D array: it
calls ``get_view(dataset, channel, t).compute()`` on one timepoint only.

Usage::

    python tests/mem_probe.py --dataset ROI2 --t 60 --channel candida
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the repo root is importable when run as a bare script (python tests/mem_probe.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from canmac.io.reader import get_view  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Single-timepoint read peak-RSS probe.")
    p.add_argument("--dataset", required=True, help="dataset id (e.g. ROI2, ROI7)")
    p.add_argument("--t", type=int, required=True, help="timepoint index to read")
    p.add_argument("--channel", required=True, help="channel name (candida|macrophage)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    # Lazy view of ONE (t, c) volume, then materialize just that timepoint.
    volume = get_view(args.dataset, args.channel, t=args.t).compute()
    print(
        f"dataset={args.dataset} t={args.t} channel={args.channel} "
        f"shape={tuple(volume.shape)} dtype={volume.dtype} nbytes={volume.nbytes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
