"""HTTP API layer.

Layering (AD-1): ``api/`` orchestrates requests; it owns no world state.
``pipeline/`` proposes, ``store/`` is the sole writer, ``providers/`` and
``media/`` are leaves.
"""
