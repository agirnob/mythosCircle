<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import type { components } from '../api/schema'
import { ApiError } from '../api/client'
import { sessionGeneration } from '../api/session'
import { useJobsStore } from '../stores/jobs'

type Job = components['schemas']['JobResponse']
const props = defineProps<{ campaignId: string }>()
const emit = defineEmits<{ settled: [] }>()
const jobs = useJobsStore()
const recentJobs = computed(() => jobs.buildInJobs(props.campaignId).slice(0, 10))
const activeJobs = computed(() =>
  jobs
    .buildInJobs(props.campaignId)
    .filter((job) => job.state === 'queued' || job.state === 'running'),
)
const clock = ref(Date.now())
const cancelling = ref<string | null>(null)
const feedback = ref<string | null>(null)
const refreshError = ref<string | null>(null)
const generation = sessionGeneration()
let timer: ReturnType<typeof globalThis.setInterval> | null = null
let polling = false
let disposed = false

async function refresh() {
  const campaignId = props.campaignId
  if (polling || !campaignId || generation !== sessionGeneration()) return
  polling = true
  try {
    await jobs.syncList(campaignId)
    if (!disposed && props.campaignId === campaignId) refreshError.value = null
  } catch {
    if (!disposed && props.campaignId === campaignId) {
      refreshError.value = 'Could not refresh build status. Retrying automatically.'
    }
  } finally {
    polling = false
  }
}

watch(
  () => [props.campaignId, ...activeJobs.value.map((job) => job.id)],
  (ids, previous) => {
    if (timer !== null) globalThis.clearInterval(timer)
    timer = null
    clock.value = Date.now()
    if (ids[0] !== previous?.[0]) {
      feedback.value = null
      refreshError.value = null
    }
    if (
      generation === sessionGeneration() &&
      previous?.[0] === ids[0] &&
      previous.slice(1).some((id) => !ids.includes(id))
    ) {
      emit('settled')
    }
    if (ids.length <= 1) return
    if (ids.slice(1).some((id) => !previous?.includes(id))) feedback.value = null
    let ticks = 0
    timer = globalThis.setInterval(() => {
      clock.value = Date.now()
      ticks += 1
      if (ticks % 5 === 0) void refresh()
    }, 1000)
  },
  { immediate: true },
)

onUnmounted(() => {
  disposed = true
  if (timer !== null) globalThis.clearInterval(timer)
})

function characterNames(job: Job): string {
  const figures = job.payload?.['key_figures']
  if (!Array.isArray(figures)) return 'World build'
  const names = figures
    .map((figure: unknown) => {
      if (typeof figure === 'string') return figure.trim()
      if (
        figure &&
        typeof figure === 'object' &&
        'name' in figure &&
        typeof figure.name === 'string'
      ) {
        return figure.name.trim()
      }
      return ''
    })
    .filter(Boolean)
  return names.join(', ') || 'World build'
}

function percent(job: Job): number {
  return Number.isFinite(job.progress)
    ? Math.round(Math.min(1, Math.max(0, job.progress)) * 100)
    : 0
}

function stage(job: Job): string {
  if (job.state === 'queued') return 'Waiting to start'
  if (job.progress < 0.1) return 'Preparing your character'
  if (job.progress < 0.2) return 'Writing your character'
  if (job.progress < 0.3) return 'Checking names and connections'
  if (job.progress < 0.4) return 'Completing the character record'
  if (job.progress < 0.45) return 'Checking and repairing character stats'
  if (job.progress < 0.5) return 'Saving your character and connections'
  return 'Completing the world build'
}

function elapsed(job: Job): string {
  const started = Date.parse(job.started_at ?? job.created_at)
  const seconds = Number.isFinite(started)
    ? Math.max(0, Math.floor((clock.value - started) / 1000))
    : 0
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

async function cancel(job: Job) {
  if (cancelling.value) return
  cancelling.value = job.id
  feedback.value = null
  try {
    await jobs.cancel(job.id)
    feedback.value = 'Generation cancelled. You can start another build.'
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      try {
        await jobs.syncList(job.campaign_id)
        feedback.value = 'This generation has already finished. Its latest status is shown below.'
      } catch {
        feedback.value = 'Could not refresh the generation status. Please retry.'
      }
    } else {
      feedback.value =
        err instanceof ApiError ? err.message : 'Could not cancel generation. Please retry.'
    }
  } finally {
    cancelling.value = null
  }
}

function resultLine(job: Job): string | null {
  if (job.state !== 'succeeded' || !job.result) return null
  const merge = (job.result as { merge?: Record<string, { merged?: unknown[] }> }).merge
  const merged = merge?.wave1?.merged
  return Array.isArray(merged) && merged.length > 0
    ? `built (merged with ${merged.length} existing)`
    : 'built'
}
</script>

<template>
  <section class="build-activity" aria-label="Build activity">
    <div
      v-for="job in activeJobs"
      :key="job.id"
      class="card generation-status"
      :aria-labelledby="`generation-${job.id}`"
    >
      <div class="generation-heading">
        <div>
          <h2 :id="`generation-${job.id}`">
            {{ job.state === 'queued' ? 'Queued' : 'Generating' }}: {{ characterNames(job) }}
          </h2>
          <p class="muted" role="status">{{ stage(job) }}</p>
        </div>
        <button
          type="button"
          class="mc-btn mc-btn-secondary"
          :disabled="cancelling !== null"
          @click="cancel(job)"
        >
          {{ cancelling === job.id ? 'Cancelling…' : 'Cancel generation' }}
        </button>
      </div>
      <progress
        v-if="job.state === 'running'"
        :value="percent(job)"
        max="100"
        aria-label="Character generation progress"
      />
      <p class="muted small">
        <template v-if="job.state === 'queued'"
          >Queue position: {{ job.queue_position ?? '…' }} · Waiting {{ elapsed(job) }}</template
        >
        <template v-else>{{ percent(job) }}% · Elapsed {{ elapsed(job) }}</template>
      </p>
      <p class="muted small">
        Stages can take several minutes. Progress updates when a stage finishes.
      </p>
      <p class="muted small">
        Cancelling stops further results from being saved. A model request already in progress may
        still need to finish.
      </p>
    </div>
    <p v-if="feedback" role="status">{{ feedback }}</p>
    <p v-if="refreshError" role="alert">
      {{ refreshError }} <button type="button" @click="refresh">Refresh status</button>
    </p>
    <div class="card job">
      <h2>Recent builds</h2>
      <p v-if="recentJobs.length === 0" class="muted">No builds yet.</p>
      <div v-for="job in recentJobs" :key="job.id" class="job-row">
        <dl>
          <dt>Character</dt>
          <dd>{{ characterNames(job) }}</dd>
          <dt>State</dt>
          <dd>
            {{
              job.state === 'queued' ? `Queued (position ${job.queue_position ?? '…'})` : job.state
            }}
          </dd>
          <template v-if="resultLine(job)"
            ><dt>Result</dt>
            <dd>{{ resultLine(job) }}</dd></template
          >
          <template v-else-if="job.state === 'failed'"
            ><dt>Error</dt>
            <dd class="error">{{ job.error ?? 'The build failed.' }}</dd></template
          >
        </dl>
      </div>
    </div>
  </section>
</template>

<style scoped>
.build-activity {
  margin-block: 1rem;
}
.generation-heading {
  display: flex;
  align-items: start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
}
.generation-heading h2 {
  margin-top: 0;
  overflow-wrap: anywhere;
}
.generation-status progress {
  width: 100%;
  accent-color: #8ab4ff;
}
.job dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 1rem;
  margin: 0;
}
.job dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.job dt,
.muted {
  color: #9aa0a6;
}
.small {
  font-size: 0.85rem;
}
.error {
  color: #ff8c8c;
}
.job-row + .job-row {
  border-top: 1px solid #2c3038;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
}
</style>
