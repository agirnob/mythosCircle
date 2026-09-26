<script setup lang="ts">
// The fully-authored Add-Character screen (path 2 of the hybrid-
// authorship spec): one or more COMPLETE character sheets, every field
// authored, zero LLM — POST /api/characters (the synchronous enqueue
// gate) → 202 + job_id, or 422 with exact schema-path violations.
//
// The three-layer F3 Frontend Gate lives HERE: the mirror validator
// (lib/characterValidation) recomputes on every sheet change — the
// submit button is disabled and each sheet's violation list inline —
// and the API ALWAYS re-validates (a client bypass hits the 422 and
// surfaces field-level errors without launching a job).
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { apiFetch, ApiError } from '../api/client'
import type { CharacterSheet, EntityRef } from '../api/characters'
import AuthorSheetForm from '../components/AuthorSheetForm.vue'
import { submissionViolations } from '../lib/characterValidation'
import { useAuthStore } from '../stores/auth'
import { useJobsStore } from '../stores/jobs'
import { connectJobSocket } from '../ws'
import type { WsMessage } from '../ws'

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string

const jobs = useJobsStore()

const loadError = ref<string | null>(null)
const entities = ref<EntityRef[]>([])
const campaignTitle = ref('')

const sheetCount = ref(1)
/** Live sheet instances in a PLAIN array — writing instances from a
 * v-for :ref callback must not mutate a reactive structure the same
 * render iterates (that self-dirties the render effect into a
 * recursion loop); the v-for keys off `sheetCount` instead. */
let sheetInstances: Array<InstanceType<typeof AuthorSheetForm> | null> = [null]

const submitting = ref(false)
const submitError = ref<string | null>(null)
const startedJobId = ref<string | null>(null)
/** The 422 gate's field-level violations, verbatim from the server. */
const serverViolations = ref<string[]>([])

let disconnectSocket: (() => void) | null = null

// --- Live mirror-gate state -------------------------------------------------

/** Per-sheet violations (index-aligned), recomputed on every change. */
const violationsBySheet = ref<string[][]>([])
const payloadViolations = ref<string[]>([])

/** The violation wall only appears once a sheet has been TOUCHED or a
 * submit was attempted — a freshly mounted empty form never screams
 * (2026-09-27 owner feedback: the on-paint 24-error list read as
 * broken). */
const dirtySheets = ref<Set<number>>(new Set())
const submitAttempted = ref(false)

function onSheetChange(index: number) {
  dirtySheets.value = new Set(dirtySheets.value).add(index)
  recomputeViolations()
}

/** Whether the inline violation lists for a sheet may render. */
function showSheetViolations(index: number): boolean {
  return dirtySheets.value.has(index) || submitAttempted.value
}

const showPayloadViolations = computed(
  () => dirtySheets.value.size > 0 || submitAttempted.value,
)

/** characters[i] prefixes stripped for the inline per-sheet lists. */
function groupViolations(violations: string[]): { perSheet: string[][]; payload: string[] } {
  const perSheet: string[][] = []
  const payload: string[] = []
  for (const violation of violations) {
    const match = /^characters\[(\d+)\]\.?(.*)$/.exec(violation)
    if (match) {
      const index = Number.parseInt(match[1]!, 10)
      const rest = match[2] ?? ''
      while (perSheet.length <= index) perSheet.push([])
      perSheet[index]!.push(rest)
    } else {
      payload.push(violation)
    }
  }
  return { perSheet, payload }
}

let lastViolationFingerprint = ''
function recomputeViolations() {
  const drafts: CharacterSheet[] = []
  for (const sheet of sheetInstances) {
    if (sheet) drafts.push(sheet.buildSheet())
  }
  // Idempotence guard: reassigning the violation lists re-renders the
  // cards; an unchanged fingerprint must NOT keep re-triggering.
  const fingerprint = JSON.stringify(drafts)
  if (fingerprint === lastViolationFingerprint) return
  lastViolationFingerprint = fingerprint
  if (drafts.length === 0) {
    violationsBySheet.value = [['characters must carry a non-empty list']]
    payloadViolations.value = []
    return
  }
  const payload = { campaign_id: campaignId, characters: drafts }
  const { perSheet, payload: payloadLevel } = groupViolations(submissionViolations(payload))
  violationsBySheet.value = perSheet
  payloadViolations.value = payloadLevel
}

const anyViolations = computed(() =>
  payloadViolations.value.length > 0 || violationsBySheet.value.some((list) => list.length > 0),
)

// --- Sheet management -------------------------------------------------------

function stagedFor(index: number): Array<{ key: string; name: string }> {
  const out: Array<{ key: string; name: string }> = []
  for (const [i, sheet] of sheetInstances.entries()) {
    if (i === index || !sheet) continue
    out.push({ key: sheet.sheetKey, name: sheet.sheetName })
  }
  return out
}

/** v-for ref callback; also serves the mount-time gate recompute (a
 * freshly mounted sheet has not CHANGED yet, but the form must be
 * disabled from the first paint). The recompute is deferred off the
 * render frame: writing the violation lists is a reactive change, and
 * doing it DURING the view's render would self-dirty the render effect
 * (a recursion loop). Each mount's recompute therefore runs one tick
 * later — still long before the DM can submit. */
function setSheetRef(index: number, instance: unknown) {
  sheetInstances[index] = instance as InstanceType<typeof AuthorSheetForm> | null
  Promise.resolve().then(() => recomputeViolations())
}

function addSheet() {
  sheetInstances.push(null)
  sheetCount.value += 1
  recomputeViolations()
}

function removeSheet(index: number) {
  if (sheetCount.value <= 1) return
  sheetInstances.splice(index, 1)
  sheetCount.value -= 1
  recomputeViolations()
}

// --- Submit -----------------------------------------------------------------

async function submit() {
  if (submitting.value || anyViolations.value) return
  submitAttempted.value = true
  submitError.value = null
  serverViolations.value = []
  startedJobId.value = null
  const characters: CharacterSheet[] = []
  for (const sheet of sheetInstances) {
    if (sheet) characters.push(sheet.buildSheet())
  }
  if (characters.length === 0) {
    submitError.value = 'Add at least one character first.'
    return
  }
  submitting.value = true
  try {
    const response = await apiFetch<{ job_id: string; state: string }>('/api/characters', {
      method: 'POST',
      body: JSON.stringify({ campaign_id: campaignId, characters }),
    })
    startedJobId.value = response.job_id
    void jobs.syncList(campaignId).catch(() => {})
  } catch (err) {
    if (err instanceof ApiError && err.status === 422) {
      const details = (err.details ?? {}) as { violations?: unknown }
      serverViolations.value = Array.isArray(details.violations)
        ? details.violations.filter((value): value is string => typeof value === 'string')
        : [err.message]
    } else {
      submitError.value = err instanceof ApiError ? err.message : 'Could not submit the character.'
    }
  } finally {
    submitting.value = false
  }
}

// --- Campaign context + live job feed ---------------------------------------

async function loadContext() {
  loadError.value = null
  try {
    const response = await apiFetch<{
      campaign: { title: string }
      entities: EntityRef[]
    }>(`/api/campaigns/${encodeURIComponent(campaignId)}/export`)
    campaignTitle.value = response.campaign.title
    entities.value = response.entities.map((entity) => ({
      id: entity.id,
      name: entity.name,
      kind: entity.kind,
    }))
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load the world.'
  }
}

onMounted(() => {
  void loadContext()
  void jobs.syncList(campaignId).catch(() => {})
  disconnectSocket = connectJobSocket(
    campaignId,
    (message: WsMessage) => {
      void jobs.handleWsMessage(campaignId, message)
      // A terminal job frame means the world may have moved (the worker
      // commited the batch) — refresh the Tier-1 entity list.
      if (message.type === 'job_done' || message.type === 'job_failed') {
        void loadContext()
      }
    },
    {
      onReconnect: () => {
        void jobs.syncList(campaignId).catch(() => {})
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

const recentJobs = computed(() => jobs
  .forCampaign(campaignId)
  .filter((job) => job.kind === 'add_character')
  .slice(0, 5))
</script>

<template>
  <section>
    <h1>Add character</h1>
    <p v-if="campaignTitle" class="muted">
      Fully-authored characters for <strong>{{ campaignTitle }}</strong> — every field below
      commits verbatim, the API validates the batch against the canonical schema, and the job
      commits a fresh character with ZERO model calls. Same-name characters coexist (every
      submission is a new person).
    </p>

    <div v-if="loadError" class="card">
      <p class="error">{{ loadError }}</p>
      <RouterLink :to="{ name: 'world', params: { id: campaignId } }" class="back">
        Back to the world
      </RouterLink>
    </div>

    <template v-else-if="!loadError">
      <div v-for="index in Array.from({ length: sheetCount }, (_, i) => i)" :key="index" class="card">
        <h2>Character {{ index + 1 }}</h2>
        <AuthorSheetForm
          :key="`sheet-${index}`"
          :ref="(instance) => setSheetRef(index, instance)"
          :index="index"
          :entities="entities"
          :staged="stagedFor(index)"
          @change="onSheetChange(index)"
        />
        <ul
          v-if="showSheetViolations(index) && violationsBySheet[index] && violationsBySheet[index].length"
          class="violations"
        >
          <li v-for="violation in violationsBySheet[index]" :key="violation">{{ violation }}</li>
        </ul>
        <p v-if="!showSheetViolations(index)" class="muted small">
          Fill the required fields to enable submission — every sheet field is required
          (the same checks run server-side at POST /api/characters).
        </p>
        <button
          v-if="sheetCount > 1"
          type="button"
          class="link"
          :disabled="submitting"
          @click="removeSheet(index)"
        >
          Remove character {{ index + 1 }}
        </button>
      </div>

      <p class="actions">
        <button type="button" class="link" :disabled="submitting" @click="addSheet">
          Add another character to this batch
        </button>
      </p>

      <form class="card" @submit.prevent="submit">
        <ul v-if="showPayloadViolations && payloadViolations.length" class="violations">
          <li v-for="violation in payloadViolations" :key="violation">{{ violation }}</li>
        </ul>
        <p v-if="submitError" class="error">{{ submitError }}</p>
        <p v-if="serverViolations.length" class="error">
          The API rejected the submission:
          <ul v-if="serverViolations" class="violations">
            <li v-for="violation in serverViolations" :key="violation">{{ violation }}</li>
          </ul>
        </p>
        <p v-if="startedJobId" class="success">
          Job started ({{ startedJobId }}) — the worker validates the batch inside its
          transaction and commits fresh characters. Watch the
          <RouterLink :to="{ name: 'world', params: { id: campaignId } }">world</RouterLink>.
        </p>
        <button type="submit" :disabled="submitting || anyViolations">
          {{
            submitting
              ? 'Submitting…'
              : startedJobId
                ? 'Submit another batch'
                : 'Submit character'
          }}
        </button>
        <p class="muted small">
          <template v-if="anyViolations && (dirtySheets.size > 0 || submitAttempted)">
            Fix the {{ payloadViolations.length + violationsBySheet.flat().length }} issue(s)
            above to enable submission — the same checks run server-side at POST /api/characters.
          </template>
          <template v-else>
            This batch passes the mirror of the canonical schema. The server re-validates
            everything (attribute names, dice pattern, required keys, relation targets).
          </template>
        </p>
      </form>

      <div v-if="recentJobs.length > 0" class="card job">
        <h2>Recent submissions</h2>
        <div v-for="job in recentJobs" :key="job.id" class="job-row">
          <dl>
            <dt>State</dt>
            <dd>{{ job.state }}</dd>
            <template v-if="job.error">
              <dt>Error</dt>
              <dd class="error mono">{{ job.error }}</dd>
            </template>
          </dl>
        </div>
      </div>

      <p class="actions">
        <RouterLink :to="{ name: 'world', params: { id: campaignId } }" class="cta">
          Back to the world
        </RouterLink>
      </p>
    </template>
  </section>
</template>

<style scoped>
.card {
  border: 1px solid #2c3038;
  border-radius: 6px;
  padding: 0.75rem;
  margin: 0.75rem 0;
}
h2 {
  margin: 0.25rem 0;
}
label {
  display: grid;
  gap: 0.25rem;
}
input,
select,
textarea {
  padding: 0.4rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
}
.violations {
  margin: 0.35rem 0;
  color: #ff8c8c;
  font-size: 0.85rem;
}
.error {
  color: #ff8c8c;
}
.success {
  color: #8fdd8f;
}
.muted {
  color: #9aa0a6;
}
.muted.small {
  font-size: 0.85rem;
}
.error.mono {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.job dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 1rem;
  margin: 0;
}
.job dt {
  color: #9aa0a6;
}
.job-row + .job-row {
  border-top: 1px solid #2c3038;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
}
.link {
  background: none;
  border: none;
  color: #8ab4ff;
  cursor: pointer;
  padding: 0;
  font: inherit;
}
.link:hover {
  text-decoration: underline;
}
</style>