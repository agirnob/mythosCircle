<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import type { components } from '../api/schema'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useJobsStore } from '../stores/jobs'
import { connectJobSocket } from '../ws'

type Job = components['schemas']['JobResponse']

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string

const campaigns = useCampaignsStore()
const jobs = useJobsStore()
const loadError = ref<string | null>(null)

const sections: Array<{ key: SectionKey; label: string; hint: string }> = [
  { key: 'places', label: 'Key places', hint: 'One per line — towns, taverns, ruins…' },
  { key: 'factions', label: 'Factions', hint: 'One per line — guilds, courts, cults…' },
  { key: 'key_figures', label: 'Key figures', hint: 'One per line — bar keep, mayor, rival…' },
]
type SectionKey = 'places' | 'factions' | 'key_figures'

const sectionText = ref<Record<SectionKey, string>>({ places: '', factions: '', key_figures: '' })
const notes = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)

let disconnectSocket: (() => void) | null = null

onMounted(async () => {
  try {
    await Promise.all([campaigns.fetchOne(campaignId), jobs.syncList(campaignId)])
    if (campaigns.error) {
      loadError.value = campaigns.error
      return
    }
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load the world.'
    return
  }
  disconnectSocket = connectJobSocket(
    campaignId,
    (message) => {
      jobs.handleWsMessage(campaignId, message)
    },
    {
      onReconnect: () => {
        void jobs.syncList(campaignId)
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

const recentJobs = computed(() => jobs.buildInJobs(campaignId).slice(0, 10))
const hasContent = computed(() => {
  const anySection = sections.some(
    (section) => splitEntries(sectionText.value[section.key]).length > 0,
  )
  return anySection || notes.value.trim().length > 0
})

function splitEntries(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

async function submit() {
  error.value = null
  const payload = {
    places: splitEntries(sectionText.value.places),
    factions: splitEntries(sectionText.value.factions),
    key_figures: splitEntries(sectionText.value.key_figures),
    notes: notes.value.trim(),
  }
  submitting.value = true
  try {
    await jobs.submitBuildIn(campaignId, payload)
    sectionText.value = { places: '', factions: '', key_figures: '' }
    notes.value = ''
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not enqueue the build-in.'
  } finally {
    submitting.value = false
  }
}

function stateLabel(job: Job): string {
  return job.state === 'queued' ? `Queued (position ${job.queue_position ?? '…'})` : job.state
}
</script>

<template>
  <section>
    <h1>Guided build-in</h1>

    <div v-if="loadError" class="card">
      <p class="error">{{ loadError }}</p>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>
    <p v-else-if="campaigns.loading || !campaigns.current" class="muted">Loading world seed…</p>
    <div v-else class="card seed">
      <h2>{{ campaigns.current.title }}</h2>
      <p class="muted">{{ campaigns.current.description || 'No description.' }}</p>
      <p>
        <RouterLink :to="{ name: 'world', params: { id: campaignId } }" class="cta secondary"
          >Open world view</RouterLink
        >
      </p>
      <p class="muted">
        Theme: <strong>{{ campaigns.current.theme }}</strong>
      </p>
      <p v-if="campaigns.current.custom_lore" class="lore">
        {{ campaigns.current.custom_lore }}
      </p>
      <p class="muted small">
        The world seed is read from your campaign — it flows into generation.
      </p>
    </div>

    <form class="card" @submit.prevent="submit">
      <label v-for="section in sections" :key="section.key">
        <span>{{ section.label }}</span>
        <textarea v-model="sectionText[section.key]" :placeholder="section.hint" rows="3" />
      </label>
      <label>
        <span>Free-form notes</span>
        <textarea
          v-model="notes"
          maxlength="2000"
          placeholder="Custom lore, rumors, history, anything in your head — one note per line is fine."
          rows="5"
        />
        <span class="muted small counter">{{ notes.length }}/2000</span>
      </label>
      <p v-if="error" class="error">{{ error }}</p>
      <button type="submit" :disabled="submitting || !hasContent">
        {{ submitting ? 'Enqueuing…' : 'Build my world' }}
      </button>
    </form>

    <div v-if="recentJobs.length > 0" class="card job">
      <h2>Build-in jobs</h2>
      <article v-for="job in recentJobs" :key="job.id" class="job-row">
        <dl>
          <dt>Job id</dt>
          <dd class="mono">{{ job.id }}</dd>
          <dt>State</dt>
          <dd>{{ stateLabel(job) }}</dd>
          <dt v-if="job.queue_position !== null">Queue position</dt>
          <dd v-if="job.queue_position !== null">{{ job.queue_position }}</dd>
          <dt v-if="job.state === 'running'">Progress</dt>
          <dd v-if="job.state === 'running'">{{ Math.round(job.progress * 100) }}%</dd>
          <dt v-if="job.error">Error</dt>
          <dd v-if="job.error" class="error">{{ job.error }}</dd>
        </dl>
      </article>
      <p class="muted small">
        The screen is not blocked — the job runs in the background and updates live.
      </p>
    </div>
  </section>
</template>

<style scoped>
.seed h2 {
  margin-top: 0;
}
form {
  display: grid;
  gap: 1rem;
}
label {
  display: grid;
  gap: 0.25rem;
}
textarea {
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
  resize: vertical;
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
.counter {
  text-align: right;
}
.job-row + .job-row {
  border-top: 1px solid #2c3038;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
}
</style>
