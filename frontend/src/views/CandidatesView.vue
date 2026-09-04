<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import type { components } from '../api/schema'
import { useAuthStore } from '../stores/auth'
import StatBlock from '../components/StatBlock.vue'
import { useCandidatesStore } from '../stores/candidates'
import { useJobsStore } from '../stores/jobs'
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
/** candidateId -> edit mode. */
const editing = ref<Record<string, boolean>>({})
/** candidateId -> draft section values ('field' or 'section.field' -> new text). */
const drafts = ref<Record<string, Record<string, string>>>({})
/** candidateId -> the draft's starting values (isEdited/editedPayload compare against these). */
const draftInitials = ref<Record<string, Record<string, string>>>({})
/** candidateId -> the payload the draft was started from (mid-edit swap detection). */
const draftBases = ref<Record<string, unknown>>({})
/** candidateId -> the candidate changed server-side mid-edit; the draft was visibly discarded. */
const staleEdits = ref<Record<string, boolean>>({})

let disconnectSocket: (() => void) | null = null

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

/** Re-sync the candidates list, then reconcile any open edit drafts. */
async function resync() {
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
// ---------------------------------------------------------------------------

const IDENTITY_FIELDS = ['level_cr', 'race_type', 'class_profession', 'alignment'] as const
const LORE_FIELDS = [
  'appearance',
  'personality',
  'background',
  'goals',
  'relationships',
  'secret',
  'rumor',
  'party_hook',
  'voice_style',
  'catchphrases',
] as const
const BOSS_FIELDS = ['lair_actions', 'legendary_actions', 'immunities', 'vulnerabilities'] as const
const WORLD_FIELDS = [
  'reputation',
  'factions',
  'current_location',
  'reaction_matrix',
  'on_defeat',
] as const

const FIELD_LABELS: Record<string, string> = {
  level_cr: 'Level / CR',
  race_type: 'Race / Type',
  class_profession: 'Class / Profession',
  alignment: 'Alignment',
  appearance: 'Appearance',
  personality: 'Personality',
  background: 'Background',
  goals: 'Goals',
  relationships: 'Relationships',
  secret: 'Secret',
  rumor: 'Rumor',
  party_hook: 'Party hook',
  voice_style: 'Voice style',
  catchphrases: 'Catchphrases',
  lair_actions: 'Lair actions',
  legendary_actions: 'Legendary actions',
  immunities: 'Immunities',
  vulnerabilities: 'Vulnerabilities',
  reputation: 'Reputation',
  factions: 'Factions',
  current_location: 'Current location',
  reaction_matrix: 'Reaction matrix',
  on_defeat: 'On defeat',
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
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
// Edit-before-accept: per-section textareas; the edited payload is what
// the accept commits (edges stay verbatim — not editable here). Every
// known section gets a textarea; sections missing from the payload
// (legacy AR19 rows) start empty so they can be filled before accept.
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
  delete staleEdits.value[id]
}

/** True when any draft value differs from the draft's starting values. */
function isEdited(candidate: Candidate): boolean {
  const draft = drafts.value[candidate.id]
  const initial = draftInitials.value[candidate.id]
  if (!draft || !initial) return false
  return Object.keys(draft).some((key) => draft[key] !== initial[key])
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
  return payload
}

async function acceptCandidate(candidate: Candidate) {
  actionError.value = null
  actingId.value = candidate.id
  try {
    await candidates.accept(
      campaignId,
      candidate.id,
      isEdited(candidate) ? editedPayload(candidate) : undefined,
    )
    cancelEdit(candidate.id)
  } catch (err) {
    actionError.value = {
      id: candidate.id,
      message: err instanceof ApiError ? err.message : 'Could not accept the candidate.',
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
              </div>
            </template>
          </dl>

          <!-- Mechanics -->
          <StatBlock
            v-if="isObject(candidate.payload['stat_block'])"
            :block="candidate.payload['stat_block']"
          />

          <!-- Conditional boss section -->
          <div v-if="editing[candidate.id] || sectionObject(candidate, 'boss')" class="subblock">
            <h4>Boss</h4>
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
            <h4>World integration</h4>
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

          <!-- Relations: staged edges, read-only (inline editing is story 3.4) -->
          <div v-if="edgesOf(candidate).length > 0" class="relations">
            <h4>Relations</h4>
            <p v-for="(edge, index) in edgesOf(candidate)" :key="index" class="mono">
              {{ relationLine(candidate, edge) }}
            </p>
          </div>
        </div>

        <p v-if="actionError && actionError.id === candidate.id" class="error">
          {{ actionError.message }}
        </p>

        <!-- Reject / Edit / Accept: one tap each, equal prominence —
             accept is never the path of least resistance (inversion #2). -->
        <div class="actions">
          <button type="button" :disabled="actingId !== null" @click="rejectCandidate(candidate)">
            Reject
          </button>
          <button type="button" :disabled="actingId !== null" @click="toggleEdit(candidate)">
            {{ editing[candidate.id] ? 'Cancel edit' : 'Edit' }}
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
</style>
