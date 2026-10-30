"""Batch/offline pipeline stages (orchestrators that emit sidecars).

Stages load the validated dataset manifest, loop its datasets (and, where
relevant, their ``dims.T`` timepoints — never the buggy "50 scenes"), and write their
outputs to the ``results/`` tree via ``canmac.io.atomic`` (temp -> rename +
``.done`` sentinel), never mutating the read-only ``raw.zarr`` inputs.
"""
