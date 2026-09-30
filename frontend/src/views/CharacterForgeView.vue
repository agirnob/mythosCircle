<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { apiFetch, ApiError } from '../api/client'
import type { components } from '../api/schema'
import CharacterSheetEditor from '../components/CharacterSheetEditor.vue'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useJobsStore } from '../stores/jobs'
import { connectJobSocket } from '../ws'
import { sessionGeneration } from '../api/session'
import type { WsMessage } from '../ws'

type Job = components['schemas']['JobResponse']

const route = useRoute()
const router = useRouter()

const campaigns = useCampaignsStore()
const jobs = useJobsStore()
const loadError = ref<string | null>(null)

/** The forge works without opening a world first: entering via /forge
 * offers the world picker AND the Generic library. A sheet is authored
 * freely; submission either builds into a picked world or generates
 * into the account's Generic library (theme-seeded, storage-isolated). */
const pickedCampaignId = ref<string | null>((route.params.id as string) || null)
const campaignId = computed(() => pickedCampaignId.value ?? '')
const picking = ref(false)
// A route-pinned forge (/campaigns/:id/forge) is a world build;
// the global /forge entry starts in draft mode.
const draftMode = ref(!pickedCampaignId.value)

const error = ref<string | null>(null)
const submitting = ref(false)
const editorRef = ref<InstanceType<typeof CharacterSheetEditor> | null>(null)

// Draft mode: no world picked — the character generates into the Generic
// library, seeded by the selected theme's default setting text.
const draftTheme = ref('')

const socketGeneration = sessionGeneration()
let disconnectSocket: (() => void) | null = null

/** The post-pick load: world meta, this world's job list + live frames,
 * and the relation picker's entity list (the editor refetches itself). */
async function loadCampaign(id: string) {
  try {
    await Promise.all([campaigns.fetchOne(id), jobs.syncList(id)])
    if (socketGeneration !== sessionGeneration()) return
    if (campaigns.error) {
      loadError.value = campaigns.error
      return
    }
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load the world.'
    return
  }
  disconnectSocket?.()
  disconnectSocket = connectJobSocket(
    id,
    (message: WsMessage) => {
      void jobs.handleWsMessage(id, message)
    },
    {
      generation: socketGeneration,
      onReconnect: () => {
        void jobs.syncList(id)
      },
      onAuthFailure: () => {
        const auth = useAuthStore()
        auth.clearSession()
        void router.push({ name: 'login' })
      },
    },
  )
}

onMounted(async () => {
  await campaigns.list()
  if (socketGeneration !== sessionGeneration()) return
  if (!draftTheme.value && campaigns.themes.length > 0) {
    draftTheme.value = campaigns.themes[0]!
  }
  if (pickedCampaignId.value) {
    await loadCampaign(pickedCampaignId.value)
    return
  }
  picking.value = true
})

async function pickWorld(id: string) {
  pickedCampaignId.value = id
  picking.value = false
  draftMode.value = false
  loadError.value = null
  await loadCampaign(id)
}

onUnmounted(() => {
  disconnectSocket?.()
})

const recentJobs = computed(() => jobs.buildInJobs(campaignId.value).slice(0, 10))
const inFlight = computed(() => jobs.buildInInFlight(campaignId.value))
const isDraft = computed(() => draftMode.value)

/** The themed fetch of the campaign list also fills the theme list
 * (GET /api/campaigns/themes) — the draft card needs it. */
async function fetchThemes() {
  try {
    await campaigns.fetchThemes()
  } catch {
    // the theme dropdown falls back to an empty list; the gate re-checks
  }
}
void fetchThemes()

async function bindToGeneric(): Promise<string | null> {
  /** Create-or-get the account's Generic library and bind this forge to
   * it, so the job feed and WS frames follow the draft build. */
  try {
    const response = await apiFetch<components['schemas']['CampaignResponse']>(
      '/api/campaigns/generic',
      { method: 'POST', body: JSON.stringify({ theme: draftTheme.value }) },
    )
    pickedCampaignId.value = response.id
    picking.value = false
    draftMode.value = true
    loadError.value = null
    await loadCampaign(response.id)
    return response.id
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not open the Generic library.'
    return null
  }
}

async function submit() {
  error.value = null
  if (!editorRef.value?.hasContent) {
    error.value = 'Give the character a name first.'
    return
  }
  if (isDraft.value && !draftTheme.value) {
    error.value = 'Pick a theme — in draft mode it seeds the generation.'
    return
  }
  submitting.value = true
  try {
    if (isDraft.value) {
      const genericId = await bindToGeneric()
      if (!genericId) return
    }
    const figure = editorRef.value.buildFigure()
    const payload = isDraft.value
      ? // the Generic gate: characters only — no world-shaping sections
        { key_figures: [figure], theme: draftTheme.value }
      : { places: [], factions: [], key_figures: [figure], notes: '' }
    await jobs.submitBuildIn(campaignId.value, payload)
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : 'Could not enqueue the character.'
  } finally {
    submitting.value = false
  }
}

function stateLabel(job: Job): string {
  return job.state === 'queued' ? `Queued (position ${job.queue_position ?? '…'})` : job.state
}

function jobError(job: Job): string | null {
  return job.state === 'failed' ? (job.error ?? 'The build failed.') : null
}

function resultLine(job: Job): string | null {
  if (job.state !== 'succeeded' || !job.result) return null
  const merge = (job.result as { merge?: Record<string, { merged?: unknown[] }> }).merge
  const wave1 = merge?.wave1
  const count = Array.isArray(wave1?.merged) ? wave1!.merged!.length : 0
  return count > 0 ? `built (merged with ${count} existing)` : 'built'
}
</script>

<template>
  <section>
    <h1>Character forge</h1>
    <p class="muted">
      Nudge a character into being. Fill what you know — those fields are yours and commit
      verbatim; leave a field blank and the local model writes it. Nothing you write is ever
      changed. Build into a world you pick below, or generate into your Generic library without
      touching any world.
    </p>

    <div v-if="loadError" class="card">
      <p class="error">{{ loadError }}</p>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>

    <!-- World picker: optional. No pick = draft mode (the Generic library). -->
    <div v-if="picking || !pickedCampaignId" class="card">
      <h2>Where should this character go?</h2>
      <div class="picker-grid">
        <div class="picker-option">
          <h3>Into a world</h3>
          <p class="muted small">
            The build sees that world's entities and lore, and can mandate new targets.
          </p>
          <p v-if="campaigns.loading" class="muted">Loading your worlds…</p>
          <p v-else-if="campaigns.campaigns.length === 0" class="muted small">
            No canon worlds yet — create one on
            <RouterLink :to="{ name: 'campaigns' }">Your worlds</RouterLink>, or just draft into
            the library.
          </p>
          <ul v-else class="world-picker">
            <li v-for="campaign in campaigns.campaigns" :key="campaign.id">
              <button
                v-if="!campaign.is_generic"
                type="button"
                class="link"
                @click="pickWorld(campaign.id)"
              >
                {{ campaign.title }}
              </button>
            </li>
          </ul>
        </div>
        <div class="picker-option">
          <h3>The Generic library</h3>
          <p class="muted small">
            No world touched: the character generates from the theme's default setting and is
            stored in your library, isolated from every world and every other stored character.
            Move it into a world later.
          </p>
          <label>
            Theme (the generation seed)
            <select v-model="draftTheme">
              <option v-for="theme in campaigns.themes" :key="theme" :value="theme">
                {{ theme }}
              </option>
            </select>
          </label>
          <p v-if="pickedCampaignId === null" class="muted small">
            Draft mode is active — submit below stores the character in the library.
          </p>
        </div>
      </div>
    </div>
    <p v-else-if="campaigns.loading && !campaigns.current" class="muted">Loading…</p>

    <template v-if="!loadError">
      <form class="card" @submit.prevent="submit">
        <CharacterSheetEditor ref="editorRef" :campaign-id="campaignId" />

        <p v-if="error" class="error">{{ error }}</p>

        <button type="submit" :disabled="submitting || inFlight || !editorRef?.hasContent">
          {{ inFlight ? 'Building…' : submitting ? 'Enqueuing…' : isDraft ? 'Generate into the library' : 'Build into the world' }}
        </button>
        <p class="muted small">
          <template v-if="isDraft">
            Draft mode: the build runs once against the theme's default setting and stores the
            character in your Generic library — no world is touched, and the library's own
            characters never influence the generation.
          </template>
          <template v-else>
            The build runs once: your fields commit verbatim, the blanks are written by the local
            model, and missing relation targets are created in the same wave. Characters never
            merge — every build is a fresh, distinct person even with a duplicate name.
          </template>
        </p>
      </form>

      <div v-if="recentJobs.length > 0" class="card job">
        <h2>Recent builds</h2>
        <div v-for="job in recentJobs" :key="job.id" class="job-row">
          <dl>
            <dt>State</dt>
            <dd>{{ stateLabel(job) }}</dd>
            <template v-if="resultLine(job)">
              <dt>Result</dt>
              <dd>{{ resultLine(job) }}</dd>
            </template>
            <template v-else-if="jobError(job)">
              <dt>Error</dt>
              <dd class="error mono">{{ jobError(job) }}</dd>
            </template>
          </dl>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.picker-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}
.picker-option {
  border: 1px solid #2c3038;
  border-radius: 6px;
  padding: 0.75rem;
  display: grid;
  gap: 0.5rem;
  align-content: start;
}
.picker-option h3 {
  margin: 0;
}
.world-picker {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 0.35rem;
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
.mono {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
}
.error {
  color: #ff8c8c;
}
.muted {
  color: #9aa0a6;
}
.muted.small {
  font-size: 0.85rem;
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
