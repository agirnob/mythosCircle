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
const asking = ref(false)
const askError = ref<string | null>(null)
const loadError = ref<string | null>(null)
const actingId = ref<string | null>(null)
const actionError = ref<{ id: string; message: string } | null>(null)
/** Accepted candidates this session: candidateId -> new entity id. */
const accepted = ref<Record<string, string>>({})

let disconnectSocket: (() => void) | null = null

async function resync() {
  await Promise.allSettled([candidates.syncList(campaignId), jobs.syncList(campaignId)])
}

onMounted(async () => {
  try {
    await candidates.syncList(campaignId)
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load the proposals.'
    return
  }
  if (!campaigns.current || campaigns.current.id !== campaignId) {
    await campaigns.fetchOne(campaignId)
  }
  void world.load(campaignId).catch(() => {})
  void jobs.syncList(campaignId).catch(() => {})
  disconnectSocket = connectJobSocket(
    campaignId,
    (message) => {
      void jobs.handleWsMessage(campaignId, message)
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

async function submitAsk() {
  askError.value = null
  const text = ask.value.trim()
  if (!text || asking.value) return
  asking.value = true
  try {
    await candidates.submitAsk(campaignId, text)
    ask.value = ''
    await jobs.syncList(campaignId)
  } catch (err) {
    askError.value = err instanceof ApiError ? err.message : 'Could not enqueue the ask.'
  } finally {
    asking.value = false
  }
}

const proposals = computed(() => candidates.proposed(campaignId))

const generating = computed<Job[]>(() =>
  jobs
    .forCampaign(campaignId)
    .filter((job) => job.kind === 'generate' && (job.state === 'queued' || job.state === 'running')),
)

async function acceptProposal(candidate: Candidate) {
  actionError.value = null
  actingId.value = candidate.id
  try {
    const accepted_row = await candidates.accept(campaignId, candidate.id)
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
  const role = asString(candidate.payload['role'])
  return [candidate.kind, role].filter(Boolean).join(' · ')
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
        <label class="mc-ask-label" for="mc-ask-input">What does the world need?</label>
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

      <section v-if="generating.length > 0">
        <SectionHeader title="Generating" :meta="`${generating.length}`" />
        <p class="mc-muted">
          <StatusBadge variant="generating">Generating</StatusBadge>
          {{ generating.length }} {{ generating.length === 1 ? 'request' : 'requests' }} in the
          queue — proposals appear below when each wave lands.
        </p>
      </section>

      <section>
        <SectionHeader title="Staged proposals" :meta="`${proposals.length}`" />
        <EmptyState
          v-if="proposals.length === 0 && generating.length === 0"
          title="No proposals waiting"
          body="Describe what the world needs above, and the first drafts will land here for review."
        />
        <EntityGrid v-else>
          <EntityCard
            v-for="candidate in proposals"
            :key="candidate.id"
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
                <button
                  type="button"
                  class="mc-btn"
                  :disabled="actingId === candidate.id"
                  @click="acceptProposal(candidate)"
                >
                  Accept
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
            </template>
            <template #menu>
              <RouterLink
                :to="{ name: 'candidates', params: { id: campaignId } }"
                class="mc-link mc-muted"
              >
                Refine →
              </RouterLink>
            </template>
          </EntityCard>
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
.mc-card-actions {
  display: flex;
  gap: var(--mc-gap-sm);
}
</style>
