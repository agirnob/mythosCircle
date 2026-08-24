# Stack — mythosCircle

Seed — verified at Finalize; the code owns this once it exists.

| Name | Version |
| --- | --- |
| Python | 3.12 |
| FastAPI | latest stable (0.14x as of 2026-08) |
| SQLAlchemy 2 + SQLite (WAL) | 2.x |
| Pydantic | v2 |
| Vue | 3.5 |
| Vite + TypeScript | 8.x / 7.x |
| Pinia | 4.x |
| llama.cpp (`llama-server`) | latest stable |
| Caddy | 2.x |
| Image/video model | owner picks at build (adapter interface already bound, AD-6) |

Notes:

- Local LLM: abliterated 14–20B (24GB VRAM cap, Q4/Q5), served as an OpenAI-compatible HTTP server (AD-14).
- All provider access goes through OpenAI-compatible HTTP adapters — local→OpenRouter is a config change (AD-6, NFR 8).
