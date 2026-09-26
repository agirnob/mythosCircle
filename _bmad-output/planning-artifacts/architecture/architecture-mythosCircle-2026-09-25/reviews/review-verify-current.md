# Review — Verify Current (spine vs repo, 2026-09-25)

Scope: every committed claim in `ARCHITECTURE-SPINE.md` that could be out of
date — Stack table versions vs `backend/pyproject.toml` + `frontend/package.json`,
and the brownfield facts AD-26..AD-35 assume vs `backend/app/store/commit.py`,
`backend/app/store/models.py` (Job kind set, edge/revision/event/session columns),
plus route-level existence checks for the proposed endpoints. Read-only; no code
edits, no test runs. No web search was needed: everything the spine names is either
repo-verifiable (all version pins) or generic technology (SQLite, OpenAI-compatible
HTTP) — there is no named external product, paper, or version whose existence rests
on training data.

## Verdict: PASS with minor findings

The Stack table matches the repo exactly, and every brownfield "current state" the
new ADs assume was confirmed in code. No stale version, no contradicted schema
claim, no training-data assertion. Three minor findings below (one inaccurate
absolute, one path-shape inconsistency, one imprecise export claim); none blocks
the slice.

## Stack table — all confirmed

| Spine claim | Repo | Status |
| --- | --- | --- |
| Python `>=3.12,<3.13` | `pyproject.toml:5` `requires-python = ">=3.12,<3.13"` | exact |
| FastAPI `0.141` | `pyproject.toml:7` `fastapi>=0.141,<0.142` | exact |
| SQLAlchemy `2.x` | `pyproject.toml:9` `sqlalchemy>=2.0,<3` | exact |
| SQLite WAL mode (`data/mythos.db`) | `data/` holds `mythos.db` + live `mythos.db-wal`/`-shm` | confirmed in use |
| Vue `3.5.41` / Pinia `4.0.3` / Vite `8.2.2` / TS `6.0.3` | `package.json`: `vue ^3.5.41`, `pinia ^4.0.3`, `vite ^8.2.2`, `typescript ^6.0.3` | exact lower bounds |
| LLM owner-managed local OpenAI-compatible server | `core/config.py` + `providers/llm.py` (`{endpoint}/chat/completions`, tri-state reasoning control for the local gemma stack); spine binds no new tech | consistent, correctly unversioned |

Caveat (not a finding): frontend entries are caret ranges, so installed versions may
float above the pinned floors; the spine reports the floors, which is the honest
"verified against the repo" reading.

## Brownfield facts — all confirmed

- **EDGE_KIND_RULES cells (`commit.py:68-97`):** `employs` src = `{character, faction}`
  (no `place`); `controls` src = `{character, faction}` (no `place`); no `part_of`
  type in `EDGE_TYPES` (`commit.py:37-56`) or the matrix. AD-31's delta is therefore
  a genuine delta — nothing it "adds" is already there, and "all other cells frozen"
  has a concrete baseline. The `100 of 114 rows` catch-all figure (AD-30) matches the
  comment at `commit.py:34-36` verbatim.
- **Edge columns (`models.py:117-141`):** `id, campaign_id, src, dst, type, counter,
  created_at` (+ `uq_edge_relationship`). No `reason` column — AD-32 is prospective,
  correctly framed. (`reason` hits elsewhere are violation-explanation strings in
  `pipeline/build_in.py` and `generate.py` drop logs, not persisted edge data.)
- **Revision/Event (`models.py:76-166`):** `revision(id, campaign_id, base_revision,
  created_at)` + `event(id, campaign_id, revision_id, type, payload, created_at)` —
  the shared stream AD-26 assumes exists.
- **Session (`models.py:312-328`):** table is still named `session`, still the login
  session (opaque token SHA-256, `expires_at`, `revoked_at`). AD-28's rename to
  `login_session` is prospective, correctly framed as build-owned migration.
- **New state tables:** `entity_session_state`, `entity_knowledge_state`,
  `login_session` appear nowhere in `backend/` — AD-28 is greenfield, correctly framed.
- **Job kinds (`models.py:187-190`):** `text, image, video, build_in, generate,
  regenerate`. `regenerate` exists, so guided-regenerate riding it (AD-3 row, AD-33)
  is grounded; no `enrich` kind exists anywhere, consistent with "Enrich adds no job
  kind". (`enrich`, `part_of`, `dial` are absent from `backend/app`; only `archetype`
  exists, as `pipeline/mechanics.py` code-owned character tables — matching the
  Deferred note that `dial`/`archetype` record fields are still open.)
- **AD-24 ≥1-edge floor:** the store backstop exists (`commit.py:320-322`, orphan check
  with `allow_orphans` only for build-in wave 1) — the "drop-with-audit backstop /
  thin candidates fail loud" assumption holds.
- **Proposed endpoints are absent, as expected:** no `/kinds` and no `/revisions`
  route in `backend/app/api/` (full route inventory checked) — AD-34/AD-35 describe
  new surface, not existing surface. No contradiction.

## Findings

1. **[MINOR — inaccurate absolute] Mermaid/direction claim vs `store/direct.py:431`.**
   The spine states "Dependency direction holds: `pipeline -> store`, never the
   reverse". `store/direct.py:431` contains `from app.pipeline.knowledge import
   validate_stat_block` — a real store→pipeline edge, albeit function-deferred with
   `noqa: PLC0415`. The codebase's own comments (`candidates.py:86-88`,
   `direct.py:15-18`) claim "no pipeline imports" as the rule while carrying this one
   exception. Suggest softening to "no module-level reverse imports" or naming the
   deferred exception; as written, a reader grepping will find a counterexample.
2. **[MINOR — path-shape inconsistency] AD-34 vs AD-35/per-campaign convention.**
   AD-34 paths the registry as `GET /api/campaigns/kinds` (collection-level, no
   `{id}`), while AD-35 binds both new reads to "AD-9 ownership like every route"
   and the Capability Map puts Tonight reads under per-campaign `api`. If kinds is
   global vocabulary (it renders the global `EDGE_KIND_RULES`), collection-level is
   defensible — but then "owned like every route" needs one line saying what ownership
   means for a campaign-independent render (any-authenticated vs per-campaign). The
   route inventory shows a precedent for collection-level (`/api/campaigns/themes`),
   so this is a one-line clarification, not a redesign.
3. **[TRIVIA — imprecise export claim] "Exporter reads entity/edge only".**
   `api/exports.py:159-190` assembles the snapshot from `world_state`
   (entities + edges) **plus** the media manifest and revision meta. The forward-looking
   point (new state rows and the log must never leak) is sound, but "entity/edge
   only" is not literally true today. Suggest "entity/edge (+ existing media
   manifest); state rows and the log never leak".

## Explicit non-findings (checked, clean)

- No version in the Stack table is stale or floats beyond its pin.
- No AD assumes a column, table, job kind, or endpoint that already exists under a
  different shape (the classic "proposes what is already there" failure).
- The `part_of` / place-employ / place-control / reason / session-rename deltas are
  all genuinely absent from code — the slice has something to build.
- Nothing in the spine required web verification: no named external versions beyond
  the repo pins, no third-party products, no paper/model claims asserted from memory.
