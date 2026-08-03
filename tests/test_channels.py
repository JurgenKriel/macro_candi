"""Unit tests for canmac.io.channels — omero-color channel resolution (D-03, IO-01).

Channels MUST be resolved by omero ``color`` hex, never by the observed index
order (which happens to be magenta/lysed, red/Candida, green/macrophage here but
must not be relied upon). The lysed channel (FF00FF) must never be returned.
"""

from __future__ import annotations

import json

import pytest

from canmac.io.channels import (
    COLORS,
    DISCARD,
    discard_indices,
    resolve_channel,
)


def test_candida_resolves_by_color_roi2(roi2_zattrs):
    # Candida = FF0000; on disk that is index 1, but resolution is by color.
    assert resolve_channel(roi2_zattrs, "candida") == 1


def test_macrophage_resolves_by_color_roi2(roi2_zattrs):
    # Macrophage = 00FF00; on disk that is index 2, resolved by color.
    assert resolve_channel(roi2_zattrs, "macrophage") == 2


def test_candida_resolves_by_color_roi7(roi7_zarr):
    zattrs = roi7_zarr / "0" / ".zattrs"
    assert resolve_channel(zattrs, "candida") == 1


def test_macrophage_resolves_by_color_roi7(roi7_zarr):
    zattrs = roi7_zarr / "0" / ".zattrs"
    assert resolve_channel(zattrs, "macrophage") == 2


def test_lysed_color_never_returned(roi2_zattrs):
    # FF00FF (lysed, index 0) must never be a resolver result for any target.
    omero = json.load(open(roi2_zattrs))["omero"]["channels"]
    lysed_idx = [i for i, c in enumerate(omero) if c["color"].upper() in DISCARD]
    assert lysed_idx == [0]  # sanity: FF00FF is index 0 on disk
    for which in COLORS:
        assert resolve_channel(roi2_zattrs, which) not in lysed_idx


def test_discard_indices_returns_lysed(roi2_zattrs):
    assert discard_indices(roi2_zattrs) == [0]


def test_absent_color_raises(tmp_path):
    # A zattrs whose omero has none of the target colors must raise, not return 0.
    zattrs = tmp_path / ".zattrs"
    zattrs.write_text(
        json.dumps(
            {"omero": {"channels": [{"color": "123456"}, {"color": "ABCDEF"}]}}
        )
    )
    with pytest.raises(ValueError):
        resolve_channel(zattrs, "candida")


def test_duplicate_color_raises(tmp_path):
    # A color appearing more than once is ambiguous -> raise loudly.
    zattrs = tmp_path / ".zattrs"
    zattrs.write_text(
        json.dumps(
            {"omero": {"channels": [{"color": "FF0000"}, {"color": "FF0000"}]}}
        )
    )
    with pytest.raises(ValueError):
        resolve_channel(zattrs, "candida")


def test_color_match_is_case_insensitive(tmp_path):
    # Lower-case hex on disk must still resolve.
    zattrs = tmp_path / ".zattrs"
    zattrs.write_text(
        json.dumps(
            {
                "omero": {
                    "channels": [
                        {"color": "ff00ff"},
                        {"color": "ff0000"},
                        {"color": "00ff00"},
                    ]
                }
            }
        )
    )
    assert resolve_channel(zattrs, "candida") == 1
    assert resolve_channel(zattrs, "macrophage") == 2
