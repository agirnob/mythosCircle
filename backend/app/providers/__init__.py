"""Inference providers — OpenAI-compatible HTTP adapters (AD-14).

All generation goes through config-swappable OpenAI-compatible endpoints
(local llama-server at MVP; any OpenAI-compatible server via a config
change).

Leaf of the dependency graph: only ``pipeline/`` and ``media/`` may
depend on providers; providers depend on nothing else in ``app/``.

Empty until Story 1.4 (OpenAI-compatible inference adapter).
"""
