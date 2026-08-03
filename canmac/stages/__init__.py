"""Batch/offline pipeline stages (orchestrators that emit sidecars).

Stages load the validated dataset manifest, loop the 2 datasets (and, where
relevant, the 120 timepoints — never the buggy "50 scenes"), and write their
outputs to the ``results/`` tree via ``canmac.io.atomic`` (temp -> rename +
``.done`` sentinel), never mutating the read-only ``raw.zarr`` inputs.
"""
