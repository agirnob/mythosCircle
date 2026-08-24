# Review — Version-Check Lens (reality-check of committed decisions)

**Spine:** `ARCHITECTURE-SPINE.md` (mythosCircle, 2026-08-23, 25 ADs; Stack declared SEED)
**Method:** live registry/release checks on 2026-08-23 — PyPI JSON API (fastapi, sqlalchemy, pydantic), endoflife.date (Python cycles), npm registry (vue, vite, typescript, pinia), GitHub releases API (ggml-org/llama.cpp, caddyserver/caddy), primary sites (rpgtoken.tools, fantasygrounds.com, docs.owlbear.rodeo).
**Verdict: PASS** (all named tech is real, current, and fit for a single-owner TTRPG DM tool; 5 findings are one-line seed corrections, none load-bearing)

Prior-reconcile items confirmed present and NOT re-reported: AD-2 identity stability (ULID preserved, rebase-or-reject), AD-11 JSON + Markdown always exportable, AD-24 candidate shape, AD-25 total delete, AD-17 WS envelope + AD-9 cookie transport (both since fixed in the spine text).

## Version table (spine claim vs. live reality, 2026-08-23)

| Spine entry | Live reality | Fit |
| --- | --- | --- |
| Python 3.12 | 3.12.14, supported to 2028-10-31 (security phase); current stable 3.14.7 (2025-10-07), 3.13.15 also current | ✅ real, sensible; two minors behind latest — fine for beta, note as candidate bump |
| FastAPI 0.11x "(latest stable)" | **0.141.1** (PyPI, not yanked; requires Python ≥3.10) | ✅ framework current & idiomatic for this app; version string stale by several patch generations |
| SQLAlchemy 2.x | 2.0.52 (PyPI), supports 3.12–3.15 | ✅ |
| Pydantic v2 | 2.13.4 (PyPI) | ✅ |
| Vue 3.5 | 3.5.41 (npm `latest`; no 4.x exists) | ✅ exactly current |
| Vite 7.x | 7.x real & stable; npm `latest` = **8.2.2** (rolldown-based) | ✅ real, one major behind current default |
| TypeScript 5.x | 5.9.x real; npm `latest` = **7.0.2** (native compiler era, platform-split packages) | ✅ real, two majors behind — stalest seed entry |
| Pinia 3.x | 3.x real; npm `latest` = **4.0.3** (peer: vue ^3.5.11) | ✅ real, one major behind |
| llama.cpp `llama-server` "latest stable" | Repo now under semantic versioning: **v0.2.0** stable tag (2026-08-21); `b[NNNN]` tags = nightly. Release notes confirm `llama-server` with OpenAI-compatible endpoints, auth, and a Web UI | ✅ "latest stable" is now a precise, correct phrasing |
| Caddy 2.x | **v2.11.4** (2026-06-03, active) | ✅ sensible in 2026 |

## Architecture-decision sanity

1. **llama-server as serial single-queue LLM server for an abliterated 14–20B model on a 24GB RTX 3090 — PASS, good fit.** Q4_K_M: ~8.5–9 GB (14B) / ~12 GB (20B); Q5_K_M: ~10 GB / ~14–15 GB — comfortably inside 24 GB with 16–32K context KV cache + room for a vision projector (AD-6 already binds an image provider). `llama-server` processes completions serially by default (concurrency 1 unless `--parallel`), so the AD-3 requirement of one job at a time matches the server's native behavior — no fight between the app queue and the model server. Implementation notes to carry into the seed: enable flash attention, set `--ctx-size` from config, and keep `--parallel` off while AD-3 holds; queue-position visibility is an app property (AD-13 queue table), not a llama-server property.

2. **Caddy as TLS front for a personal server — PASS, sensible in 2026.** v2.11.4 is current; auto-ACME/Let's Encrypt, single static binary, Caddyfile config (matches AD-22's one-config-file stance). Nothing has displaced it for a one-person single-host deploy; reverse-proxying Caddy→uvicorn (AD-21) with FastAPI bound to localhost is the standard shape.

3. **SQLite WAL single-writer for revisions + event log + queue + media manifest + accounts — PASS, known-limitation caveat.** Single owner, single API process, local file (WAL is file-based, no remote access — irrelevant under AD-8's single-machine rule) makes this a good fit; the whole-DB write lock is neutralized by AD-13's "single writer = the API process" (consistent with AD-1's one commit path). **Caveat to fold into AD-12:** "snapshots the SQLite file" is only safe via the backup API — `PRAGMA wal_checkpoint(TRUNCATE)` + copy, or `VACUUM INTO` / `sqlite3 .backup`; a raw `cp` of a WAL-mode DB can drop un-checkpointed WAL frames. The pre-beta "restore script is exercised" step should exercise exactly that path. Secondary note: the append-only event table grows unbounded; nightly checkpointing keeps the main file honest (no schema change needed).

4. **"RPGToken" as a MapTool token format — naming, not fabrication.** There is no canonical format *named* RPGToken; the real target is MapTool's token JSON (`.token.json`: name, image, gridWidth/gridHeight, description, …), and the third-party tool the PRD is naming after is rpgtoken.tools ("RPG Token Tools — Create tokens for tabletop games", live). The GitHub repo `rangersprotocol/RPGToken` (Solidity ERC20 vesting) is an unrelated name collision. PRD line 143 already defines it correctly ("MapTool character format: JSON + portrait"), so spine/PRD are consistent. Recommendation: define the export artifact as **"MapTool token JSON + portrait image"** and keep "RPGToken" only as the label.

5. **"Owlbear" — real VTT, but the artifact must be specified.** Owlbear Rodeo is a live, free VTT (docs.owlbear.rodeo, extensions active into 2026). Its asset/token system loads token images **from URLs** (asset API `uploadImages`; token item "loads from a url"); there is no documented Owlbear token-JSON import schema to validate against (unlike MapTool). So the "Owlbear" export should be spec'd as: **token/portrait image URL served by the media service (AD-10) + stat-block text**, with validation limited to image existence. This keeps AD-11's "format-validated" promise honest.

6. **"Fantasy Grounds" — real and active in 2026; different format family.** © SmiteWorks 2004–2026, free-to-play, full 5e store. FG content is XML+Lua modules (`.fgmodule` / Forge), not token JSON — heavier than the other targets; fine as a Phase-1 target but note the artifact shape differs from Owlbear/MapTool.

7. **"5e stat block" shape vs. D&D 5e SRD — consistent with two pins.** SRD 5.1 stat-block fields: name (type, alignment), Initiative, AC, HP, Speed, six ability scores, saves, skills, senses + Passive Perception, Challenge Rating (monsters) / level (NPCs only), languages, traits/actions. PRD line 50's shorthand "(level, AC, HP, abilities, key skills)" is consistent *except* "level" is an NPC-only field (monsters use CR) — do not make `level` required for all entities. As of 2026 there are **two** stat-block dialects (SRD 5.1 (2023) and the 2024-revision "5.2"; Fantasy Grounds ships rulesets for both). Recommendation: AD-23/AD-24 should pin one canonical field set (recommend SRD 5.1, CR for monsters, level optional for NPCs) so the export validator (AD-11) has a stable target.

## Findings (ranked)

1. **[Seed] Version strings stale (one-line fixes):** FastAPI "0.11x (latest stable)" → 0.141.x; Vite "7.x" and TS "5.x" and Pinia "3.x" are real and installable but one–two majors behind the 2026 defaults (8.2.x / 7.0.x / 4.0.x). Nothing forces a move now, but the seed should either pin deliberately or read "latest stable" per line, as the llama.cpp row already does.
2. **[AD-12] WAL-mode backup must use the backup API** (checkpoint + copy / `VACUUM INTO`), not a raw file snapshot; exercise this exact path in the pre-beta restore drill.
3. **[AD-11] "Owlbear" artifact is unspecified:** Owlbear has no documented token-JSON import; spec it as image-URL + stat-block text with image-existence validation.
4. **[Naming] "RPGToken" is a tool name (rpgtoken.tools), not a format:** define the artifact as MapTool token JSON + portrait; keep the label, drop the implication of a standard.
5. **[AD-23/AD-24] Pin the 5e stat-block dialect** (recommend SRD 5.1: CR for monsters, level optional for NPCs) so AD-11 validation is stable across 5.1/2024-revision content (FG ships both).

**Verdict rationale:** Every committed technology exists, is maintained, and is a sensible 2026 choice for a single-owner local TTRPG DM tool; the two "could be out of date" risks (FastAPI 0.11x, TS 5.x) are string staleness, not capability gaps. The architecture decisions touching external tech (serial llama-server on 24 GB, Caddy front, SQLite WAL single writer) hold up; the only implementation trap found is the WAL backup path in AD-12. No load-bearing gap — PASS.
