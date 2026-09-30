<script setup lang="ts">
/**
 * Ask the world (rebuild Stage 4, §16) — plain-language generation with a
 * visible queue and staged proposals.
 *
 * Same store contracts as CandidatesView (submitAsk / proposed / accept /
 * reject + jobs WS sync); per-section re-roll and inline editing stay in
 * the existing proposals surface, linked from each card as "Refine".
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import type { components } from '../api/schema'
import EmptyState from '../components/ui/EmptyState.vue'
import EntityCard from '../components/ui/EntityCard.vue'
import EntityGrid from '../components/ui/EntityGrid.vue'
import ErrorState from '../components/ui/ErrorState.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import SectionHeader from '../components/ui/SectionHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import StatBlockEditor from '../components/StatBlockEditor.vue'
import { asString } from '../components/profile/profile'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useCandidatesStore } from '../stores/candidates'
import { useJobsStore } from '../stores/jobs'
import { useWorldStore } from '../stores/world'
import { connectJobSocket } from '../ws'

type Candidate = components['schemas']['CandidateResponse']
type Job = components['schemas']['JobResponse']

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string

const campaigns = useCampaignsStore()
const candidates = useCandidatesStore()
const jobs = useJobsStore()
const world = useWorldStore()

const ask = ref('')
const entityKind = ref<'character' | 'faction' | 'place'>('character')
const asking = ref(false)
const askError = ref<string | null>(null)
const loadError = ref<string | null>(null)
const jobsError = ref<string | null>(null)
const actingId = ref<string | null>(null)
const actionError = ref<{ id: string; message: string } | null>(null)
const reviewing = ref<Record<string, boolean>>({})
const drafts = ref<Record<string, Record<string, unknown>>>({})
/** Accepted candidates this session: candidateId -> new entity id. */
const accepted = ref<Record<string, string>>({})

let disconnectSocket: (() => void) | null = null

async function syncCandidates() {
  try {
    await candidates.syncList(campaignId)
    loadError.value = null
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load the proposals.'
  }
}

async function syncJobs() {
  try {
    await jobs.syncList(campaignId)
    jobsError.value = null
  } catch (err) {
    jobsError.value = err instanceof ApiError ? err.message : 'Could not load generation status.'
  }
}

async function resync() {
  await Promise.all([syncCandidates(), syncJobs()])
}

onMounted(async () => {
  await resync()
  if (!campaigns.current || campaigns.current.id !== campaignId) {
    await campaigns.fetchOne(campaignId)
  }
  await world.load(campaignId).catch(() => {})
  disconnectSocket = connectJobSocket(
    campaignId,
    (message) => {
      void jobs.handleWsMessage(campaignId, message).catch((err: unknown) => {
        jobsError.value = err instanceof ApiError ? err.message : 'Could not load generation status.'
      })
      if (message.type === 'job_done' || message.type === 'job_failed') {
        void resync()
      }
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

async function enqueueAsk(text: string, kind: 'character' | 'faction' | 'place', clearForm: boolean) {
  askError.value = null
  if (!text || asking.value) return
  asking.value = true
  try {
    const job = await candidates.submitAsk(campaignId, text, kind)
    jobs.upsert(job)
    if (clearForm && ask.value.trim() === text && entityKind.value === kind) ask.value = ''
    void syncJobs()
  } catch (err) {
    askError.value = err instanceof ApiError ? err.message : 'Could not enqueue the ask.'
  } finally {
    asking.value = false
  }
}

async function submitAsk() {
  await enqueueAsk(ask.value.trim(), entityKind.value, true)
}

async function retryJob(job: Job) {
  const text = asString(job.payload['ask'])?.trim()
  const rawKind = asString(job.payload['entity_kind'])
  const kind = rawKind === 'faction' || rawKind === 'place' ? rawKind : 'character'
  if (!text) {
    askError.value = 'This request has no saved ask to retry.'
    return
  }
  await enqueueAsk(text, kind, false)
}

const proposals = computed(() => candidates.proposed(campaignId))

const generating = computed<Job[]>(() =>
  jobs
    .forCampaign(campaignId)
    .filter((job) => job.kind === 'generate' && (job.state === 'queued' || job.state === 'running')),
)

const failedGenerations = computed<Job[]>(() => {
  const seenAsks = new Set<string>()
  return jobs
    .forCampaign(campaignId)
    .filter((job) => job.kind === 'generate')
    .filter((job) => {
      const key = JSON.stringify([job.payload['ask'], job.payload['entity_kind'] ?? 'character'])
      if (seenAsks.has(key)) return false
      seenAsks.add(key)
      return job.state === 'failed'
    })
    .slice(0, 3)
})

function failureMessage(job: Job): string {
  const error = job.error?.toLowerCase() ?? ''
  const kind = asString(job.payload['entity_kind']) ?? 'character'
  if (error.includes('no committed entities')) {
    return 'This world needs at least one place, faction, or character before it can generate connected proposals. Add one, then try again.'
  }
  if (error.includes('valid candidate(s) survived validation')) {
    if (kind === 'faction' && (error.includes('doctrine') || error.includes('assets'))) {
      return 'The generator left out required faction details, such as its beliefs or resources. No proposals were saved. Try again.'
    }
    if (kind === 'place' && (error.includes('inhabitants') || error.includes('whats_hidden'))) {
      return 'The generator left out required place details, such as who lives there or what it hides. No proposals were saved. Try again.'
    }
    return 'The generated proposals were missing required details, so none could be saved. Try again.'
  }
  if (error.includes('stat repair') || error.includes('stat block')) {
    return 'The generator could not finish a valid stat block. No proposals were saved. Try again.'
  }
  if (error.includes('timed out') || error.includes('timeout')) {
    return 'The generator took too long to respond. No proposals were saved. Try again in a moment.'
  }
  if (error.includes('connection error') || error.includes('provider returned http 5')) {
    return 'The text generator is unavailable right now. No proposals were saved. Try again when it is running.'
  }
  if (error.includes('provider returned http 429')) {
    return 'The text generator is busy. No proposals were saved. Try again in a moment.'
  }
  if (error.includes('max_tokens') || error.includes('output is incomplete')) {
    return 'The generator stopped before finishing its response. No proposals were saved. Try again.'
  }
  if (error.includes('not valid json') || error.includes("'candidates' list") || error.includes('returned no content')) {
    return 'The generator sent a response the app could not use. No proposals were saved. Try again.'
  }
  if (error.includes('budget')) {
    return 'The request used all its generation attempts without a usable result. No proposals were saved. Try again.'
  }
  return 'The generator could not finish this request. No proposals were saved. Try again.'
}

async function acceptProposal(candidate: Candidate) {
  actionError.value = null
  actingId.value = candidate.id
  try {
    const accepted_row = await candidates.accept(
      campaignId,
      candidate.id,
      reviewing.value[candidate.id] ? drafts.value[candidate.id] : undefined,
    )
    if (accepted_row.accepted_entity_id) {
      accepted.value[candidate.id] = accepted_row.accepted_entity_id
    }
    await resync()
  } catch (err) {
    actionError.value = {
      id: candidate.id,
      message: err instanceof ApiError ? err.message : 'Could not accept the proposal.',
    }
  } finally {
    actingId.value = null
  }
}

async function rejectProposal(candidate: Candidate) {
  actionError.value = null
  actingId.value = candidate.id
  try {
    await candidates.reject(campaignId, candidate.id)
    await resync()
  } catch (err) {
    actionError.value = {
      id: candidate.id,
      message: err instanceof ApiError ? err.message : 'Could not reject the proposal.',
    }
  } finally {
    actingId.value = null
  }
}

function candidateName(candidate: Candidate): string {
  return asString(candidate.payload['name']) ?? 'Unnamed proposal'
}

function candidateMeta(candidate: Candidate): string {
  const requestedKind = asString(candidate.payload['entity_kind'])
  const role = asString(candidate.payload['role'])
  return [requestedKind ?? candidate.kind, role].filter(Boolean).join(' · ')
}

function candidateTeaser(candidate: Candidate): string | undefined {
  const payload = candidate.payload as Record<string, unknown>
  return (
    asString(payload['personality']) ??
    asString(payload['party_hook']) ??
    asString(payload['description']) ??
    undefined
  )
}

function candidateEdges(candidate: Candidate): string {
  const edges = candidate.payload['edges']
  const count = Array.isArray(edges) ? edges.length : 0
  return `${count} staged ${count === 1 ? 'relation' : 'relations'}`
}

function candidateRelations(candidate: Candidate): Array<{
  type: string
  direction: 'outbound' | 'inbound'
  target: string
  counter: string
  reason: string
}> {
  const edges = candidate.payload['edges']
  if (!Array.isArray(edges)) return []
  const entities = world.entry(campaignId).world?.entities ?? []
  const related = candidateRelatedEntities(candidate)
  return edges
    .filter((edge): edge is Record<string, unknown> => typeof edge === 'object' && edge !== null)
    .map((edge) => {
      const endpoint = asString(edge['endpoint']) ?? 'Unknown target'
      const target =
        entities.find((entity) => entity.id === endpoint)?.name ??
        related.find((entity) => entity.ref === endpoint)?.name ??
        (endpoint.startsWith('N') ? 'New related entity' : 'Existing world entity')
      return {
        type: asString(edge['type']) ?? 'relationship',
        direction: edge['direction'] === 'inbound' ? 'inbound' : 'outbound',
        target,
        counter: typeof edge['counter'] === 'number' ? String(edge['counter']) : '1',
        reason: asString(edge['reason']) ?? 'No reason supplied.',
      }
    })
}

function candidateRelatedEntities(candidate: Candidate): Array<{
  ref: string
  kind: string
  name: string
  description: string
}> {
  const value = candidate.payload['related_entities']
  if (!Array.isArray(value)) return []
  return value.filter(
    (entity): entity is { ref: string; kind: string; name: string; description: string } =>
      typeof entity === 'object' &&
      entity !== null &&
      typeof entity.ref === 'string' &&
      typeof entity.kind === 'string' &&
      typeof entity.name === 'string' &&
      typeof entity.description === 'string',
  )
}

function candidateKind(candidate: Candidate): 'character' | 'faction' | 'place' {
  const kind = asString(candidate.payload['entity_kind'])
  return kind === 'faction' || kind === 'place' ? kind : 'character'
}

function reviewFields(candidate: Candidate): Array<{ key: string; label: string }> {
  if (candidateKind(candidate) === 'place') {
    return [
      { key: 'name', label: 'Name' },
      { key: 'description', label: 'Description' },
      { key: 'inhabitants', label: 'Inhabitants' },
      { key: 'whats_hidden', label: "What's hidden" },
    ]
  }
  if (candidateKind(candidate) === 'faction') {
    return [
      { key: 'name', label: 'Name' },
      { key: 'description', label: 'Description' },
      { key: 'doctrine', label: 'Doctrine' },
      { key: 'assets', label: 'Assets' },
    ]
  }
  return [
    { key: 'name', label: 'Name' },
    { key: 'appearance', label: 'Appearance' },
    { key: 'personality', label: 'Personality' },
    { key: 'background', label: 'Background' },
    { key: 'goals', label: 'Goals' },
    { key: 'relationships', label: 'Relationships' },
    { key: 'secret', label: 'Secret' },
    { key: 'rumor', label: 'Rumor' },
    { key: 'party_hook', label: 'Party hook' },
    { key: 'voice_style', label: 'Voice style' },
    { key: 'catchphrases', label: 'Catchphrases' },
  ]
}

function reviewProposal(candidate: Candidate) {
  drafts.value[candidate.id] = JSON.parse(JSON.stringify(candidate.payload)) as Record<string, unknown>
  reviewing.value[candidate.id] = true
  actionError.value = null
}

function closeReview(candidate: Candidate) {
  reviewing.value[candidate.id] = false
  delete drafts.value[candidate.id]
  if (actionError.value?.id === candidate.id) actionError.value = null
}

function draftText(candidate: Candidate, key: string): string {
  return asString(drafts.value[candidate.id]?.[key]) ?? asString(candidate.payload[key]) ?? ''
}

function updateDraft(candidate: Candidate, key: string, event: { target: unknown }) {
  const target = event.target as { value: string }
  if (!drafts.value[candidate.id]) reviewProposal(candidate)
  drafts.value[candidate.id][key] = target.value
}

function draftStatBlock(candidate: Candidate): Record<string, unknown> | null {
  const value = drafts.value[candidate.id]?.['stat_block']
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

function updateStatBlock(candidate: Candidate, value: Record<string, unknown> | null) {
  if (!drafts.value[candidate.id]) reviewProposal(candidate)
  drafts.value[candidate.id]['stat_block'] = value
}
</script>

<template>
  <div>
    <PageHeader
      title="Ask the world"
      :description="
        campaigns.current
          ? `Describe what ${campaigns.current.title} needs — the model drafts candidates, you accept what fits.`
          : 'Describe what the world needs — the model drafts candidates, you accept what fits.'
      "
    />

    <ErrorState
      v-if="loadError"
      title="Could not load the proposals."
      :message="loadError"
    >
      <template #actions>
        <button type="button" class="mc-btn mc-btn-secondary" @click="() => resync()">
          Try again
        </button>
      </template>
    </ErrorState>
    <template v-else>
      <section class="mc-ask-card">
        <div class="mc-ask-heading">
          <label class="mc-ask-label" for="mc-ask-input">What does the world need?</label>
          <label class="mc-kind-label" for="mc-entity-kind">Generate as</label>
          <select id="mc-entity-kind" v-model="entityKind" class="mc-select" :disabled="asking">
            <option value="character">Character</option>
            <option value="faction">Faction</option>
            <option value="place">Place</option>
          </select>
        </div>
        <textarea
          id="mc-ask-input"
          v-model="ask"
          class="mc-textarea"
          rows="3"
          placeholder="A retired pirate who runs the bathhouse and knows where the bodies are buried…"
          :disabled="asking"
          @keydown.ctrl.enter="submitAsk"
        />
        <div class="mc-ask-footer">
          <button type="button" class="mc-btn" :disabled="asking || !ask.trim()" @click="submitAsk">
            {{ asking ? 'Enqueuing…' : generating.length > 0 ? 'Ask again' : 'Generate' }}
          </button>
          <span class="mc-muted">Ctrl+Enter to send. The world is unchanged until you accept.</span>
        </div>
        <p v-if="askError" class="mc-error-text">{{ askError }}</p>
      </section>

      <div v-if="jobsError" class="mc-job-sync-error" role="alert">
        <span>{{ jobsError }}</span>
        <button type="button" class="mc-btn mc-btn-secondary" @click="syncJobs">Retry status</button>
      </div>

      <section v-if="generating.length > 0">
        <SectionHeader title="Generating" :meta="`${generating.length}`" />
        <p class="mc-muted">
          <StatusBadge variant="generating">Generating</StatusBadge>
          {{ generating.length }} {{ generating.length === 1 ? 'request' : 'requests' }} in the
          queue — proposals appear below when each wave lands.
        </p>
      </section>

      <section v-if="failedGenerations.length > 0">
        <SectionHeader title="Recent failed asks" :meta="`${failedGenerations.length}`" />
        <div class="mc-failed-list">
          <article v-for="job in failedGenerations" :key="job.id" class="mc-failed-request">
            <div class="mc-failed-request-heading">
              <StatusBadge variant="failed">Failed</StatusBadge>
              <strong>{{ asString(job.payload['entity_kind']) ?? 'character' }} · {{ asString(job.payload['ask']) ?? 'Generation request' }}</strong>
            </div>
            <p class="mc-failed-explanation" role="alert">{{ failureMessage(job) }}</p>
            <button
              type="button"
              class="mc-btn mc-btn-secondary"
              :disabled="asking"
              @click="retryJob(job)"
            >
              Retry this ask
            </button>
            <details v-if="job.error" class="mc-failed-details">
              <summary>Technical details</summary>
              <pre>{{ job.error }}</pre>
            </details>
          </article>
        </div>
      </section>

      <section>
        <SectionHeader title="Staged proposals" :meta="`${proposals.length}`" />
        <EmptyState
          v-if="proposals.length === 0 && generating.length === 0"
          title="No proposals waiting"
          :body="failedGenerations.length > 0 ? 'The latest request failed. Its error and retry action appear above.' : 'Describe what the world needs above, and the first drafts will land here for review.'"
        />
        <EntityGrid v-else>
          <template v-for="candidate in proposals" :key="candidate.id">
            <EntityCard
              :name="candidateName(candidate)"
              :meta="candidateMeta(candidate)"
              :description="candidateTeaser(candidate)"
              :relation-info="candidateEdges(candidate)"
            >
            <template #primary>
              <span v-if="accepted[candidate.id]">
                <RouterLink
                  :to="{
                    name: 'entity',
                    params: { id: campaignId, entityId: accepted[candidate.id] },
                  }"
                  class="mc-link"
                >
                  Open in world →
                </RouterLink>
              </span>
              <span v-else class="mc-card-actions">
                <span class="mc-card-action-row">
                  <button
                    type="button"
                    class="mc-btn"
                    :disabled="actingId === candidate.id"
                    @click="acceptProposal(candidate)"
                  >
                    {{ reviewing[candidate.id] ? 'Accept edited' : 'Accept' }}
                  </button>
                  <button
                    type="button"
                    class="mc-btn mc-btn-secondary"
                    :disabled="actingId === candidate.id"
                    @click="rejectProposal(candidate)"
                  >
                    Reject
                  </button>
                </span>
                <button
                  type="button"
                  class="mc-btn mc-btn-secondary mc-review-action"
                  :disabled="actingId === candidate.id"
                  @click="reviewing[candidate.id] ? closeReview(candidate) : reviewProposal(candidate)"
                >
                  {{ reviewing[candidate.id] ? 'Close review' : 'Review / edit' }}
                </button>
              </span>
            </template>
            </EntityCard>
            <section v-if="reviewing[candidate.id]" class="mc-review-panel">
              <div class="mc-review-title-row">
                <div>
                  <p class="mc-review-kicker">Reviewing proposal</p>
                  <h3>{{ candidateName(candidate) }}</h3>
                </div>
                <span class="mc-review-kind">{{ candidateKind(candidate) }}</span>
              </div>
              <div class="mc-review-fields">
                <div v-for="field in reviewFields(candidate)" :key="field.key" class="mc-review-field">
                  <label :for="`review-${candidate.id}-${field.key}`">{{ field.label }}</label>
                  <textarea
                    v-if="field.key !== 'name'"
                    :id="`review-${candidate.id}-${field.key}`"
                    :value="draftText(candidate, field.key)"
                    rows="3"
                    @input="updateDraft(candidate, field.key, $event)"
                  />
                  <input
                    v-else
                    :id="`review-${candidate.id}-${field.key}`"
                    :value="draftText(candidate, field.key)"
                    @input="updateDraft(candidate, field.key, $event)"
                  />
                </div>
              </div>
              <div v-if="candidateKind(candidate) === 'character'" class="mc-review-stat-block">
                <p class="mc-review-heading">Stat block</p>
                <StatBlockEditor
                  :model-value="draftStatBlock(candidate)"
                  @update:model-value="updateStatBlock(candidate, $event)"
                />
              </div>
              <div class="mc-review-relations">
                <p class="mc-review-heading">Relations</p>
                <p v-if="candidateRelations(candidate).length === 0" class="mc-muted">
                  No staged relations.
                </p>
                <div
                  v-for="(relation, index) in candidateRelations(candidate)"
                  :key="`${candidate.id}-relation-${index}`"
                  class="mc-review-relation"
                >
                  <div class="mc-review-relation-line">
                    <strong>{{ relation.type }}</strong>
                    <span
                      class="mc-review-relation-arrow"
                      :title="relation.direction === 'inbound' ? 'Target points to this proposal' : 'Proposal points to target'"
                      :aria-label="relation.direction === 'inbound' ? 'points from target to proposal' : 'points from proposal to target'"
                    >
                      {{ relation.direction === 'inbound' ? '←' : '→' }}
                    </span>
                    <span>{{ relation.target }}</span>
                  </div>
                  <span class="mc-muted">Counter {{ relation.counter }}</span>
                  <p>{{ relation.reason }}</p>
                </div>
              </div>
              <div v-if="candidateRelatedEntities(candidate).length > 0" class="mc-review-related">
                <p class="mc-review-heading">New related entities in this proposal</p>
                <div
                  v-for="entity in candidateRelatedEntities(candidate)"
                  :key="`${candidate.id}-${entity.ref}`"
                  class="mc-review-related-card"
                >
                  <div>
                    <strong>{{ entity.name }}</strong>
                    <span class="mc-review-relation-direction">{{ entity.kind }}</span>
                  </div>
                  <p>{{ entity.description }}</p>
                </div>
                <p class="mc-muted">Accepting this proposal commits these entities and their relations together.</p>
              </div>
              <details class="mc-review-technical">
                <summary>Show generated technical record</summary>
                <pre>{{ JSON.stringify(drafts[candidate.id], null, 2) }}</pre>
              </details>
            </section>
          </template>
        </EntityGrid>
        <p v-if="actionError" class="mc-error-text">{{ actionError.message }}</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.mc-ask-card {
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  padding: 1.1rem 1.25rem;
  margin-bottom: 0.5rem;
}
.mc-ask-label {
  display: block;
  font-weight: 600;
  margin-bottom: 0.5rem;
}
.mc-ask-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.5rem;
}
.mc-ask-heading .mc-ask-label {
  margin-bottom: 0;
}
.mc-kind-label {
  color: var(--mc-text-muted);
  font-size: 0.78rem;
  margin-left: auto;
}
.mc-select {
  min-width: 8.5rem;
  padding: 0.45rem 0.65rem;
  color: var(--mc-text-primary);
  background: var(--mc-surface-raised);
  border: 1px solid var(--mc-border);
  border-radius: 0.45rem;
}
.mc-ask-footer {
  display: flex;
  align-items: center;
  gap: var(--mc-gap);
  margin-top: 0.75rem;
}
.mc-error-text {
  color: var(--mc-danger);
  margin: 0.75rem 0 0;
}
.mc-job-sync-error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-top: 0.75rem;
  color: var(--mc-danger);
}
.mc-failed-list {
  display: grid;
  gap: 0.75rem;
}
.mc-failed-request {
  min-width: 0;
  padding: 0.9rem 1rem;
  border: 1px solid var(--mc-danger);
  border-radius: var(--mc-radius);
  background: var(--mc-surface);
}
.mc-failed-request-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  overflow-wrap: anywhere;
}
.mc-failed-explanation {
  margin: 0.8rem 0;
  color: var(--mc-text-primary);
}
.mc-failed-details {
  margin-top: 0.75rem;
  color: var(--mc-text-muted);
  font-size: 0.82rem;
}
.mc-failed-details summary {
  cursor: pointer;
}
.mc-failed-details pre {
  max-height: 14rem;
  overflow: auto;
  padding: 0.7rem;
  border: 1px solid var(--mc-border);
  border-radius: 0.4rem;
  background: var(--mc-surface-raised);
  color: var(--mc-text-secondary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin-bottom: 0.8rem;
}
.mc-card-actions {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--mc-gap-sm);
}
.mc-card-action-row {
  display: flex;
  gap: var(--mc-gap-sm);
}
.mc-review-action {
  width: 100%;
}
.mc-review-stat-block {
  margin-top: 1rem;
  padding-top: 0.9rem;
  border-top: 1px solid var(--mc-border);
}
.mc-review-relations {
  margin-top: 1rem;
  padding-top: 0.9rem;
  border-top: 1px solid var(--mc-border);
}
.mc-review-relation {
  padding: 0.65rem 0;
  border-bottom: 1px solid var(--mc-border);
}
.mc-review-relation:last-child {
  border-bottom: 0;
}
.mc-review-relation-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.mc-review-relation-arrow {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.35rem;
  color: var(--mc-interactive-bright);
  font-size: 1.4rem;
  font-weight: 700;
  line-height: 1;
}
.mc-review-relation-direction {
  margin-left: 0.45rem;
  color: var(--mc-interactive-bright);
  font-size: 0.78rem;
  text-transform: uppercase;
}
.mc-review-relation p {
  margin: 0.25rem 0 0;
  color: var(--mc-text-secondary);
}
.mc-review-related {
  margin-top: 1rem;
  padding-top: 0.9rem;
  border-top: 1px solid var(--mc-border);
}
.mc-review-related-card {
  padding: 0.65rem 0;
  border-bottom: 1px solid var(--mc-border);
}
.mc-review-related-card:last-of-type {
  border-bottom: 0;
}
.mc-review-related-card p {
  margin: 0.25rem 0 0;
  color: var(--mc-text-secondary);
}
.mc-review-panel {
  grid-column: 1 / -1;
  margin-top: 0;
  padding: 0.85rem;
  border: 1px solid var(--mc-border);
  border-radius: 0.55rem;
  background: rgba(8, 14, 24, 0.55);
}
.mc-review-title-row {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.8rem;
}
.mc-review-title-row h3 {
  margin: 0;
  font-size: 1.1rem;
}
.mc-review-kicker,
.mc-review-kind {
  margin: 0 0 0.25rem;
  color: var(--mc-text-muted);
  font-size: 0.75rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.mc-review-kind {
  margin: 0;
  color: var(--mc-interactive-bright);
}
.mc-review-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}
.mc-review-heading {
  margin: 0 0 0.7rem;
  color: var(--mc-text-primary);
  font-weight: 650;
}
.mc-review-field {
  display: grid;
  gap: 0.3rem;
  margin-top: 0.65rem;
}
.mc-review-field label {
  color: var(--mc-text-muted);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.mc-review-field input,
.mc-review-field textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 0.55rem 0.65rem;
  color: var(--mc-text-primary);
  background: var(--mc-surface-raised);
  border: 1px solid var(--mc-border);
  border-radius: 0.4rem;
  font: inherit;
  resize: vertical;
}
.mc-review-technical {
  margin-top: 0.8rem;
  color: var(--mc-text-muted);
  font-size: 0.8rem;
}
.mc-review-technical pre {
  max-height: 18rem;
  overflow: auto;
  padding: 0.7rem;
  white-space: pre-wrap;
  color: var(--mc-text-secondary);
  background: rgba(0, 0, 0, 0.22);
}
@media (max-width: 600px) {
  .mc-ask-heading {
    align-items: stretch;
    flex-direction: column;
    gap: 0.35rem;
  }
  .mc-kind-label {
    margin-left: 0;
  }
  .mc-review-fields {
    grid-template-columns: 1fr;
  }
}
</style>
