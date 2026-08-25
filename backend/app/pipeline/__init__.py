"""Generation pipeline — proposes changes; never writes world state (AD-1).

The pipeline reads the store, proposes a subgraph (entities, edges,
media), and hands it to a store commit. Generation failures never
mutate state.

Empty until the pipeline stories (Epic 2).
"""
