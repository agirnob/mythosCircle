# Rubric Review — v3 Architecture Slice Spine (2026-09-25)

Scope: `ARCHITECTURE-SPINE.md` (feature altitude, build-substrate) against the
good-spine checklist. Sources read: v3 spine, parent 08-23 spine (AD-1..AD-25 +
Deferred), v3 slice `.memlog.md`, UX `DESIGN.md` / `EXPERIENCE.md` / UX
`.memlog.md`, repo ground truth (`backend/app/store/models.py`,
`backend/app/store/commit.py` EDGE_TYPES / EDGE_KIND_RULES / `edge_kind_ok`,
`backend/pyproject.toml`, `frontend/package.json`). Read-only review; no edits
to the spine, no validation commands run.

## Verdict: CONDITIONAL PASS

The spine fixes the real divergence points for the build level, every new AD is
enforceable, the stack table is repo-verified, and no new AD weakens the
parent. Two gaps block a clean pass: (1) the spine silently supersedes the
"final" UX docs on Tier-2 state without saying so, leaving two authoritative
sources in contradiction; (2) the dial-home / lazy-render decisions
(DIRECT_KEYS+1, no-migration render) have no AD although two units can diverge
on them. Both are fixable with small AD/note additions, no structural rework.

## Checklist walk

### 1. Fixes the real divergence points for the level below — MOSTLY YES

Covered, each with an owner and an enforcement site:

| Divergence | Rule |
|---|---|
| Canon vs table-state rewind split | AD-26 one logbook |
| Two history vocabularies (undo vs edit) | AD-27 `edited` always |
| N smart readers deriving state N ways | AD-28 committed rows, dumb joins |
| Two meanings of `session` one grep apart | AD-28 `login_session` rename |
| Relationship swamp, catch-all drift | AD-30 strict kinds + AD-31 delta |
| Meaningless generic edges | AD-32 saved reason |
| One blank voiding a generation | AD-33 repair-then-drop |
| Picker offers what commit drops | AD-34 code-wins registry |
| Tonight reads becoming writes / leaking campaigns | AD-35 read-only + owned |
| Verb proliferation by silent addition | Deferred AD-gate |

Missed (see Findings F-2, F-3): dial storage location and enrich request shape
have no enforceable rule although writer/renderer/exporter can diverge on them.

### 2. Every AD Rule enforceable and preventing its stated divergence — YES, with one soft spot

- AD-26: enforceable — one commit path, single writer; tests can assert verb
  commits produce `revision`+`event` rows through `store/`.
- AD-27: enforceable — compensating-commit mechanics inherited from AD-2;
  feed-rendering rule is a frontend assertion (`edited` for verb commits and
  take-backs).
- AD-28: enforceable — table names, same-transaction rows+events, rename
  migration. Concrete enough to fail a build that derives state at read.
- AD-29: enforceable — one toggle, two values, both directions undoable; the
  explicit `No shown-to-me` closes the middle-state fork.
- AD-30: enforceable via the matrix + `edge_kind_ok` for model output AND
  hand-authored edges alike (closes the picker bypass).
- AD-31: enforceable — exact cell deltas (`employs`/`controls` src += `place`;
  `part_of` = `{place,faction}`→`{place,faction}`; all else frozen) plus the
  pin test as the named enforcement site. "One matrix edit propagates to
  prompts, validators, pickers, and the pin test" names all four consumers.
- AD-32: enforceable — blank-reason reject at commit for new edges,
  grandfathered `NULL` for old rows. Counter semantics explicitly untouched,
  so AD-23 stands.
- AD-33: enforceable in structure (one attempt, single-field schema,
  take-only-X merge, null-prose denylist, drop-with-audit, AD-24 floor) but
  contains the spine's softest phrase: "grammar-enforced **where backends
  allow**". A backend that never allows it silently degrades to prompt-only
  discipline. Recommend: name the fallback (server-side single-key
  extraction + discard-rest is already required by take-only-X, so the
  guarantee holds either way — say that).
- AD-34: enforceable — endpoint renders `EDGE_KIND_RULES`, owns nothing,
  nothing writes at runtime. The accept-time rejection class becomes a test:
  every picker option passes `edge_kind_ok`.
- AD-35: enforceable — read-only + AD-9 ownership + payload shape (meta +
  event summaries only). "Kinds responses are cacheable" is declared but has
  no TTL/invalidation rule (see F-4).

### 3. Nothing under Deferred lets two units diverge — MOSTLY YES

- Tier-2-after-Tier-1 sequencing gate: correctly a gate, not design. OK.
- `login_session` migration mechanics as build detail: safe, single owner
  (the build migration). OK.
- New verbs as new ADs, never silent: the strongest deferred line — closes
  the exact proliferation fork AD-27/AD-28 would otherwise leave open. Good.
- Sandbox/what-if, capture parse, kind-tabs as UX-owned opens: correctly
  below this altitude; none touches the commit path. OK.
- **Gap (F-2):** "records predating dial/archetype/reason fields: defaults at
  read vs backfill" is deferred to build detail, but this is a two-unit
  decision (readers defaulting vs writers backfilling can disagree row by
  row). "Revisit before the build slice" mitigates but does not decide; at
  minimum the spine should pin the tie-break direction (read-defaults win
  unless the build AD says otherwise), matching the already-decided lazy
  render (Q5=C).

### 4. Named tech verified-current (repo-pinned) — YES

Checked against the repo; all match:

| Spine Stack entry | Repo ground truth |
|---|---|
| Python `>=3.12,<3.13` | `requires-python = ">=3.12,<3.13"` ✓ |
| FastAPI `0.141` | `fastapi>=0.141,<0.142` ✓ |
| SQLAlchemy `2.x` | `sqlalchemy>=2.0,<3` ✓ |
| Vue `3.5.41` / Pinia `4.0.3` / Vite `8.2.2` / TS `6.0.3` | `frontend/package.json` ✓ |
| SQLite WAL, `data/mythos.db` | existing store ✓ |
| LLM owner-managed OpenAI-compatible, no change | AD-6/AD-14 ✓ |

"No new technology bound by this slice" is accurate — the shapes are tables,
one column, one rename, two read endpoints, a derived payload.

### 5. Ratifies the brownfield codebase — YES

- Tables named in the assignment (`campaign/revision/entity/edge/event/job/
  proposed_candidate/media/account/session`) all exist in `models.py`; the
  spine's new shapes (`entity_session_state`, `entity_knowledge_state`,
  `edge.reason`, `login_session` rename) are correctly presented as
  build-to-add, and the absence of all four in the repo was confirmed (grep:
  no hits) — i.e. the spine does not hallucinate existing support.
- `EDGE_KIND_RULES` / `edge_kind_ok` confirmed in `commit.py`; repo matrix
  baseline verified: 16-type `EDGE_TYPES` (incl. `relationship/debt/grudge/
  loyalty/member_of/located_in/rival_of/kin_of/ally_of/enemy_of/bases_at/
  controls/employs/worships/hails_from/protects`), `controls`/`employs` src
  currently `{character,faction}`, no `part_of`. AD-31's delta is therefore a
  true delta against this baseline — the build has a precise diff to apply.
- `edge_counter` semantics, ULIDs, revision-owns-events, compensating undo
  all consistent with `models.py` / parent AD-2.

### 6. Covers the driving v3 UX — YES, EXCEPT the supersession is silent (F-1)

Registry, guided regenerate/enrich, `part_of`, Tonight Tier-1 reads, Tier-2
verbs + knowledge are all governed. But the spine follows the 09-25 owner
verdicts (one logbook, 2-state toggle, committed rows in the shared stream)
while `EXPERIENCE.md` (status `final`, updated 2026-09-25) still specifies the
superseded 09-24 design: parallel tables "with their own revision entry"
(State Patterns, Tier-2 block, knowledge-chip/verb-row rows), 3-state
`hidden → shown-to-me → known-by-party`, and a "dedicated commit path".
The slice `.memlog.md` records the supersession; the spine itself never cites
it. A builder holding both "final" documents faces a direct contradiction on
the highest-risk decision in the slice.

### 7. No new AD weakens parent 08-23 ADs — YES, with one note

- AD-26 composes AD-1 (extends coverage to run-state, writer count stays
  one); the Inherited Invariants table explicitly traces each parent AD.
  AD-1 needs no amendment — the two-logbook lean was considered and rejected
  in memlog, correctly.
- AD-27 reuses AD-2 mechanics; AD-28 stays in AD-13's single DB / single
  transaction; AD-30/31/32 preserve AD-5 closed vocabulary and AD-23
  counters; AD-33 spends the AD-3 call budget; AD-35 inherits AD-9/AD-11.
- Note: AD-31 *widens* two existing matrix cells (`employs`/`controls` src
  += `place`) while parent AD-5 contemplates extension "by adding a type".
  Cell-widening is within AD-5's spirit (closed vocabulary, deterministic
  checks) and the spine contains it with "all other cells frozen" + pin
  test, so this is not a weakening — but the spine should say one sentence
  that AD-5 permits cell evolution under delta discipline, so a future
  reader does not read AD-5 as types-only.

### 8. Every altitude-owned dimension decided/deferred/open — MOSTLY YES, ops hand-waved (F-4)

Data, commit/undo, vocabulary, repair, registry, read surface, sequencing:
decided or explicitly gated. The exception is the Structural Seed's single
line "Operational envelope unchanged — nothing to decide at this altitude."
That is *probably* right (single machine, AD-8 stands; reads are small), but
three envelope-adjacent items go unowned: (a) `revisions?limit=N` has no max
— a large-N read against the event stream is the only new unbounded query in
the slice; (b) "kinds responses are cacheable" names no TTL/invalidation
(the matrix changes only at build, so "immutable until deploy" would close
it in one clause); (c) enrich/regenerate volume hits the same single FIFO —
covered by the call budget per job, but queue-position UX under repeated
fill-blank repairs is unmentioned. One short AD or convention line would
close all three.

## Findings

- **F-1 (MAJOR): Silent supersession of the "final" UX docs on Tier-2 state.**
  AD-26/28/29 implement the 09-25 one-logbook + 2-state + shared-stream
  verdicts, but `EXPERIENCE.md`/`DESIGN.md` (final, same date) still specify
  parallel tables with own revisions and 3-state knowledge. Evidence: slice
  `.memlog.md` decisions 2026-09-25 vs EXPERIENCE lines 218–234, 311, 315,
  340 and UX memlog 09-24 verdicts (2)/(3). Fix: add a Supersession note to
  the spine (one paragraph citing the 09-25 verdicts as overriding UX
  memlog 09-24 (2), EXPERIENCE Tier-2 spec, and the 3-state), and flag the UX
  docs for a conforming update. Without this, two build units will implement
  different Tier-2 stores, each citing a "final" source.
- **F-2 (MAJOR): Dial-home and lazy-render have no AD.**
  Owner-decided, load-bearing, cross-unit: dial lives in the record as a
  top-level key (`DIRECT_KEYS`+1, survives export + re-rolls; UX memlog Q3=C,
  EXPERIENCE Dial semantics), character dial = mechanics weight only (Q4=B),
  flat records never migrate — absent sections render empty + enrich nudge
  (Q5=C). The Capability Map assigns the area to AD-34/AD-5, but no Rule
  states the key, the export survival, or the no-migration render — so the
  writer, the renderer, and the exporter can diverge exactly the way AD-34
  was written to prevent for edges. The Deferred "defaults at read vs
  backfill" bullet defers part of this without a tie-break. Fix: one AD
  (dial-in-record + lazy render + read-defaults-win) or two short ones.
- **F-3 (MODERATE): Enrich request shape is inherited, not ruled.**
  EXPERIENCE Flow 4 pins enrich to the regenerate contract (`sections: null`
  + guide + dial on any kind, enabled by the registry vocab). The spine
  states "Enrich adds no job kind, no second pipeline" only in the Inherited
  Invariants table — a traceability row, not an enforceable Rule — and no AD
  binds the `(seed, record, requested, context, guide, dial)` function the UX
  requires. Fix: fold one sentence into AD-33 or the AD-15/19 inheritance
  ("enrich rides the regenerate payload with sections=null + guide + dial")
  so a builder cannot invent a second enrich path.
- **F-4 (MINOR): Operational envelope dismissed in one line.**
  "Nothing to decide at this altitude" leaves three small items unowned:
  revisions `limit=N` unbounded (only new unbounded read), kinds
  cacheability without TTL/invalidation (one clause — "immutable until
  deploy" — closes it), FIFO pressure from repeated repairs (per-job budget
  covers cost, queue UX unmentioned). Fix: one convention paragraph or a
  short AD-35 companion. Not pass-blocking alone.
- **F-5 (MINOR): AD-31 widens cells under an AD-5 written for type-adds.**
  Not a weakening in substance (closed vocabulary + deterministic checks +
  frozen cells + pin test preserve AD-5's intent), but the spine should state
  that AD-5 permits cell evolution under delta discipline. One sentence.
- **F-6 (MINOR, wording): AD-33 "grammar-enforced where backends allow".**
  Conditional enforcement on the highest-cost path. The take-only-X merge
  already guarantees the outcome server-side regardless of backend support —
  say so, and the softness disappears.

## Explicit non-issues (checked, clean)

- Stack versions all repo-verified; no drift.
- `part_of` pairs exclude people (`located_in` owns that) — matches owner
  verdict; no `based_in` alias — matches the rejection.
- Null-prose denylist cites the `canonicalize_null_prose` precedent —
  brownfield-consistent repair philosophy.
- Tier-1 ships with the revisions endpoint per owner verdict (1); the
  endpoint's absence from the old store (revision_events internal-only) is
  correctly the reason it is new surface.
- Slice numbering AD-26+ with no renumbering; paradigm "applied, not
  extended" is the right call and honestly stated.
- Archive/sandbox/capture-parse/kind-tabs correctly left to UX/build
  altitude.

## Recommended spine deltas (no rework)

1. Supersession paragraph (F-1).
2. New AD for dial-home + lazy render with read-defaults tie-break (F-2,
   absorbs the Deferred backfill bullet).
3. One sentence pinning enrich to the regenerate payload (F-3).
4. Envelope convention: revisions limit cap, kinds immutable-until-deploy,
   repair queue note (F-4).
5. AD-5 cell-evolution sentence; AD-33 fallback wording (F-5, F-6).
