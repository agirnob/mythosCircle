<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { apiFetch, ApiError } from '../api/client'
import type { components } from '../api/schema'
import CharacterSheetEditor from '../components/CharacterSheetEditor.vue'
import BuildActivity from '../components/BuildActivity.vue'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useJobsStore } from '../stores/jobs'
import { connectJobSocket } from '../ws'
import { sessionGeneration } from '../api/session'
import type { WsMessage } from '../ws'

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
const pickerRef = ref<globalThis.HTMLElement | null>(null)
const changeDestinationRef = ref<globalThis.HTMLButtonElement | null>(null)
function closePicker() {
  const restoreFocus = pickerRef.value?.contains(globalThis.document.activeElement)
  picking.value = false
  if (restoreFocus) void nextTick(() => changeDestinationRef.value?.focus())
}
const loadingDestination = ref(true)
const destinationReady = ref(false)
const loadedCampaign = ref<components['schemas']['CampaignResponse'] | null>(null)
const destinationName = computed(() => draftMode.value ? 'Generic library' :
  loadedCampaign.value?.title ?? campaigns.campaigns.find((world) => world.id === campaignId.value)?.title ?? 'Selected world')
let destinationGeneration = 0
let disposed = false
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

/** Metadata and history must both resolve before this destination can submit.
 * Keep metadata local: the shared store's current world may change elsewhere. */
async function loadCampaign(id: string): Promise<boolean> {
  const generation = ++destinationGeneration
  disconnectSocket?.()
  disconnectSocket = null
  loadedCampaign.value = null
  destinationReady.value = false
  loadingDestination.value = true
  loadError.value = null
  const isCurrent = () => !disposed && generation === destinationGeneration &&
    socketGeneration === sessionGeneration() && pickedCampaignId.value === id
  try {
    const [campaign] = await Promise.all([
      apiFetch<components['schemas']['CampaignResponse']>(`/api/campaigns/${encodeURIComponent(id)}`),
      jobs.syncList(id),
    ])
    if (!isCurrent()) return false
    if (campaign.id !== id) throw new Error('Unexpected world response.')
    loadedCampaign.value = campaign
    draftMode.value = campaign.is_generic === true
    if (draftMode.value && !draftTheme.value) draftTheme.value = campaign.theme
    destinationReady.value = true
    disconnectSocket = connectJobSocket(
      id,
      (message: WsMessage) => {
        if (isCurrent()) void jobs.handleWsMessage(id, message)
      },
      {
        generation: socketGeneration,
        onReconnect: () => {
          if (isCurrent()) void jobs.syncList(id)
        },
        onAuthFailure: () => {
          if (!isCurrent()) return
          const auth = useAuthStore()
          auth.clearSession()
          void router.push({ name: 'login' })
        },
      },
    )
    return true
  } catch (err) {
    if (isCurrent()) {
      loadError.value = err instanceof ApiError ? err.message : 'Could not load the destination.'
    }
    return false
  } finally {
    if (isCurrent()) loadingDestination.value = false
  }
}

onMounted(async () => {
  const generation = destinationGeneration
  await Promise.all([campaigns.list(), fetchThemes()])
  if (disposed || socketGeneration !== sessionGeneration()) return
  if (!draftTheme.value && campaigns.themes.length > 0) {
    draftTheme.value = campaigns.themes[0]!
  }
  if (generation !== destinationGeneration) return
  if (pickedCampaignId.value) {
    await loadCampaign(pickedCampaignId.value)
    return
  }
  const load = pickGeneric()
  const genericGeneration = destinationGeneration
  await load
  if (!disposed && genericGeneration === destinationGeneration && socketGeneration === sessionGeneration()) {
    picking.value = true
  }
})

async function pickWorld(id: string) {
  if (submitting.value) return
  pickedCampaignId.value = id
  closePicker()
  draftMode.value = false
  error.value = null
  await loadCampaign(id)
}

async function pickGeneric() {
  if (submitting.value) return
  draftMode.value = true
  closePicker()
  error.value = null
  const generic = campaigns.campaigns.find((campaign) => campaign.is_generic)
  pickedCampaignId.value = generic?.id ?? null
  if (generic) {
    await loadCampaign(generic.id)
  } else {
    ++destinationGeneration
    disconnectSocket?.()
    disconnectSocket = null
    loadedCampaign.value = null
    loadError.value = null
    loadingDestination.value = false
    destinationReady.value = true
  }
}

async function retryDestination() {
  if (submitting.value || loadingDestination.value) return
  if (pickedCampaignId.value) await loadCampaign(pickedCampaignId.value)
  else await bindToGeneric()
}

onUnmounted(() => {
  disposed = true
  ++destinationGeneration
  disconnectSocket?.()
})

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

async function bindToGeneric(): Promise<string | null> {
  const generation = destinationGeneration
  loadingDestination.value = true
  destinationReady.value = false
  loadError.value = null
  try {
    const response = await apiFetch<components['schemas']['CampaignResponse']>(
      '/api/campaigns/generic',
      { method: 'POST', body: JSON.stringify({ theme: draftTheme.value }) },
    )
    if (disposed || generation !== destinationGeneration || socketGeneration !== sessionGeneration()) return null
    pickedCampaignId.value = response.id
    draftMode.value = true
    return (await loadCampaign(response.id)) ? response.id : null
  } catch (err) {
    if (!disposed && generation === destinationGeneration && socketGeneration === sessionGeneration()) {
      loadError.value = err instanceof ApiError ? err.message : 'Could not open the Generic library.'
      loadingDestination.value = false
    }
    return null
  }
}

async function submit() {
  if (submitting.value || !destinationReady.value || loadingDestination.value || inFlight.value ||
      disposed || socketGeneration !== sessionGeneration()) return
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
    if (isDraft.value && !campaignId.value) {
      const genericId = await bindToGeneric()
      if (!genericId) return
    }
    if (!destinationReady.value || inFlight.value || disposed || socketGeneration !== sessionGeneration()) return
    const figure = editorRef.value.buildFigure()
    if (isDraft.value) delete figure.relations
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

</script>

<template>
  <section>
    <h1>Character forge</h1>
    <p class="muted">
      Nudge a character into being. Fill what you know — those fields are yours and commit
      verbatim; leave a field blank and the selected model writes it. Nothing you write is ever
      changed. Build into a world you pick below, or generate into your Generic library without
      touching any world.
    </p>

    <div class="card destination-control">
      <p role="status">Destination: <strong>{{ destinationName }}</strong></p>
      <button ref="changeDestinationRef" type="button" :disabled="submitting" aria-controls="forge-destination-picker" :aria-expanded="picking" @click="picking = !picking">
        Change destination
      </button>
      <p v-if="loadingDestination" class="muted">Loading destination…</p>
      <div v-if="loadError" role="alert">
        <p class="error">{{ loadError }}</p>
        <button type="button" :disabled="loadingDestination || submitting" @click="retryDestination">Retry destination</button>
      </div>
      <label v-if="isDraft">
        Theme (the generation seed)
        <select v-model="draftTheme" :disabled="submitting">
          <option v-for="theme in campaigns.themes" :key="theme" :value="theme">{{ theme }}</option>
        </select>
      </label>
    </div>

    <div v-if="picking" id="forge-destination-picker" ref="pickerRef" class="card">
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
                :disabled="submitting"
                :aria-pressed="!isDraft && pickedCampaignId === campaign.id"
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
          <button type="button" :disabled="submitting" :aria-pressed="isDraft" @click="pickGeneric">
            Use Generic library
          </button>
        </div>
      </div>
    </div>

    <BuildActivity v-if="destinationReady && campaignId" :key="campaignId" :campaign-id="campaignId" />
    <form class="card" @submit.prevent="submit">
      <CharacterSheetEditor ref="editorRef" :campaign-id="isDraft ? '' : campaignId" />

      <p v-if="error" class="error">{{ error }}</p>

      <button type="submit" :disabled="submitting || loadingDestination || !destinationReady || inFlight || !editorRef?.hasContent">
        {{ inFlight ? 'Building…' : submitting ? 'Enqueuing…' : isDraft ? 'Generate into the library' : 'Build into the world' }}
      </button>
      <p class="muted small">
        <template v-if="isDraft">
          Draft mode: the build runs once against the theme's default setting and stores the
          character in your Generic library — no world is touched, and the library's own
          characters never influence the generation.
        </template>
        <template v-else>
          The build runs once: your fields commit verbatim, the blanks are written by the selected
          model, and missing relation targets are created in the same wave. Characters never
          merge — every build is a fresh, distinct person even with a duplicate name.
        </template>
      </p>
    </form>

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
