<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import type { components } from '../../api/schema'
import { ApiError, apiFetch } from '../../api/client'
import { BOSS_ROLES } from '../profile/profile'
import { hasNonBlankAppearance } from '../../lib/appearance'
import {
  PORTRAIT_BACKGROUNDS,
  PORTRAIT_BACKGROUND_LABELS,
  PORTRAIT_FRAMINGS,
  PORTRAIT_FRAMING_LABELS,
  PORTRAIT_STYLES,
  PORTRAIT_STYLE_LABELS,
  type PortraitBackground,
  type PortraitFraming,
  type PortraitStyle,
} from '../../lib/portrait'
import { useJobsStore } from '../../stores/jobs'
import { useWorldStore } from '../../stores/world'
import { connectJobSocket } from '../../ws'
import { sessionGeneration } from '../../api/session'

type EntityExport = components['schemas']['EntityExport']

const props = defineProps<{ campaignId: string; entity: EntityExport }>()
const world = useWorldStore()
const jobs = useJobsStore()
const error = ref('')
const portraitDraft = ref({
  style: 'illustration' as PortraitStyle,
  framing: 'headshot' as PortraitFraming,
  background: 'scene' as PortraitBackground,
  customStyle: '',
})
const editedRevealPrompt = ref<string | null>(null)
const automaticRevealPrompt = ref<string | null>(null)
const automaticPromptLoading = ref(false)
const automaticPromptError = ref('')
const portraitLink = ref('')
const portraitLinkBusy = ref(false)
const socketGeneration = sessionGeneration()
let disconnect: (() => void) | null = null
let automaticPromptRequest = 0

const entityId = computed(() => props.entity.id)
const revealDraftKey = computed(() => `mythoscircle:reveal-prompt:${props.campaignId}:${entityId.value}`)
const data = computed(() => props.entity.data as Record<string, unknown>)
const appearanceReady = computed(() => hasNonBlankAppearance(data.value['appearance']))
const isBoss = computed(() => {
  const role = data.value['role']
  return typeof role === 'string' && BOSS_ROLES.has(role)
})
const portrait = computed(() => world.portraitFor(props.campaignId, entityId.value))
const video = computed(() => world.videoFor(props.campaignId, entityId.value))
const videoPromptUsed = computed(() => {
  if (!video.value) return null
  const job = jobs.forCampaign(props.campaignId).find((candidate) =>
    candidate.kind === 'video' &&
    candidate.state === 'succeeded' &&
    candidate.result?.['filename'] === video.value?.filename &&
    candidate.payload?.['entity_id'] === entityId.value,
  )
  const prompt = job?.result?.['prompt'] ?? job?.payload?.['prompt']
  return typeof prompt === 'string' && prompt.trim() ? prompt : null
})
const portraitJob = computed(() => latestJob('image'))
const videoJob = computed(() => latestJob('video'))
const revealPrompt = computed({
  get: () => editedRevealPrompt.value ?? automaticRevealPrompt.value ?? '',
  set: (value: string) => {
    editedRevealPrompt.value = value
    try {
      globalThis.localStorage.setItem(revealDraftKey.value, value)
    } catch {
      // Editing still works when browser storage is unavailable.
    }
  },
})
const canUseAutomaticPrompt = computed(() =>
  automaticRevealPrompt.value !== null && revealPrompt.value !== automaticRevealPrompt.value,
)

watch([() => props.campaignId, entityId], restoreRevealDraft)
watch(
  [() => props.campaignId, entityId, () => JSON.stringify(data.value['appearance']), isBoss],
  () => { void loadAutomaticPrompt() },
)

function latestJob(kind: string) {
  return (
    jobs
      .forCampaign(props.campaignId)
      .filter((job) => {
        if (job.kind !== kind) return false
        const payload = job.payload as { entity_id?: string } | null
        return payload?.entity_id === entityId.value
      })
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
  )
}

function mediaUrl(row: { entity_id: string; filename: string }): string {
  return `/api/campaigns/${encodeURIComponent(props.campaignId)}/media/${encodeURIComponent(row.entity_id)}/${row.filename}`
}

function entityExportUrl(format: 'markdown' | 'html' | 'owlbear' | 'fg' | 'maptool'): string {
  return `/api/campaigns/${encodeURIComponent(props.campaignId)}/entities/${encodeURIComponent(entityId.value)}/export?format=${format}`
}

function status(job: ReturnType<typeof latestJob>, label: string): string | null {
  if (!job) return null
  if (job.state === 'queued') return `${label} queued — position ${job.queue_position ?? '…'}`
  if (job.state === 'running') return `${label} generating…`
  if (job.state === 'failed') {
    if (label === 'Portrait' && /provider connection error|connection refused|all connection attempts failed|connecterror/i.test(job.error ?? '')) {
      return 'Portrait generator is offline. Start the image service and try again.'
    }
    return `${label} failed: ${job.error ?? 'unknown error'}`
  }
  return null
}

async function generatePortrait() {
  if (!appearanceReady.value || jobs.portraitInFlight(props.campaignId, entityId.value)) return
  error.value = ''
  try {
    await jobs.submitPortrait(props.campaignId, entityId.value, {
      style: portraitDraft.value.style,
      framing: portraitDraft.value.framing,
      background: portraitDraft.value.background,
      customStyle: portraitDraft.value.style === 'custom' ? portraitDraft.value.customStyle : undefined,
    })
    await jobs.syncList(props.campaignId)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not generate the portrait.'
  }
}

async function getPortraitLink() {
  portraitLinkBusy.value = true
  error.value = ''
  try {
    const response = await apiFetch<{ url: string }>(
      `/api/campaigns/${encodeURIComponent(props.campaignId)}/entities/${encodeURIComponent(entityId.value)}/portrait-url`,
    )
    portraitLink.value = response.url
    try {
      await globalThis.navigator.clipboard.writeText(response.url)
    } catch {
      // The link remains visible for manual copying.
    }
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not get the portrait link.'
  } finally {
    portraitLinkBusy.value = false
  }
}

async function deletePortrait() {
  if (!portrait.value) return
  error.value = ''
  try {
    await world.deleteMedia(props.campaignId, entityId.value, portrait.value.id)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not delete the portrait.'
  }
}

function restoreRevealDraft() {
  try {
    editedRevealPrompt.value = globalThis.localStorage.getItem(revealDraftKey.value)
  } catch {
    editedRevealPrompt.value = null
  }
}

function useAutomaticPrompt() {
  if (automaticRevealPrompt.value === null) return
  editedRevealPrompt.value = null
  try {
    globalThis.localStorage.removeItem(revealDraftKey.value)
  } catch {
    // The appearance prompt is restored for this session even without storage.
  }
}

async function loadAutomaticPrompt() {
  if (socketGeneration !== sessionGeneration()) return
  const request = ++automaticPromptRequest
  automaticRevealPrompt.value = null
  automaticPromptError.value = ''
  automaticPromptLoading.value = false
  if (!isBoss.value || !appearanceReady.value) return
  automaticPromptLoading.value = true
  try {
    const response = await apiFetch<components['schemas']['RevealPromptResponse']>(
      `/api/campaigns/${encodeURIComponent(props.campaignId)}/entities/${encodeURIComponent(entityId.value)}/reveal-prompt`,
    )
    if (request !== automaticPromptRequest || socketGeneration !== sessionGeneration()) return
    if (!response.prompt?.trim()) throw new Error('Empty automatic prompt')
    automaticRevealPrompt.value = response.prompt
  } catch {
    if (request === automaticPromptRequest) automaticPromptError.value = 'Could not load the automatic prompt.'
  } finally {
    if (request === automaticPromptRequest) automaticPromptLoading.value = false
  }
}

async function renderRevealVideo() {
  if (!isBoss.value || !appearanceReady.value || jobs.videoInFlight(props.campaignId, entityId.value)) return
  const prompt = revealPrompt.value
  error.value = ''
  if (!prompt.trim()) {
    error.value = 'Enter a video prompt or load the automatic prompt before rendering.'
    return
  }
  try {
    await jobs.submitRevealVideo(props.campaignId, entityId.value, prompt)
    await jobs.syncList(props.campaignId)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not generate the reveal video.'
  }
}

onMounted(() => {
  if (socketGeneration !== sessionGeneration()) return
  restoreRevealDraft()
  void world.fetchMedia(props.campaignId)
  void jobs.syncList(props.campaignId).catch(() => {})
  void loadAutomaticPrompt()
  disconnect = connectJobSocket(props.campaignId, (message) => {
    void world.handleJobMessage(props.campaignId, message)
  }, { generation: socketGeneration })
})

onUnmounted(() => {
  automaticPromptRequest += 1
  disconnect?.()
})
</script>

<template>
  <section class="mc-media-actions" aria-labelledby="media-actions-title">
    <h2 id="media-actions-title">Media and exports</h2>
    <div class="mc-export-links">
      <a v-for="format in ['markdown', 'html', 'owlbear', 'fg', 'maptool']" :key="format" :href="entityExportUrl(format as 'markdown' | 'html' | 'owlbear' | 'fg' | 'maptool')" download>
        {{ format === 'html' ? 'Sheet (HTML)' : format === 'owlbear' ? 'Owlbear (Forge)' : format === 'fg' ? 'Fantasy Grounds' : format === 'maptool' ? 'MapTool' : 'Markdown' }}
      </a>
    </div>

    <div class="mc-media-block">
      <h3>Portrait</h3>
      <img v-if="portrait" :src="mediaUrl(portrait)" :alt="`${entity.name} portrait`" class="mc-media-image" />
      <p v-else class="mc-muted">No portrait yet.</p>
      <div class="mc-media-controls">
        <select v-model="portraitDraft.style" aria-label="Portrait style" :disabled="!appearanceReady">
          <option v-for="style in PORTRAIT_STYLES" :key="style" :value="style">{{ PORTRAIT_STYLE_LABELS[style] }}</option>
        </select>
        <input v-if="portraitDraft.style === 'custom'" v-model="portraitDraft.customStyle" placeholder="Describe the style…" :disabled="!appearanceReady" />
        <select v-model="portraitDraft.framing" aria-label="Portrait framing" :disabled="!appearanceReady">
          <option v-for="framing in PORTRAIT_FRAMINGS" :key="framing" :value="framing">{{ PORTRAIT_FRAMING_LABELS[framing] }}</option>
        </select>
        <select v-model="portraitDraft.background" aria-label="Portrait background" :disabled="!appearanceReady">
          <option v-for="background in PORTRAIT_BACKGROUNDS" :key="background" :value="background">{{ PORTRAIT_BACKGROUND_LABELS[background] }}</option>
        </select>
      </div>
      <div class="mc-media-buttons">
        <button type="button" class="mc-btn mc-btn-secondary" :disabled="!appearanceReady || jobs.portraitInFlight(campaignId, entityId)" @click="generatePortrait">
          {{ jobs.portraitInFlight(campaignId, entityId) ? 'Portrait queued…' : 'Generate portrait' }}
        </button>
        <button type="button" class="mc-btn mc-btn-secondary" :disabled="portraitLinkBusy" @click="getPortraitLink">
          {{ portraitLinkBusy ? 'Getting link…' : 'Copy portrait link' }}
        </button>
        <button v-if="portrait" type="button" class="mc-btn mc-btn-secondary" @click="deletePortrait">Delete portrait</button>
      </div>
      <p v-if="!appearanceReady" class="mc-muted">Add an appearance before generating media.</p>
      <p v-if="status(portraitJob, 'Portrait')" class="mc-muted">{{ status(portraitJob, 'Portrait') }}</p>
      <input v-if="portraitLink" :value="portraitLink" readonly aria-label="Portrait link" class="mc-media-link" @focus="($event.target as HTMLInputElement).select()" />
    </div>

    <div v-if="isBoss" class="mc-media-block">
      <h3>Reveal video</h3>
      <p v-if="video && jobs.videoInFlight(campaignId, entityId)" class="mc-muted">Showing the previous video while the new reveal renders.</p>
      <video v-if="video" :src="mediaUrl(video)" controls preload="metadata" class="mc-media-video" :aria-label="`${entity.name} reveal video`" />
      <details v-if="videoPromptUsed" class="mc-media-prompt-history">
        <summary>Prompt used for this video</summary>
        <pre>{{ videoPromptUsed }}</pre>
      </details>
      <label :for="`reveal-video-prompt-${entityId}`" class="mc-media-prompt-label">Prompt sent to video generator</label>
      <textarea :id="`reveal-video-prompt-${entityId}`" v-model="revealPrompt" rows="5" placeholder="Write a reveal prompt…" aria-label="Reveal video prompt" />
      <p v-if="revealPrompt.trim()" class="mc-muted">Edit the prompt as you like. The text shown here is sent when you render.</p>
      <p v-if="automaticPromptLoading && !revealPrompt.trim()" class="mc-muted">Loading appearance-based prompt…</p>
      <div v-if="automaticPromptError && !revealPrompt.trim()" class="mc-media-buttons">
        <p class="mc-muted">{{ automaticPromptError }} Write a prompt or try again.</p>
        <button type="button" class="mc-btn mc-btn-secondary" @click="loadAutomaticPrompt">Retry</button>
      </div>
      <p v-if="!revealPrompt.trim() && !automaticPromptLoading && !automaticPromptError" class="mc-muted">Enter a prompt before rendering.</p>
      <div v-if="canUseAutomaticPrompt" class="mc-media-buttons">
        <button type="button" class="mc-btn mc-btn-secondary" @click="useAutomaticPrompt">Use appearance prompt</button>
      </div>
      <div class="mc-media-buttons">
        <button type="button" class="mc-btn" :disabled="!appearanceReady || !revealPrompt.trim() || jobs.videoInFlight(campaignId, entityId)" @click="renderRevealVideo">
          {{ jobs.videoInFlight(campaignId, entityId) ? 'Video queued…' : 'Render reveal video' }}
        </button>
      </div>
      <p v-if="status(videoJob, 'Reveal video')" class="mc-muted">{{ status(videoJob, 'Reveal video') }}</p>
    </div>
    <p v-if="error" class="mc-action-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.mc-media-actions { display: grid; gap: 1rem; }
.mc-media-actions h2, .mc-media-block h3 { margin: 0; font-family: var(--mc-display-font); }
.mc-export-links, .mc-media-buttons, .mc-media-controls { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
.mc-export-links a { display: inline-flex; align-items: center; min-height: 2.25rem; padding: .45rem .75rem; border: 1px solid var(--mc-border); border-radius: var(--mc-radius-sm); background: var(--mc-surface-raised); color: var(--mc-text-secondary); font-size: .85rem; text-decoration: none; }
.mc-export-links a:hover { border-color: var(--mc-interactive); color: var(--mc-text-primary); background: var(--mc-glow-violet); }
.mc-media-block { display: grid; gap: .7rem; padding: 1rem; border: 1px solid var(--mc-border); border-radius: var(--mc-radius); background: var(--mc-surface); }
.mc-media-image { width: min(100%, 24rem); max-height: 28rem; object-fit: cover; border-radius: var(--mc-radius-sm); }
.mc-media-video { width: min(100%, 36rem); border-radius: var(--mc-radius-sm); }
.mc-media-controls select, .mc-media-controls input, .mc-media-block textarea, .mc-media-link { min-width: 0; padding: .55rem .65rem; border: 1px solid var(--mc-border); border-radius: var(--mc-radius-sm); background: var(--mc-input); color: var(--mc-text-primary); }
.mc-media-block textarea { width: 100%; resize: vertical; }
.mc-media-link { width: 100%; }
.mc-media-prompt-label { color: var(--mc-text-secondary); font-size: .85rem; font-weight: 600; }
.mc-media-prompt-history summary { cursor: pointer; color: var(--mc-text-secondary); font-size: .85rem; }
.mc-media-prompt-history pre { margin: .6rem 0 0; padding: .75rem; border: 1px solid var(--mc-border); border-radius: var(--mc-radius-sm); background: var(--mc-input); color: var(--mc-text-primary); font: inherit; font-size: .85rem; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
