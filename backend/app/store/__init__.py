"""World store — the sole writer of world state (AD-1).

Every mutation of the world graph flows through a store commit: atomic
subgraph transactions, one revision per commit, per-transaction undo.
Nothing else in the codebase writes world state.

Empty until Story 1.2 (Versioned World Store).
"""
