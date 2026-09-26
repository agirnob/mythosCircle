# Handover — v3 slice build (prepared 2026-09-25)

Fresh session: implement `spec-v3-slice-backend.md` (status: `ready-for-dev`).
Render the build workflow first, then follow its step-03:
`_bmad/render/bmad-build/mythoscircle-664cbd4a5101/55a0b0a7b79b9c8822bb/step-03-implement.md`
(read fully; step-file architecture — never load two step files at once).

## 1. What this project is

mythosCircle: local, privacy-first TTRPG world/NPC generator for DMs. DM keeps
the wheel — drafts + accept, authored words commit verbatim, event-sourced
immutable world (one commit path, compensating undo). Python 3.12 + FastAPI +
SQLAlchemy 2 + SQLite WAL backend; Vue 3.5 + Vite + TS + Pinia frontend; local
llama.cpp LLM. Solo repo: commit + push directly, no PR gate. Never write world
state outside `backend/app/store/`. Full law + tokens: v3 DESIGN.md; behavior:
v3 EXPERIENCE.md (paths §5).

## 2. Session status — today 2026-09-25, three phases done

- **UX finalize.** v3 spines were `draft` with two cleanups open. Owner
  redirected the palette (kept voice + typography): Rustic Dark Grimoire —
  base `#161311`, sunken `#120f0d`, raised `#211c18`, overlay `#2b241f`,
  terracotta `#b8522b`/`#a04422` will-fire only, brass `#c8923c`
  tags/pills/highlights only, ivory `#ebdcc9` + iron-gall `#a89885` text
  (all pairs AA-verified, tightest white-on-terracotta 4.9:1). Faction Scope
  slider dropped; stale disabled token aligned to `#858585`. Both spines
  `status: final`. Voice/typography unchanged (chamberlain, serif-names,
  mono-machine). Cool-slate `selected`, borders, blue link carried — warm
  re-tint open on owner call.
- **Architecture run** (coaching path, owner chose it over fast). New slice
  workspace inheriting parent 08-23 (AD-1..25 binding, read-only).
  Distilled `ARCHITECTURE-SPINE.md`, `status: final`, AD-26..AD-38.
  Reviewer gate ran full: lint clean (one known `{id}` route-param false
  positive), rubric CONDITIONAL→fixed (supersession note, AD-36/37/38,
  operational bounds), verify PASS-minor (named the `direct.py:431` AR25
  deferred-import exception), adversary REJECT→6 holes + 5 gaps all bound.
- **Build spec.** `spec-v3-slice-backend.md`, `status: ready-for-dev`,
  frozen intent + 13 tasks + GWT acceptance + Code Map with line anchors.
  CHECKPOINT 1 approved with one clarification: dial stays (owner briefly
  confused it with the removed Size slider — dial is the live record setting;
  slider was the place-template trim).

## 3. Key decisions with rationale (all in spine + memlogs)

One logbook (divergence unrepresentable); take-back = surgical per-transaction
inverse rendering as `edited`, never filtered; committed state rows
(`entity_session_state`, `entity_knowledge_state`), readers dumb;
`session` = tonight's table, login table → `login_session` (table+model+all
refs + migration); knowledge = secret↔known toggle (shown-to-me dropped);
strict matrix kept (no swamp); employs/controls += place src, part_of =
{place,faction}→{place,faction}, neutral counter; saved edge reasons
(non-blank on create+retarget, NULL grandfathered); fill-blank repair
(single-field schema, take-only-X, null-prose denylist; one attempt, then
drop-with-audit; ≥1-edge floor fails thin candidates loud); code-wins
registry (version token, re-fetch per mount); read endpoints owned + bounded
(revisions default 20/max 100); dial+archetype in record (AD-36); flat stays
flat (AD-37); enrich = shaped regenerate (AD-38).

## 4. Open — Ask-First at build time (spec §Boundaries)

Backfill vs read-time defaults for dial/archetype/reason; revisions
default/max tuning; `login_session` migration handling on the deployed stack.
HALT and ask on any of these — do not invent.

## 5. Warnings (paid for earlier)

- Tree is dirty: owner's uncommitted frontend rebuild (`App.vue`, `main.ts`,
  `router*`, `components/shell|ui`, `styles`, `New*Views`) + untracked
  planning dirs. Owner cleared backend-only work; DO NOT touch frontend files.
- Harness scout model route was down this session (all 4 investigation scouts
  failed on the QAT-GGUF endpoint); Code Map was built inline via grep — spot
  check anchors if behavior looks off.
- Live DB is WAL: never `cp` it; `sqlite3 data/mythos.db ".backup dest"`.
  Dev api hub service: `mythos-api` (cwd backend). Full stack facts: memory.
- No `npm run gen:api` in this slice (backend-only; frontend types untouched).

## 6. Artifact index

- Spec: `_bmad-output/implementation-artifacts/spec-v3-slice-backend.md`
  (`ready-for-dev`) ← start here
- Spine: `_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-09-25/ARCHITECTURE-SPINE.md`
  (`final`) + `.memlog.md` (26 entries) + `reviews/` (rubric, verify-current, adversary)
- UX: `_bmad-output/planning-artifacts/ux-designs/ux-mythosCircle-2026-09-23/`
  `DESIGN.md` + `EXPERIENCE.md` (`final`), `.memlog.md` (85 entries),
  `HANDOVER-REPORT.md` (prior-session brief)
- Parent spine: `_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md`
  (`final`, AD-1..25 binding)
- Workflow: `_bmad/render/bmad-build/mythoscircle-664cbd4a5101/55a0b0a7b79b9c8822bb/step-03-implement.md`

Next: fresh session reads the spec + step-03 and dispatches the implementer.
Do not re-run UX/architecture — both are final.
