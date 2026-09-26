# Adversary Review — v3 Slice Spine (AD-26..AD-35)

Scope: `_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-09-25/ARCHITECTURE-SPINE.md`
Parent (read-only reference): `architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md` (AD-1..AD-25).
Mode: attack only. No repairs made, no spine edits. No linters/tests run.

## Verdict: REJECT (as written) — 6 compatible-yet-divergent pairs, each needing a new or tightened AD

Method: for each hole below, Units Alpha and Beta each obey every AD to the letter
(including all inherited ADs). They still cannot interoperate or substitute for each
other — different DB content, different commit histories, different accept/reject
outcomes for identical DM input. Every pair is therefore a missing constraint, not a
builder error.

---

## Hole 1 — Rows-vs-log authority (AD-26 + AD-28): cache pair diverges on rewind, rebuild, export

- **Unit Alpha ("rows are cache"):** `entity_session_state` / `entity_knowledge_state`
  rows are a materialization of the `event` stream. Same-transaction write (AD-28)
  satisfied; rebuild-the-rows-from-events is always legal; on any doubt re-derive.
  Readers join rows, but rows carry no authority beyond speed.
- **Unit Beta ("rows are truth"):** rows are first-class committed state
  (AD-28's own words: "first-class committed rows"). The event is audit; the row is
  what Roster/Cast reads. Re-deriving rows from events is forbidden — it would
  destroy DM intent not captured in event payloads.
- **Both comply:** AD-26 says only "shared revision + event stream, no second log".
  AD-28 says only "written in the same transaction as their events". Neither states
  which side wins when row and event disagree, whether a rebuild-from-log migration
  is legal, or what the event payload must contain to make a rebuild faithful.
- **Collision:** Alpha survives a "rebuild state rows from log" migration with zero
  loss; Beta loses data (or refuses the migration). A backup/restore or
  export-then-reimport built by Alpha omits/reconstructs rows one way; Beta's omits
  them another. AD-11 ("exporter reads entity/edge only — state rows and the log
  never leak") makes this worse: after an export/import round-trip, Alpha's Tonight
  roster is empty-but-rebuildable while Beta's Tonight roster is empty-and-gone —
  both "compliant".
- **Needs:** new/tightened AD naming the authority (rows xor log), the mandatory
  event payload for every verb/toggle (enough to rebuild the row or explicitly
  not), and whether rebuild-from-log is legal or forbidden.

## Hole 2 — Take-back vs later canon edits: two compliant undo orders (AD-27 + AD-2)

- **Unit Alpha ("surgical inverse"):** take-back appends inverse deltas of exactly
  its own transaction (AD-27). Later commits stand = later deltas are left
  byte-identical; the inverse is applied field-by-field onto current state.
- **Unit Beta ("revert-to-before-image"):** take-back restores the before-image of
  the fields its transaction touched. Later commits stand = later commits to
  *other* fields stand, but a later canon edit to the *same* field is overwritten
  by the restore — it "stood" until the take-back legitimately reclaimed its field.
- **Both comply:** AD-27's sentence "later commits stand" does not define
  field-level vs transaction-level standing, nor what happens when a verb's inverse
  and a later guided-regenerate/enrich touch the same entity field. AD-2's
  "compensating commit that reverts that revision's state deltas" is equally
  satisfiable by both readings.
- **Collision:** verb wounds dragon (hp 40→28), DM then enriches the same dragon's
  description/hp text via guided regenerate (hp line rewritten to 35), DM then
  takes back the verb. Alpha: hp = 35-12 = 23 (inverse delta applied). Beta:
  hp = 40 (before-image restored, enrich silently eaten). Same ADs, different world
  state, and AD-27's "renders as edited" rule hides which one happened from the
  recent-changes feed.
- **Needs:** tightened AD-27 defining take-back/later-edit precedence at field
  granularity (delta-replay vs before-image, same-field conflict rule, and what the
  `edited` rendering must disclose).

## Hole 3 — Toggle-vs-record-edit: one step or two, whose undo (AD-29 + AD-2 + AD-27)

- **Unit Alpha ("toggle is its own transaction"):** each knowledge flip
  (`secret`→`known`, AD-29 "one undoable step each way") is a standalone revision.
  Take-back of a toggle never touches the entity record (AD-29: "the record never
  changes — only the marker moves").
- **Unit Beta ("toggle rides the edit transaction"):** a DM edit that rewrites an
  entity's secret text and flips its marker in one save is one subgraph commit
  (AD-2 atomicity; AD-28 same-transaction precedent). Its take-back inverts both
  halves together — record text and marker move as one step.
- **Both comply:** AD-29 mandates "one undoable step each way" but never binds the
  step to a transaction boundary; AD-2 mandates atomic commits but never forbids
  bundling a record edit with a marker flip; AD-27 covers "verb commits and their
  take-backs" and is silent on whether toggles take-back like verbs at all.
- **Collision:** DM rewrites the secret ("the barkeep is a spy" → "the barkeep is
  a victim") and marks it known in one gesture, then takes back. Alpha: two
  revisions, two take-backs; taking back "the toggle" leaves the rewritten text
  known — party now knows a secret the DM thinks was untold. Beta: one revision,
  one take-back; text and marker revert together. Incompatible histories, opposite
  party-knowledge outcomes, both "one undoable step".
- **Needs:** new/tightened AD fixing toggle transaction granularity (standalone vs
  bundleable), whether toggle take-back exists and what it inverts, and ordering
  when a record edit and a toggle land in the same gesture.

## Hole 4 — Registry "renders" without a freshness contract (AD-34 + AD-31 + AD-35)

- **Unit Alpha ("render once, serve long"):** `GET /kinds` renders
  `EDGE_KIND_RULES` at startup into a payload; AD-35 explicitly blesses
  "cacheable". Pickers read the cached payload. "Nothing writes it at runtime"
  (AD-34) is trivially satisfied — nothing does.
- **Unit Beta ("render per request"):** `GET /kinds` derives from the live matrix
  constant on every call; frontend revalidates before each picker open.
- **Both comply:** AD-34 constrains only direction (renders, never owns) and
  runtime writability. It sets no freshness bound, no cache TTL/invalidation, no
  version/ETag, and no definition of "derived from the same source" (AD-34) for
  per-kind availability. AD-31's "one matrix edit propagates to prompts,
  validators, pickers, and the pin test" names four consumers but no mechanism or
  deadline.
- **Collision:** matrix ships `part_of` place→place. Alpha's cached kinds payload
  (or a pinned frontend bundle) still offers/forbids the old cells; the accept-time
  commit path (live matrix) drops what the picker offered — the exact
  "accept-time rejection class" AD-34 claims to prevent, reproduced with full
  compliance. The pin test passes in both units (it pins whatever each unit
  renders) so it cannot catch the drift.
- **Needs:** tightened AD-34 with a freshness/version contract (payload carries
  matrix version; pickers validate or revalidate; cache bound), plus a definition
  of the availability-derivation function so two builders compute the same cells.

## Hole 5 — Reason validation: blank-on-create only, NULL forever (AD-32 + AD-33)

- **Unit Alpha ("create-gate only"):** commit rejects blank reason on *new* edges
  (AD-32's literal words); edits (counter bumps, retargets, take-back replays,
  repair merges) never revalidate; grandfathered `NULL`s persist indefinitely.
- **Unit Beta ("create-gate plus repair-fill"):** commit rejects blank on new
  edges AND the AD-33 repair loop fills blank reasons via fill-blank; grandfathered
  rows get reasons on next touch.
- **Both comply:** AD-32 binds rejection to "new edges" and grandfathers
  pre-reason rows with no sunset, no backfill rule (the Deferred section explicitly
  punts "records predating reason fields: defaults at read vs backfill — build
  detail"). AD-33's fill-blank guardrails are scoped to "per blank" generation
  fields; whether edge `reason` is a fillable blank, and whether `""` counts as
  blank (the null-prose list names `none`, `n/a`, `unknown`, `...` — not the empty
  string), is undefined.
- **Collision:** same imported/grandfathered world: Alpha keeps `NULL` reasons
  forever (reason column optional in practice); Beta fills or rejects them on next
  touch (reason effectively required). A `part_of` edge with `reason=""` commits
  under Alpha's reading, fails under Beta's. The Deferred "build detail" punt is
  doing load-bearing work: the two builds' edge tables diverge with identical ADs.
- **Needs:** tightened AD-32 (edit-path rule; `NULL`/empty-string sunset or
  permanent-grandfather verdict; whether reason participates in AD-33 repair) and
  closure of the blank vocabulary (`""`, whitespace).

## Hole 6 — Repair-merge discards "the rest" — reason rides along or dies (AD-33 + AD-32)

- **Unit Alpha ("field-X-only merge"):** repair asks for the missing field alone,
  "code merges field X alone and discards the rest" (AD-33). A repaired edge's
  accompanying model-written reason is discarded with "the rest"; the stashed
  original (possibly blank) reason stands — then AD-32's create-gate rejects the
  edge it just repaired.
- **Unit Beta ("edge-shape exception"):** edges are exempt from field-X-only
  discipline: reason is re-asked/stashed alongside because AD-32 requires "the
  model writes one reason per edge". The merge keeps reason from the repair reply.
- **Both comply:** AD-33's merge rule is stated for entity fields ("missing
  field"); its application to edge `reason` is unspecified. AD-32 requires a
  model-written reason per edge but never says which model call (wave-1 vs repair)
  may author it, nor whether repair replies may carry one. "Malformed → existing
  re-derive repair" vs "missing-field → fill-blank" classification of a
  blank-reason edge is unassigned — either bucket is defensible.
- **Collision:** wave-1 emits a valid `employs` edge with blank reason. Alpha:
  fill-blank asks for reason alone, discards everything else, then either commits
  a reason-only fragment or drops a good edge with audit noise. Beta: re-derives
  the whole edge and commits clean. Same budget (both "count against the job call
  budget"), opposite outcomes: one build's repair loop systematically voids
  place-hire edges the other's saves.
- **Needs:** tightened AD-33 classifying reason-repair (fill-blank vs re-derive),
  stating what a single-field repair reply may/must contain for edges, and
  reconciling "discards the rest" with AD-32's per-edge reason mandate.

---

## Secondary gaps (no full pair constructed; each still admits two readings)

1. **`session` rename blast radius (AD-28).** "The login session table renames to
   `login_session`" — owner of the rename is "the build", but readers of the old
   name (auth middleware, WebSocket queue position per AD-3, frontend) are
   unenumerated. A builder that renames table-only vs one that renames every
   reference both comply; one breaks auth.
2. **Revision-read shape (AD-35).** "Meta + event summaries only" — event-summary
   schema undefined. A summary carrying full verb payloads (hp deltas, secret
   text) vs one carrying opaque ids both comply; the former leaks party secrets
   to any campaign reader, the latter starves the recent-changes feed AD-27
   depends on.
3. **`part_of` counter semantics (AD-31 + AD-23).** AD-23 defines per-type counter
   semantics and says reason is "alongside, never instead"; the new `part_of`
   type ships with no counter semantics (membership since when? intensity?).
   Counter-required vs counter-meaningless readings both pass "counter semantics
   untouched" — because nothing was specified to leave untouched.
4. **Enrich's transaction identity (AD-33 + AD-2).** Enrich "adds no job kind, no
   second pipeline" — but whether an enrich accept is a regeneration-in-place
   (same ULID, AD-2's replace-in-place sentence) or a new revision of the same
   entity with edge re-targeting forbidden is unstated. In-place vs
   replace-with-same-ULID builders diverge on media/inbound-edge survival.
5. **Take-back rendering vs filtering (AD-27 + AD-35).** "Both render as `edited`
   in recent changes — the feed never distinguishes undo from edit." A builder
   that drops take-back entries from revision reads (they're "just" compensating
   commits) vs one that lists them as `edited` both claim compliance; auditability
   of the table's own history differs completely.

## What would close the gate

Six tightenings (one per hole), each a sentence or two at spine altitude — authority
declaration (Hole 1), take-back precedence (Hole 2), toggle granularity (Hole 3),
registry freshness/version (Hole 4), reason lifecycle incl. `NULL` sunset and `""`
(Hole 5), reason-repair classification (Hole 6) — plus schema/shape binds for the
secondary items (event-summary schema, `part_of` counter, enrich identity). No
structural change to the slice; the paradigm and AD sequence stand.
