<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import type { components } from '../api/schema'
import StatBlock from '../components/StatBlock.vue'
import { useAuthStore } from '../stores/auth'
import { useCandidatesStore } from '../stores/candidates'
import { useJobsStore } from '../stores/jobs'
import {
  asString,
  BOSS_FIELDS,
  EDGE_DIRECTIONS,
  EDGE_VOCAB,
  FIELD_LABELS,
  IDENTITY_FIELDS,
  LORE_FIELDS,
  WORLD_INTEGRATION_FIELDS as WORLD_FIELDS,
} from '../components/profile/profile'
import { useWorldStore } from '../stores/world'
import { connectJobSocket } from '../ws'
import type { WsMessage } from '../ws'

type Candidate = components['schemas']['CandidateResponse']

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string

const candidates = useCandidatesStore()
const jobs = useJobsStore()
const world = useWorldStore()

const loadError = ref<string | null>(null)
const ask = ref('')
const askError = ref<string | null>(null)
const asking = ref(false)
/** Last action failure, keyed to its candidate so it stays visible after the in-flight flag clears. */
const actionError = ref<{ id: string; message: string } | null>(null)
const actingId = ref<string | null>(null)
/** candidateId -> the in-flight re-roll: 'whole' or the section name. */
const rollingId = ref<Record<string, string>>({})
/** candidateId -> edit mode. */
const editing = ref<Record<string, boolean>>({})
/** candidateId -> draft section values ('field' or 'section.field' -> new text). */
const drafts = ref<Record<string, Record<string, string>>>({})
/** candidateId -> the draft's starting values (isEdited/editedPayload compare against these). */
const draftInitials = ref<Record<string, Record<string, string>>>({})
/** candidateId -> the DM's working edge list while editing. */
const edgeDrafts = ref<Record<string, StagedEdge[]>>({})
/** candidateId -> the payload the draft was started from (mid-edit swap detection). */
const draftBases = ref<Record<string, unknown>>({})
/** candidateId -> JSON of the initial edge list ("unchanged" reference). */
const edgeInitials = ref<Record<string, string>>({})
/** candidateId -> the add-relation form state. */
const edgeAdds = ref<Record<string, EdgeAddForm>>({})

interface EdgeAddForm {
  endpoint: string
  direction: string
  type: string
  counter: number
}

let disconnectSocket: (() => void) | null = null
/** candidateId -> the candidate changed server-side mid-edit; the draft was visibly discarded. */
const staleEdits = ref<Record<string, boolean>>({})

onMounted(async () => {
  try {
    await candidates.syncList(campaignId)
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load the candidates.'
    return
  }
  try {
    await jobs.syncList(campaignId)
  } catch {
    // Job status is a side panel, not the accept screen — the candidates
    // list is the reason this view exists; a jobs failure must not read
    // as a candidates failure.
  }
  // Endpoint names for the relation lines — best effort; ids are the fallback.
  world.load(campaignId).catch(() => undefined)
  disconnectSocket = connectJobSocket(
    campaignId,
    (message) => {
      void onJobMessage(message)
    },
    {
      onReconnect: () => {
        void resync()
      },
      onAuthFailure: () => {
        const auth = useAuthStore()
        auth.account = null
        void router.push({ name: 'login' })
      },
    },
  )
})

onUnmounted(() => {
  disconnectSocket?.()
})

/**
 * WS dispatch: the jobs store absorbs every frame; a generate job
 * settling re-syncs the candidates list (new rows appear, or an error
 * the job panel already shows) and — on success — clears the ask box
 * (the ask text survives a FAILED job so the DM can resubmit without
 * retyping; it is the DM's input, cleared only once it succeeded).
 */
async function onJobMessage(message: WsMessage) {
  await jobs.handleWsMessage(campaignId, message)
  if (message.type === 'job_done' || message.type === 'job_failed') {
    if (message.type === 'job_done' && jobs.byId[message.job_id]?.kind === 'generate') {
      ask.value = ''
    }
    await resync()
  }
}

/**
 * Re-sync the candidates list, then reconcile any open edit drafts.
 * The world snapshot refetches too (coalesced, fire-and-forget): the
 * edge-add picker and relation lines read committed entities, so a
 * build-in or accept committing mid-session must land here or the
 * picker omits just-committed entities (spec-3-4 review round 1).
 */
async function resync() {
  world.requestRefetch(campaignId)
  await candidates.syncList(campaignId)
  pruneStaleEdits()
}

/**
 * A re-sync can replace a candidate's payload while the DM is mid-edit.
 * A draft started from a different payload is DISCARDED VISIBLY — never
 * silently merged into data the DM never saw (spec-3-3 review round 2).
 */
function pruneStaleEdits() {
  for (const id of Object.keys(editing.value)) {
    const row = candidates.byId[id]
    const base = draftBases.value[id]
    if (!row || row.status !== 'proposed') {
      cancelEdit(id)
    } else if (base !== undefined && JSON.stringify(row.payload) !== JSON.stringify(base)) {
      cancelEdit(id)
      staleEdits.value[id] = true
    }
  }
}

function cancelEdit(id: string) {
  delete editing.value[id]
  delete drafts.value[id]
  delete draftInitials.value[id]
  delete draftBases.value[id]
  delete edgeDrafts.value[id]
  delete edgeInitials.value[id]
  delete edgeAdds.value[id]
}

const proposed = computed(() => candidates.proposed(campaignId))

/** Committed entity names for the relation lines (id fallback when the world is unavailable). */
const nameById = computed(() => {
  const names = new Map<string, string>()
  for (const entity of world.entry(campaignId).world?.entities ?? []) {
    names.set(entity.id, entity.name)
  }
  return names
})

/** Recent generate jobs — the ask box's visible progress and errors
 * (existing WS/REST job states; spec-3-3 EMPTY_QUEUE error handling). */
const recentGenerate = computed(() =>
  jobs
    .forCampaign(campaignId)
    .filter((job) => job.kind === 'generate')
    .slice(0, 5),
)

/** Recent regenerate jobs — re-roll progress/errors (spec-3.5). */
const recentRegenerate = computed(() =>
  jobs
    .forCampaign(campaignId)
    .filter((job) => job.kind === 'regenerate')
    .slice(0, 5),
)

/**
 * What a re-roll card is a re-roll OF, e.g. "Re-roll of Secret".
 *
 * A per-section re-roll stages the ENTIRE record, so the card cannot show
 * its own scope — the job's payload is where the requested sections live.
 * Null for a card that came from a plain generate ask (no regenerate job).
 */
function reRollLabel(candidate: Candidate): string | null {
  const job = jobs.byId[candidate.job_id]
  if (!job || job.kind !== 'regenerate') return null
  const sections = (job.payload as { sections?: string[] } | null)?.sections
  if (!sections?.length) return 'Re-roll of the whole character'
  return `Re-roll of ${sections.map((section) => FIELD_LABELS[section] ?? section).join(', ')}`
}

type Job = components['schemas']['JobResponse']

function stateText(job: Job): string {
  const label =
    job.state === 'queued' ? `Queued (position ${job.queue_position ?? '…'})` : job.state
  return job.error ? `${label}: ${job.error}` : label
}

async function submitAsk() {
  askError.value = null
  const text = ask.value.trim()
  if (!text) return
  asking.value = true
  try {
    // The ask text is NOT cleared here: it clears when the job succeeds
    // (WS job_done), so a failed job never costs the DM a retyping.
    await candidates.submitAsk(campaignId, text)
    await jobs.syncList(campaignId)
  } catch (err) {
    askError.value = err instanceof ApiError ? err.message : 'Could not enqueue the ask.'
  } finally {
    asking.value = false
  }
}

// ---------------------------------------------------------------------------
// The AR24 sectioned profile: known sections render, unknown keys are
// skipped (AR24 forward compatibility) — nothing here fails on extras.
// The field sets, labels, and edge vocabulary are the SHARED profile
// module (epic-3 retro item 2) — one home, both views (see
// ../components/profile/profile.ts).
// ---------------------------------------------------------------------------

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function sectionObject(
  candidate: Candidate,
  section: 'boss' | 'world_integration',
): Record<string, unknown> | null {
  const value = candidate.payload[section]
  return isObject(value) ? value : null
}

interface StagedEdge {
  endpoint: string
  direction: string
  type: string
  counter?: unknown
}

function edgesOf(candidate: Candidate): StagedEdge[] {
  const value = candidate.payload['edges']
  if (!Array.isArray(value)) return []
  return value.filter(
    (edge): edge is StagedEdge =>
      isObject(edge) && typeof edge['endpoint'] === 'string' && typeof edge['type'] === 'string',
  )
}

/** Relation lines follow WorldView's `src --label--> dst` convention; the
 * candidate sits on one side and the staged edges are read-only (3.4).
 * Forward compat: the counter renders whenever the edge carries one —
 * a counter-typed edge this client doesn't know must not hide it. */
function relationLine(candidate: Candidate, edge: StagedEdge): string {
  const label = typeof edge.counter === 'number' ? `${edge.type}(${edge.counter})` : edge.type
  const name = nameById.value.get(edge.endpoint) ?? edge.endpoint
  const self = asString(candidate.payload['name']) ?? '(candidate)'
  return edge.direction === 'outbound'
    ? `${self} --${label}--> ${name}`
    : `${name} --${label}--> ${self}`
}

// ---------------------------------------------------------------------------
// Story 3.4: the DM's edge draft. Staged relations can be re-countered,
// deleted, and new ones added — targeting COMMITTED world entities only
// (a staged candidate's edge endpoint must resolve to committed state,
// AR19). The draft rides the accept override; the backend validates the
// final list and an invalid edge fails the whole accept (row stays
// proposed).
// ---------------------------------------------------------------------------

/** Committed entities the candidate can wire into (world store snapshot). */
const worldEntities = computed(() => world.entry(campaignId).world?.entities ?? [])

/** The draft edge as a relation line (same convention as relationLine). */
function draftEdgeLine(candidate: Candidate, edge: StagedEdge): string {
  const label = typeof edge.counter === 'number' ? `${edge.type}(${edge.counter})` : edge.type
  const name = nameById.value.get(edge.endpoint) ?? edge.endpoint
  const self = asString(candidate.payload['name']) ?? '(candidate)'
  return edge.direction === 'outbound'
    ? `${self} --${label}--> ${name}`
    : `${name} --${label}--> ${self}`
}

function removeEdgeDraft(candidateId: string, index: number) {
  edgeDrafts.value[candidateId]?.splice(index, 1)
}

function addEdgeDraft(candidateId: string) {
  const form = edgeAdds.value[candidateId]
  if (!form || !form.endpoint || actingId.value !== null) return
  edgeDrafts.value[candidateId] = [
    ...(edgeDrafts.value[candidateId] ?? []),
    {
      endpoint: form.endpoint,
      direction: form.direction,
      type: form.type,
      counter: Number.isFinite(form.counter) ? form.counter : 1,
    },
  ]
  // Reset the form for a possible second add.
  edgeAdds.value[candidateId] = {
    endpoint: '',
    direction: 'outbound',
    type: EDGE_VOCAB[0],
    counter: 1,
  }
}

// ---------------------------------------------------------------------------
// Edit-before-accept: per-section textareas; the edited payload is what
// the accept commits. Story 3.4 adds the DM's edge set to the draft:
// staged relations can be re-countered, deleted, and new ones added
// (targeting committed world entities) — the edited list rides the
// accept override and commits with the candidate. Every known section
// gets a textarea; sections missing from the payload (legacy AR19 rows)
// start empty so they can be filled before accept.
// ---------------------------------------------------------------------------

const EDITABLE_SECTIONS: Array<{
  section: 'top' | 'boss' | 'world_integration'
  fields: readonly string[]
}> = [
  { section: 'top', fields: IDENTITY_FIELDS },
  { section: 'top', fields: LORE_FIELDS },
  { section: 'boss', fields: BOSS_FIELDS },
  { section: 'world_integration', fields: WORLD_FIELDS },
]

function draftKey(section: 'top' | 'boss' | 'world_integration', field: string): string {
  return section === 'top' ? field : `${section}.${field}`
}

function originalValue(
  candidate: Candidate,
  section: 'top' | 'boss' | 'world_integration',
  field: string,
): string | null {
  if (section === 'top') return asString(candidate.payload[field])
  const obj = sectionObject(candidate, section)
  return obj === null ? null : asString(obj[field])
}

function toggleEdit(candidate: Candidate) {
  const id = candidate.id
  if (editing.value[id]) {
    cancelEdit(id)
    return
  }
  const draft: Record<string, string> = {}
  for (const { section, fields } of EDITABLE_SECTIONS) {
    for (const field of fields) {
      draft[draftKey(section, field)] = originalValue(candidate, section, field) ?? ''
    }
  }
  editing.value[id] = true
  drafts.value[id] = { ...draft }
  draftInitials.value[id] = draft
  draftBases.value[id] = JSON.parse(JSON.stringify(candidate.payload))
  // The DM's working edge list: a deep copy of the staged edges, edited
  // inline (counter) and by add/delete. The initial JSON pins "unchanged".
  edgeDrafts.value[id] = edgesOf(candidate).map((edge) => ({ ...edge }))
  edgeInitials.value[id] = JSON.stringify(edgeDrafts.value[id])
  edgeAdds.value[id] = { endpoint: '', direction: 'outbound', type: EDGE_VOCAB[0], counter: 1 }
  delete staleEdits.value[id]
}

/** True when any draft value OR the edge draft differs from its start. */
function isEdited(candidate: Candidate): boolean {
  const draft = drafts.value[candidate.id]
  const initial = draftInitials.value[candidate.id]
  if (draft && initial) {
    if (Object.keys(draft).some((key) => draft[key] !== initial[key])) return true
  }
  const edgeDraft = edgeDrafts.value[candidate.id]
  if (edgeDraft !== undefined) {
    if (JSON.stringify(edgeDraft) !== edgeInitials.value[candidate.id]) return true
  }
  return false
}

/** The staged payload with the draft edits applied (deep copy). Only
 * values the DM actually changed are applied — untouched empty textareas
 * (sections a legacy row lacks) must not blank the override into a
 * guaranteed 422. */
function editedPayload(candidate: Candidate): Record<string, unknown> {
  const payload = JSON.parse(JSON.stringify(candidate.payload)) as Record<string, unknown>
  const draft = drafts.value[candidate.id] ?? {}
  const initial = draftInitials.value[candidate.id] ?? {}
  for (const [path, value] of Object.entries(draft)) {
    if (value === initial[path]) continue
    const [head, nested] = path.split('.')
    if (nested === undefined) {
      payload[head] = value
    } else if (isObject(payload[head])) {
      ;(payload[head] as Record<string, unknown>)[nested] = value
    }
  }
  // Story 3.4: the DM's edge draft rides the override — the backend
  // validates every edge against committed world state (verbatim list
  // is no longer required; an unchanged draft is byte-equal anyway).
  const edgeDraft = edgeDrafts.value[candidate.id]
  if (edgeDraft !== undefined) {
    payload['edges'] = edgeDraft
  }
  return payload
}

async function acceptCandidate(candidate: Candidate) {
  actionError.value = null
  if (acceptConflictFor.value[candidate.id]) {
    // A stale accept attempt while the dialog is open must not stack a
    // second rejection — close the dialog instead (either the conflict
    // resolved or the row settled).
    closeConflictDialog(candidate.id)
  }
  actingId.value = candidate.id
  // The accepted appearance rides the override when edited — capture it
  // BEFORE cancelEdit (which wipes the drafts).
  const override = isEdited(candidate) ? editedPayload(candidate) : undefined
  try {
    await candidates.accept(campaignId, candidate.id, override)
    cancelEdit(candidate.id)
  } catch (err) {
    if (isEditConflict(err) && candidate.regenerates_entity_id) {
      // spec-3.6: the target moved since staging — three-way dialog.
      openConflictDialog(candidate.id)
    } else {
      actionError.value = {
        id: candidate.id,
        message: err instanceof ApiError ? err.message : 'Could not accept the candidate.',
      }
    }
  } finally {
    actingId.value = null
  }
}

async function rejectCandidate(candidate: Candidate) {
  actionError.value = null
  actingId.value = candidate.id
  try {
    await candidates.reject(campaignId, candidate.id)
    cancelEdit(candidate.id)
  } catch (err) {
    actionError.value = {
      id: candidate.id,
      message: err instanceof ApiError ? err.message : 'Could not reject the candidate.',
    }
  } finally {
    actingId.value = null
  }
}

/**
 * Spec-3.5 re-roll: whole candidate (sections null) or one section.
 * A re-roll visibly discards any in-flight manual draft on that
 * candidate (3-3 precedent — never a silent merge); the row's payload
 * is replaced when the job lands (WS job_done -> resync). The draft is
 * discarded only AFTER the job enqueues successfully — a failed submit
 * (network/4xx/409) leaves the DM's manual draft and edit mode intact.
 * `actingId` gates the whole row's buttons while the roll is in flight
 * (a double-click can never fire two jobs for one intent).
 */
async function rollCandidate(candidate: Candidate, sections: string[] | null, label: string) {
  actionError.value = null
  actingId.value = candidate.id
  rollingId.value[candidate.id] = label
  try {
    await jobs.submitRegenerate(campaignId, { kind: 'candidate', id: candidate.id }, sections)
    await jobs.syncList(campaignId)
    if (editing.value[candidate.id]) {
      cancelEdit(candidate.id)
      staleEdits.value[candidate.id] = false
    }
  } catch (err) {
    actionError.value = {
      id: candidate.id,
      message: err instanceof ApiError ? err.message : 'Could not re-roll the candidate.',
    }
  } finally {
    delete rollingId.value[candidate.id]
    actingId.value = null
  }
}

// ---------------------------------------------------------------------------
// Accept-conflict dialog (spec-3.6 ACCEPT_CONFLICT / ACCEPT_ANYWAY): a
// regenerate-entity accept rejected because the target's committed record
// moved since staging opens a three-way choice — re-roll (the candidate
// re-roll in place, preserving staged edge edits), accept the generated
// version anyway (TWO-STEP confirm, then confirm_overwrite: true), or
// cancel (row stays proposed). The dialog keys on the edit-conflict
// message, NEVER on a bare conflict code — a CandidateSettledError from
// a double submit renders the plain card error instead.
// ---------------------------------------------------------------------------

const EDIT_CONFLICT_MARKER = 'changed since this candidate was generated'

/** candidateId -> the conflict dialog is open for this row. */
const acceptConflictFor = ref<Record<string, boolean>>({})
/** candidateId -> the accept-anyway two-step confirm is armed. */
const acceptArmed = ref<Record<string, boolean>>({})

function isEditConflict(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409 && err.message.includes(EDIT_CONFLICT_MARKER)
}

function openConflictDialog(candidateId: string) {
  acceptConflictFor.value[candidateId] = true
  acceptArmed.value[candidateId] = false
}

function closeConflictDialog(candidateId: string) {
  delete acceptConflictFor.value[candidateId]
  delete acceptArmed.value[candidateId]
}

function conflictTargetName(candidate: Candidate): string {
  const targetId = candidate.regenerates_entity_id
  if (!targetId) return 'The entity'
  return nameById.value.get(targetId) ?? 'The entity'
}

/** Three-way escape 1: re-roll the CANDIDATE in place against the latest
 * world (spec-3.6 RE_ROLL_AFTER_EDIT) — the row's staged edge edits
 * survive (3-4), the runner re-reads the target at staging, and the
 * base refresh makes the row accept-able again. */
async function conflictReroll(candidate: Candidate) {
  if (actingId.value !== null) return
  // The jobs-store in-flight discipline (spec-3-6): a regenerate for
  // this row still queued/running blocks a second enqueue (the second
  // replace_candidate_payload would win, burning a generation).
  if (jobs.regenerateInFlight(campaignId, 'candidate', candidate.id)) return
  actionError.value = null
  actingId.value = candidate.id
  rollingId.value[candidate.id] = 'whole'
  try {
    await jobs.submitRegenerate(campaignId, { kind: 'candidate', id: candidate.id }, null)
    await jobs.syncList(campaignId)
    // The row's payload was replaced server-side: discard any open edit
    // draft VISIBLY (the 3-3 rollCandidate precedent) — a draft seeded
    // from the pre-roll payload must never silently re-apply old section
    // values onto the re-rolled row at a later accept.
    if (editing.value[candidate.id]) {
      cancelEdit(candidate.id)
      staleEdits.value[candidate.id] = false
    }
    closeConflictDialog(candidate.id)
  } catch (err) {
    actionError.value = {
      id: candidate.id,
      message: err instanceof ApiError ? err.message : 'Could not re-roll the candidate.',
    }
  } finally {
    delete rollingId.value[candidate.id]
    actingId.value = null
  }
}

/** Three-way escape 2: accept the generated version anyway. Two-step
 * confirm (the destructive confirmation precedent): the first click
 * arms, the second sends confirm_overwrite. */
async function conflictAcceptAnyway(candidate: Candidate) {
  if (actingId.value !== null) return
  if (!acceptArmed.value[candidate.id]) {
    acceptArmed.value[candidate.id] = true
    return
  }
  actionError.value = null
  actingId.value = candidate.id
  const override = isEdited(candidate) ? editedPayload(candidate) : undefined
  try {
    await candidates.accept(campaignId, candidate.id, override, true)
    cancelEdit(candidate.id)
    closeConflictDialog(candidate.id)
  } catch (err) {
    actionError.value = {
      id: candidate.id,
      message: err instanceof ApiError ? err.message : 'Could not accept the candidate.',
    }
  } finally {
    actingId.value = null
    acceptArmed.value[candidate.id] = false
  }
}

/** Re-roll label text for a section while a job is in flight. */
function rollLabel(candidateId: string, section: string): string {
  return rollingId.value[candidateId] === section ? 'Re-rolling…' : 'Re-roll'
}

</script>

<template>
  <section>
    <h1>Candidates</h1>

    <div v-if="loadError" class="card">
      <p class="error">{{ loadError }}</p>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>
    <template v-else>
      <form class="card ask" @submit.prevent="submitAsk">
        <h2 id="ask-label">Ask for something new</h2>
        <textarea
          v-model="ask"
          rows="2"
          aria-labelledby="ask-label"
          placeholder="A rival for Mira, with a secret that pays off later…"
        ></textarea>
        <p v-if="askError" class="error">{{ askError }}</p>
        <button type="submit" :disabled="asking || ask.trim() === ''">
          {{ asking ? 'Asking…' : 'Ask' }}
        </button>
        <p
          v-for="job in recentGenerate"
          :key="job.id"
          class="muted small"
          :class="{ failed: job.state === 'failed' }"
        >
          Ask — {{ stateText(job) }}
        </p>
        <p
          v-for="job in recentRegenerate"
          :key="job.id"
          class="muted small"
          :class="{ failed: job.state === 'failed' }"
        >
          Re-roll — {{ stateText(job) }}
        </p>
      </form>

      <p v-if="proposed.length === 0" class="muted">
        No candidates waiting — ask above and they will appear here when the job completes.
      </p>

      <article v-for="candidate in proposed" :key="candidate.id" class="card candidate">
        <header class="candidate-head">
          <h3>{{ candidate.payload['name'] }}</h3>
          <p class="muted role">
            {{ asString(candidate.payload['role']) ?? '' }}
            <template v-for="field in IDENTITY_FIELDS" :key="field">
              <template v-if="asString(candidate.payload[field])">
                · {{ FIELD_LABELS[field] }}: {{ candidate.payload[field] }}
              </template>
            </template>
          </p>
          <!-- A re-roll of one section stages the WHOLE record, so without
               this badge two cards for one character are indistinguishable
               (2026-09-11: a stat-block card and a catchphrases card sat side
               by side reading near-identically). The job payload is the only
               place the requested sections are recorded. -->
          <p v-if="reRollLabel(candidate)" class="muted small re-roll-badge">
            {{ reRollLabel(candidate) }}
          </p>
        </header>

        <p v-if="staleEdits[candidate.id]" class="error sync-note">
          The candidate changed while you were editing — your draft was discarded. Review and edit
          again before accepting.
        </p>

        <div class="sections">
          <!-- Narrative lore -->
          <dl>
            <template v-for="field in LORE_FIELDS" :key="field">
              <div v-if="editing[candidate.id]" class="edit-row">
                <dt>{{ FIELD_LABELS[field] }}</dt>
                <dd>
                  <textarea
                    v-model="drafts[candidate.id][field]"
                    rows="3"
                    :aria-label="FIELD_LABELS[field]"
                  ></textarea>
                </dd>
              </div>
              <div v-else-if="asString(candidate.payload[field])" class="view-row">
                <dt>{{ FIELD_LABELS[field] }}</dt>
                <dd class="text">{{ candidate.payload[field] }}</dd>
                <button
                  type="button"
                  class="link re-roll"
                  :disabled="actingId !== null"
                  @click="rollCandidate(candidate, [field], field)"
                >
                  {{ rollLabel(candidate.id, field) }}
                </button>
              </div>
            </template>
          </dl>

          <!-- Mechanics -->
          <div v-if="isObject(candidate.payload['stat_block'])" class="subblock">
            <h4>
              Stat block
              <button
                v-if="!editing[candidate.id]"
                type="button"
                class="link re-roll"
                :disabled="actingId !== null"
                @click="rollCandidate(candidate, ['stat_block'], 'stat_block')"
              >
                {{ rollLabel(candidate.id, 'stat_block') }}
              </button>
            </h4>
            <StatBlock :block="candidate.payload['stat_block']" />
          </div>

          <!-- Conditional boss section -->
          <div v-if="editing[candidate.id] || sectionObject(candidate, 'boss')" class="subblock">
            <h4>
              Boss
              <button
                v-if="!editing[candidate.id]"
                type="button"
                class="link re-roll"
                :disabled="actingId !== null"
                @click="rollCandidate(candidate, ['boss'], 'boss')"
              >
                {{ rollLabel(candidate.id, 'boss') }}
              </button>
            </h4>
            <dl>
              <template v-for="field in BOSS_FIELDS" :key="field">
                <div v-if="editing[candidate.id]" class="edit-row">
                  <dt>{{ FIELD_LABELS[field] }}</dt>
                  <dd>
                    <textarea
                      v-model="drafts[candidate.id][`boss.${field}`]"
                      rows="3"
                      :aria-label="FIELD_LABELS[field]"
                    ></textarea>
                  </dd>
                </div>
                <div
                  v-else-if="asString(sectionObject(candidate, 'boss')?.[field])"
                  class="view-row"
                >
                  <dt>{{ FIELD_LABELS[field] }}</dt>
                  <dd class="text">{{ sectionObject(candidate, 'boss')?.[field] }}</dd>
                </div>
              </template>
            </dl>
          </div>

          <!-- World integration -->
          <div
            v-if="editing[candidate.id] || sectionObject(candidate, 'world_integration')"
            class="subblock"
          >
            <h4>
              World integration
              <button
                v-if="!editing[candidate.id]"
                type="button"
                class="link re-roll"
                :disabled="actingId !== null"
                @click="rollCandidate(candidate, ['world_integration'], 'world_integration')"
              >
                {{ rollLabel(candidate.id, 'world_integration') }}
              </button>
            </h4>
            <dl>
              <template v-for="field in WORLD_FIELDS" :key="field">
                <div v-if="editing[candidate.id]" class="edit-row">
                  <dt>{{ FIELD_LABELS[field] }}</dt>
                  <dd>
                    <textarea
                      v-model="drafts[candidate.id][`world_integration.${field}`]"
                      rows="3"
                      :aria-label="FIELD_LABELS[field]"
                    ></textarea>
                  </dd>
                </div>
                <div
                  v-else-if="asString(sectionObject(candidate, 'world_integration')?.[field])"
                  class="view-row"
                >
                  <dt>{{ FIELD_LABELS[field] }}</dt>
                  <dd class="text">{{ sectionObject(candidate, 'world_integration')?.[field] }}</dd>
                </div>
              </template>
            </dl>
          </div>

          <!-- Relations: the DM's edge draft in edit mode (3.4); read-only lines otherwise -->
          <div v-if="editing[candidate.id] || edgesOf(candidate).length > 0" class="relations">
            <h4>Relations</h4>
            <template v-if="editing[candidate.id]">
              <p v-if="(edgeDrafts[candidate.id] ?? []).length === 0" class="muted">
                No relations staged — add at least one or the accept will fail (a new entity must
                weave into the world).
              </p>
              <p v-for="(edge, index) in edgeDrafts[candidate.id] ?? []" :key="index" class="mono">
                {{ draftEdgeLine(candidate, edge) }}
                <input
                  v-model.number="edge.counter"
                  class="counter-input"
                  type="number"
                  aria-label="Counter"
                />
                <button
                  type="button"
                  class="link"
                  :disabled="actingId !== null"
                  @click="removeEdgeDraft(candidate.id, index)"
                >
                  Delete
                </button>
              </p>
              <form class="add-relation" @submit.prevent="addEdgeDraft(candidate.id)">
                <select v-model="edgeAdds[candidate.id].direction" aria-label="Direction">
                  <option v-for="direction in EDGE_DIRECTIONS" :key="direction" :value="direction">
                    {{ direction }}
                  </option>
                </select>
                <select v-model="edgeAdds[candidate.id].type" aria-label="Relation type">
                  <option v-for="edgeType in EDGE_VOCAB" :key="edgeType" :value="edgeType">
                    {{ edgeType }}
                  </option>
                </select>
                <select v-model="edgeAdds[candidate.id].endpoint" aria-label="Target entity">
                  <option value="" disabled>Choose an entity…</option>
                  <option v-for="target in worldEntities" :key="target.id" :value="target.id">
                    {{ target.name }}
                  </option>
                </select>
                <input
                  v-model.number="edgeAdds[candidate.id].counter"
                  class="counter-input"
                  type="number"
                  aria-label="Counter"
                />
                <button type="submit" :disabled="!edgeAdds[candidate.id].endpoint">Add</button>
              </form>
            </template>
            <template v-else>
              <p v-for="(edge, index) in edgesOf(candidate)" :key="index" class="mono">
                {{ relationLine(candidate, edge) }}
              </p>
            </template>
          </div>
        </div>

        <p v-if="actionError && actionError.id === candidate.id" class="error">
          {{ actionError.message }}
        </p>

        <div v-if="acceptConflictFor[candidate.id]" class="conflict-dialog">
          <p class="error">
            <strong>{{ conflictTargetName(candidate) }}</strong> changed since this candidate was
            generated. Re-roll against the latest world, accept the generated version anyway
            (overwriting your edit), or cancel.
          </p>
          <p v-if="acceptArmed[candidate.id]" class="muted confirm-hint">
            Accepting overwrites your edit. This is undoable via the previous revision. Click again
            to confirm.
          </p>
          <div class="actions">
            <button
              type="button"
              :disabled="
                actingId !== null || jobs.regenerateInFlight(campaignId, 'candidate', candidate.id)
              "
              @click="conflictReroll(candidate)"
            >
              {{
                rollingId[candidate.id] === 'whole' ? 'Re-rolling…' : 'Re-roll against latest world'
              }}
            </button>
            <button
              type="button"
              :disabled="actingId !== null"
              @click="conflictAcceptAnyway(candidate)"
            >
              {{
                acceptArmed[candidate.id] ? 'Confirm overwrite' : 'Accept generated version anyway'
              }}
            </button>
            <button
              type="button"
              :disabled="actingId !== null"
              @click="closeConflictDialog(candidate.id)"
            >
              Cancel
            </button>
          </div>
        </div>

        <!-- Reject / Edit / Re-roll / Accept: one tap each, equal prominence —
             accept is never the path of least resistance (inversion #2). -->
        <div class="actions">
          <button type="button" :disabled="actingId !== null" @click="rejectCandidate(candidate)">
            Reject
          </button>
          <button type="button" :disabled="actingId !== null" @click="toggleEdit(candidate)">
            {{ editing[candidate.id] ? 'Cancel edit' : 'Edit' }}
          </button>
          <button
            type="button"
            :disabled="actingId !== null"
            @click="rollCandidate(candidate, null, 'whole')"
          >
            {{ rollingId[candidate.id] === 'whole' ? 'Re-rolling…' : 'Re-roll' }}
          </button>
          <button type="button" :disabled="actingId !== null" @click="acceptCandidate(candidate)">
            {{ editing[candidate.id] && isEdited(candidate) ? 'Accept edited' : 'Accept' }}
          </button>
        </div>
      </article>
    </template>
  </section>
</template>

<style scoped>
.ask {
  display: grid;
  gap: 0.75rem;
  max-width: 42rem;
}
.ask h2 {
  margin: 0;
}
.ask textarea {
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
  resize: vertical;
}
.candidate {
  max-width: 42rem;
  margin-top: 1rem;
}
.candidate-head h3 {
  margin: 0.25rem 0;
}
.role {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
}
dl {
  margin: 0.25rem 0;
}
dt {
  color: #9aa0a6;
  font-size: 0.85rem;
}
dd {
  margin: 0.1rem 0 0.1rem 0;
}
.text {
  white-space: pre-wrap;
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
  margin: 0.1rem 0;
}
.subblock {
  border-left: 3px solid #2c3038;
  padding-left: 0.75rem;
  margin-top: 0.5rem;
}
.subblock h4 {
  margin: 0.25rem 0;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #9aa0a6;
}
.relations h4 {
  margin: 0.5rem 0 0.25rem;
  font-size: 0.85rem;
  color: #9aa0a6;
}
.edit-row textarea {
  width: 100%;
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
  resize: vertical;
}
.sync-note {
  border: 1px solid #2c3038;
  border-left: 3px solid #ff7b72;
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
}
.failed {
  color: #ff7b72;
}
.actions {
  display: flex;
  gap: 0.75rem;
  margin-top: 0.75rem;
}
.link {
  margin-left: 0.4rem;
  padding: 0;
  border: none;
  background: none;
  color: #58a6ff;
  cursor: pointer;
  font-size: 0.85rem;
}
.link:disabled {
  color: #484f58;
  cursor: default;
}
.re-roll {
  margin-left: 0;
  float: right;
}
.counter-input {
  width: 5rem;
  margin-left: 0.4rem;
}
.add-relation {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 0.4rem 0;
}
.add-relation select,
.add-relation input {
  font-size: 0.85rem;
}
.relations .error {
  font-size: 0.85rem;
}

.conflict-dialog {
  border: 1px solid #2c3038;
  border-left: 3px solid #d29922;
  border-radius: 6px;
  padding: 0.6rem 0.75rem;
  margin: 0.5rem 0;
}
.conflict-dialog .actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.conflict-dialog .confirm-hint {
  font-size: 0.8rem;
  margin: 0.4rem 0 0;
}
</style>
