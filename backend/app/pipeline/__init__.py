"""Generation pipeline — proposes changes; the store commits (AD-1).

The pipeline reads the store, runs provider calls, and lands accepted
output through a store commit (``commit_subgraph``) — the store remains
the sole writer of world rows (AD-13). A failing wave writes nothing;
waves committed before the failure (the build-in wave-1 core) stay
committed — documented resilience, no compensating undo.
"""
