<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import type { components } from '../../api/schema'
import { ApiError, apiFetch } from '../../api/client'
import { BOSS_ROLES } from '../profile/profile'
import { hasNonBlankAppearance } from '../../lib/appearance'
import {
  PORTRAIT_BACKGROUNDS,
  PORTRAIT_BACKGROUND_LABELS,
  ARTWORK_FRAMINGS,
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
const initialFraming =
  props.entity.kind === 'character'
    ? 'headshot'
    : (ARTWORK_FRAMINGS[props.entity.kind]?.[0] ?? 'headshot')
const portraitDraft = ref({
  style: 'illustration' as PortraitStyle,
  framing: initialFraming as PortraitFraming,
  background: 'scene' as PortraitBackground,
  customStyle: '',
})
const editedRevealPrompt = ref<string | null>(null)
const automaticRevealPrompt = ref<string | null>(null)
const automaticPromptLoading = ref(false)
const automaticPromptError = ref('')
const direction = ref('')
const sourceImageId = ref<string | null>(null)
const pendingSourceImageId = ref<string | null>(null)
const enhancementJobId = ref<string | null>(null)
const enhancementBaseline = ref('')
const enhancementDirection = ref('')
const enhancedCandidate = ref<string | null>(null)
const enhancementError = ref('')
const enhancementSubmitting = ref(false)
const enhancementRequestId = ref('')
const dismissedEnhancements = ref<string[]>([])
const enhancementMayAutoApply = ref(true)
const pendingEnhancementPosts = ref<Record<string, number>>({})
const portraitLink = ref('')
const portraitLinkBusy = ref(false)
const socketGeneration = sessionGeneration()
let disconnect: (() => void) | null = null
let automaticPromptRequest = 0

const entityId = computed(() => props.entity.id)
const revealDraftKey = computed(
  () => `mythoscircle:reveal-prompt:${props.campaignId}:${entityId.value}`,
)
const data = computed(() => props.entity.data as Record<string, unknown>)
const appearanceReady = computed(() => hasNonBlankAppearance(data.value['appearance']))
const artworkReady = computed(() => {
  if (props.entity.kind === 'character') return appearanceReady.value
  const description = data.value['description']
  return (
    (typeof description === 'string' && !!description.trim()) ||
    (typeof props.entity.text === 'string' && !!props.entity.text.trim())
  )
})
const isCharacter = computed(() => props.entity.kind === 'character')
const artworkLabel = computed(() =>
  props.entity.kind === 'place'
    ? 'Place artwork'
    : props.entity.kind === 'faction'
      ? 'Faction artwork'
      : 'Portrait',
)
const artworkSubject = computed(() =>
  props.entity.kind === 'place'
    ? 'location'
    : props.entity.kind === 'faction'
      ? 'faction'
      : 'portrait',
)
const availableFramings = computed(
  () => ARTWORK_FRAMINGS[props.entity.kind] ?? ARTWORK_FRAMINGS.character,
)
const isBoss = computed(() => {
  const role = data.value['role']
  return typeof role === 'string' && BOSS_ROLES.has(role)
})
const portrait = computed(() => world.portraitFor(props.campaignId, entityId.value))
const sourcePortrait = computed(() => {
  const pending = enhancementJob.value?.payload?.['image_id']
  const id =
    pendingSourceImageId.value ??
    (typeof pending === 'string' ? pending : null) ??
    sourceImageId.value
  return id
    ? (world
        .mediaFor(props.campaignId)
        .find((row) => row.id === id && row.entity_id === entityId.value && row.kind === 'image') ??
        null)
    : portrait.value
})
const enhancementJob = computed(() =>
  enhancementJobId.value
    ? (jobs.forCampaign(props.campaignId).find((job) => job.id === enhancementJobId.value) ?? null)
    : null,
)
const enhancementBusy = computed(
  () =>
    enhancementSubmitting.value ||
    enhancementJob.value?.state === 'queued' ||
    enhancementJob.value?.state === 'running' ||
    jobs.videoPromptInFlight(props.campaignId, entityId.value),
)
const video = computed(() => world.videoFor(props.campaignId, entityId.value))
const videoPromptUsed = computed(() => {
  if (!video.value) return null
  const job = jobs
    .forCampaign(props.campaignId)
    .find(
      (candidate) =>
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
    persistRevealDraft()
  },
})
const canUseAutomaticPrompt = computed(
  () => automaticRevealPrompt.value !== null && revealPrompt.value !== automaticRevealPrompt.value,
)

watch(
  () => props.entity.kind,
  (kind) => {
    const options = ARTWORK_FRAMINGS[kind] ?? ARTWORK_FRAMINGS.character
    if (!options.includes(portraitDraft.value.framing)) {
      portraitDraft.value.framing = kind === 'character' ? 'headshot' : options[0]!
    }
  },
)

watch([() => props.campaignId, entityId], restoreRevealDraft)
watch(
  [() => props.campaignId, entityId, () => JSON.stringify(data.value['appearance']), isBoss],
  () => {
    void loadAutomaticPrompt()
  },
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
    if (
      /provider connection error|connection refused|all connection attempts failed|connecterror/i.test(
        job.error ?? '',
      )
    ) {
      return 'Image generator is offline. Start the image service and try again.'
    }
    return `${label} failed: ${job.error ?? 'unknown error'}`
  }
  return null
}

async function generatePortrait() {
  if (!artworkReady.value || jobs.portraitInFlight(props.campaignId, entityId.value)) return
  error.value = ''
  try {
    await jobs.submitPortrait(props.campaignId, entityId.value, {
      style: portraitDraft.value.style,
      framing: portraitDraft.value.framing,
      background: portraitDraft.value.background,
      customStyle:
        portraitDraft.value.style === 'custom' ? portraitDraft.value.customStyle : undefined,
    })
    await jobs.syncList(props.campaignId)
  } catch (err) {
    error.value =
      err instanceof ApiError ? err.message : `Could not generate ${artworkSubject.value} artwork.`
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

function persistRevealDraft() {
  if (socketGeneration !== sessionGeneration()) return
  try {
    globalThis.localStorage.setItem(
      `${revealDraftKey.value}:image-bound`,
      JSON.stringify({
        prompt: editedRevealPrompt.value,
        direction: direction.value,
        imageId: sourceImageId.value,
        pendingImageId: pendingSourceImageId.value,
        jobId: enhancementJobId.value,
        baseline: enhancementBaseline.value,
        submittedDirection: enhancementDirection.value,
        candidate: enhancedCandidate.value,
        requestId: enhancementRequestId.value,
        dismissed: dismissedEnhancements.value,
        mayAutoApply: enhancementMayAutoApply.value,
      }),
    )
  } catch {
    /* Editing remains usable without browser storage. */
  }
}

function restoreRevealDraft() {
  automaticPromptRequest += 1
  enhancementError.value = ''
  enhancementSubmitting.value = false
  enhancementRequestId.value = ''
  dismissedEnhancements.value = []
  enhancementMayAutoApply.value = true
  editedRevealPrompt.value = null
  direction.value = ''
  sourceImageId.value = null
  pendingSourceImageId.value = null
  enhancementJobId.value = null
  enhancementBaseline.value = ''
  enhancementDirection.value = ''
  enhancedCandidate.value = null
  try {
    const stored = globalThis.localStorage.getItem(`${revealDraftKey.value}:image-bound`)
    if (stored) {
      const state = JSON.parse(stored) as Record<string, unknown>
      editedRevealPrompt.value = typeof state.prompt === 'string' ? state.prompt : null
      direction.value = typeof state.direction === 'string' ? state.direction : ''
      sourceImageId.value = typeof state.imageId === 'string' ? state.imageId : null
      pendingSourceImageId.value =
        typeof state.pendingImageId === 'string' ? state.pendingImageId : null
      enhancementJobId.value = typeof state.jobId === 'string' ? state.jobId : null
      enhancementBaseline.value = typeof state.baseline === 'string' ? state.baseline : ''
      enhancementDirection.value =
        typeof state.submittedDirection === 'string' ? state.submittedDirection : ''
      enhancedCandidate.value = typeof state.candidate === 'string' ? state.candidate : null
      enhancementRequestId.value = typeof state.requestId === 'string' ? state.requestId : ''
      dismissedEnhancements.value = Array.isArray(state.dismissed)
        ? state.dismissed.filter((id): id is string => typeof id === 'string')
        : []
      enhancementMayAutoApply.value = state.mayAutoApply !== false
    } else editedRevealPrompt.value = globalThis.localStorage.getItem(revealDraftKey.value)
  } catch {
    /* Keep the editable defaults. */
  }
  recoverEnhancement()
}

function resetEnhancementSource() {
  editedRevealPrompt.value = revealPrompt.value
  dismissedEnhancements.value = [
    ...new Set([
      ...dismissedEnhancements.value,
      ...jobs
        .forCampaign(props.campaignId)
        .filter(
          (job) => job.kind === 'video_prompt' && job.payload?.['entity_id'] === entityId.value,
        )
        .map((job) => job.id),
    ]),
  ]
  enhancementRequestId.value = ''
  enhancementSubmitting.value = false
  sourceImageId.value = null
  pendingSourceImageId.value = null
  enhancementJobId.value = null
  enhancedCandidate.value = null
  enhancementError.value = ''
  persistRevealDraft()
}

function useAutomaticPrompt() {
  if (automaticRevealPrompt.value === null) return
  resetEnhancementSource()
  editedRevealPrompt.value = automaticRevealPrompt.value
  persistRevealDraft()
}

function recoverEnhancement() {
  if (
    socketGeneration !== sessionGeneration() ||
    enhancementJobId.value ||
    enhancementSubmitting.value ||
    pendingEnhancementPosts.value[revealDraftKey.value]
  )
    return
  const job = jobs
    .forCampaign(props.campaignId)
    .filter(
      (job) =>
        job.kind === 'video_prompt' &&
        job.payload?.['entity_id'] === entityId.value &&
        typeof job.payload?.['image_id'] === 'string',
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
  if (!job || dismissedEnhancements.value.includes(job.id)) return
  enhancementMayAutoApply.value = editedRevealPrompt.value === null
  enhancementBaseline.value = revealPrompt.value
  const submitted = job.payload?.['prompt']
  enhancementDirection.value = typeof submitted === 'string' ? submitted : ''
  if (!direction.value) direction.value = enhancementDirection.value
  enhancementJobId.value = job.id
  pendingSourceImageId.value =
    typeof job.payload?.['image_id'] === 'string' ? job.payload['image_id'] : null
  persistRevealDraft()
}
watch([() => jobs.forCampaign(props.campaignId), pendingEnhancementPosts], recoverEnhancement, {
  deep: true,
})

function applyEnhancedPrompt() {
  const job = enhancementJob.value
  if (!job || !enhancedCandidate.value || typeof job.result?.['image_id'] !== 'string') return
  dismissedEnhancements.value.push(job.id)
  editedRevealPrompt.value = enhancedCandidate.value
  sourceImageId.value = job.result['image_id']
  pendingSourceImageId.value = null
  enhancedCandidate.value = null
  enhancementJobId.value = null
  persistRevealDraft()
}

watch(direction, persistRevealDraft)
watch(
  enhancementJob,
  (job) => {
    if (socketGeneration !== sessionGeneration() || !job || job.state !== 'succeeded') return
    const prompt = job.result?.['prompt']
    if (typeof prompt !== 'string' || typeof job.result?.['image_id'] !== 'string') return
    enhancedCandidate.value = prompt
    if (
      enhancementMayAutoApply.value &&
      revealPrompt.value === enhancementBaseline.value &&
      direction.value === enhancementDirection.value
    )
      applyEnhancedPrompt()
    else persistRevealDraft()
  },
  { deep: true },
)

async function enhanceRevealPrompt() {
  if (
    !sourcePortrait.value ||
    !direction.value.trim() ||
    direction.value.length > 8000 ||
    enhancementBusy.value
  )
    return
  const campaign = props.campaignId,
    entity = entityId.value
  const key = revealDraftKey.value,
    generation = sessionGeneration()
  pendingSourceImageId.value = sourcePortrait.value.id
  enhancementSubmitting.value = true
  pendingEnhancementPosts.value[key] = (pendingEnhancementPosts.value[key] ?? 0) + 1
  const requestId = `${Date.now()}:${Math.random()}`
  enhancementRequestId.value = requestId
  enhancementMayAutoApply.value = true
  enhancementError.value = ''
  editedRevealPrompt.value = revealPrompt.value
  enhancementBaseline.value = revealPrompt.value
  enhancementDirection.value = direction.value
  enhancedCandidate.value = null
  persistRevealDraft()
  let savedState: string | null = null
  try {
    savedState = globalThis.localStorage.getItem(`${key}:image-bound`)
  } catch {
    /* Storage is optional. */
  }
  try {
    const job = await jobs.submitRevealVideoPrompt(
      campaign,
      entity,
      sourcePortrait.value.id,
      direction.value,
    )
    if (generation !== sessionGeneration()) return
    if (key !== revealDraftKey.value) {
      try {
        const state = JSON.parse(
          globalThis.localStorage.getItem(`${key}:image-bound`) ?? savedState ?? '{}',
        ) as Record<string, unknown>
        if (state.requestId !== requestId) {
          const dismissed = Array.isArray(state.dismissed) ? state.dismissed : []
          state.dismissed = [...dismissed, job.id]
          globalThis.localStorage.setItem(`${key}:image-bound`, JSON.stringify(state))
          return
        }
        state.jobId = job.id
        globalThis.localStorage.setItem(`${key}:image-bound`, JSON.stringify(state))
      } catch {
        /* Keep the active entity untouched. */
      }
      return
    }
    if (enhancementRequestId.value !== requestId) {
      dismissedEnhancements.value.push(job.id)
      persistRevealDraft()
      return
    }
    enhancementJobId.value = job.id
    persistRevealDraft()
    await jobs.syncList(campaign)
  } catch (err) {
    if (
      key === revealDraftKey.value &&
      generation === sessionGeneration() &&
      enhancementRequestId.value === requestId
    )
      enhancementError.value =
        err instanceof ApiError ? err.message : 'Could not enhance the reveal prompt.'
  } finally {
    pendingEnhancementPosts.value[key] = Math.max(0, (pendingEnhancementPosts.value[key] ?? 1) - 1)
    if (
      key === revealDraftKey.value &&
      generation === sessionGeneration() &&
      enhancementRequestId.value === requestId
    )
      enhancementSubmitting.value = false
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
    if (request === automaticPromptRequest)
      automaticPromptError.value = 'Could not load the automatic prompt.'
  } finally {
    if (request === automaticPromptRequest) automaticPromptLoading.value = false
  }
}

async function renderRevealVideo() {
  if (
    !isBoss.value ||
    (!sourceImageId.value && !appearanceReady.value) ||
    jobs.videoInFlight(props.campaignId, entityId.value)
  )
    return
  const prompt = revealPrompt.value
  error.value = ''
  if (!prompt.trim()) {
    error.value = 'Enter a video prompt or load the automatic prompt before rendering.'
    return
  }
  try {
    await jobs.submitRevealVideo(
      props.campaignId,
      entityId.value,
      prompt,
      sourceImageId.value ?? undefined,
    )
    await jobs.syncList(props.campaignId)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not generate the reveal video.'
  }
}

onMounted(() => {
  if (socketGeneration !== sessionGeneration()) return
  restoreRevealDraft()
  recoverEnhancement()
  void world.fetchMedia(props.campaignId)
  void jobs.syncList(props.campaignId).catch(() => {})
  void loadAutomaticPrompt()
  disconnect = connectJobSocket(
    props.campaignId,
    (message) => {
      void world.handleJobMessage(props.campaignId, message)
    },
    { generation: socketGeneration },
  )
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

    <details class="mc-media-block mc-media-disclosure">
      <summary class="mc-media-summary">
        <span>{{ artworkLabel }}</span>
        <span class="mc-muted">{{ status(portraitJob, artworkLabel) ?? (portrait ? 'Artwork ready' : 'No artwork yet') }}</span>
      </summary>
      <img v-if="portrait" :src="mediaUrl(portrait)" :alt="`${entity.name} ${artworkSubject} artwork`" class="mc-media-image" />
      <p v-else class="mc-muted">No artwork yet.</p>
      <div class="mc-media-controls">
        <select v-model="portraitDraft.style" :aria-label="`${artworkLabel} style`" :disabled="!artworkReady">
          <option v-for="style in PORTRAIT_STYLES" :key="style" :value="style">{{ PORTRAIT_STYLE_LABELS[style] }}</option>
        </select>
        <input v-if="portraitDraft.style === 'custom'" v-model="portraitDraft.customStyle" placeholder="Describe the style…" :disabled="!artworkReady" />
        <select v-model="portraitDraft.framing" :aria-label="`${artworkLabel} composition`" :disabled="!artworkReady">
          <option v-for="framing in availableFramings" :key="framing" :value="framing">{{ PORTRAIT_FRAMING_LABELS[framing] }}</option>
        </select>
        <select v-model="portraitDraft.background" :aria-label="`${artworkLabel} background`" :disabled="!artworkReady">
          <option v-for="background in PORTRAIT_BACKGROUNDS" :key="background" :value="background">{{ PORTRAIT_BACKGROUND_LABELS[background] }}</option>
        </select>
      </div>
      <div class="mc-media-buttons">
        <button type="button" class="mc-btn mc-btn-secondary" :disabled="!artworkReady || jobs.portraitInFlight(campaignId, entityId)" @click="generatePortrait">
          {{ jobs.portraitInFlight(campaignId, entityId) ? 'Artwork queued…' : `Generate ${artworkSubject} artwork` }}
        </button>
        <button v-if="isCharacter" type="button" class="mc-btn mc-btn-secondary" :disabled="portraitLinkBusy" @click="getPortraitLink">
          {{ portraitLinkBusy ? 'Getting link…' : 'Copy portrait link' }}
        </button>
        <button v-if="portrait" type="button" class="mc-btn mc-btn-secondary" @click="deletePortrait">Delete artwork</button>
      </div>
      <p v-if="!artworkReady" class="mc-muted">{{ isCharacter ? 'Add an appearance before generating a portrait.' : 'Add a description before generating artwork.' }}</p>
      <p v-if="status(portraitJob, artworkLabel)" class="mc-muted">{{ status(portraitJob, artworkLabel) }}</p>
      <input v-if="portraitLink" :value="portraitLink" readonly aria-label="Portrait link" class="mc-media-link" @focus="($event.target as HTMLInputElement).select()" />
    </details>

    <details v-if="isBoss" class="mc-media-block mc-media-disclosure">
      <summary class="mc-media-summary">
        <span>Reveal video</span>
        <span class="mc-muted">{{
          status(videoJob, 'Reveal video') ?? (video ? 'Video ready' : 'No video yet')
        }}</span>
      </summary>
      <p v-if="video && jobs.videoInFlight(campaignId, entityId)" class="mc-muted">
        Showing the previous video while the new reveal renders.
      </p>
      <video
        v-if="video"
        :src="mediaUrl(video)"
        controls
        preload="metadata"
        class="mc-media-video"
        :aria-label="`${entity.name} reveal video`"
      />
      <details v-if="videoPromptUsed" class="mc-media-prompt-history">
        <summary>Prompt used for this video</summary>
        <pre>{{ videoPromptUsed }}</pre>
      </details>
      <p class="mc-muted">
        Enhance your direction using the source portrait. Review the H3 draft with image alignment,
        visual action, soundscape and music before rendering.
      </p>
      <img
        v-if="sourcePortrait"
        :src="mediaUrl(sourcePortrait)"
        :alt="`${entity.name} source portrait for reveal`"
        class="mc-media-image"
      />
      <p v-else class="mc-muted">
        {{
          sourceImageId
            ? 'The source portrait is no longer available. Rendering will fail until you choose another source.'
            : 'Generate a portrait before enhancing your direction.'
        }}
      </p>
      <button v-if="(sourceImageId || enhancementJobId || pendingSourceImageId) && (!portrait || (sourceImageId && sourceImageId !== portrait.id) || sourcePortrait?.id !== portrait.id)" type="button" class="mc-btn mc-btn-secondary" @click="resetEnhancementSource">Use latest portrait</button>
      <label :for="`reveal-direction-${entityId}`">Your video direction</label>
      <textarea
        :id="`reveal-direction-${entityId}`"
        v-model="direction"
        rows="3"
        maxlength="8000"
        aria-label="Your video direction"
        placeholder="Describe action, camera motion and audio…"
      />
      <button
        type="button"
        class="mc-btn mc-btn-secondary"
        :disabled="
          !sourcePortrait || !direction.trim() || direction.length > 8000 || enhancementBusy
        "
        @click="enhanceRevealPrompt"
      >
        {{ enhancementBusy ? 'Enhancing prompt…' : 'Enhance with portrait' }}
      </button>
      <p
        v-if="enhancementJob?.state === 'queued' || enhancementJob?.state === 'running'"
        class="mc-muted"
        role="status"
      >
        {{
          enhancementJob.state === 'queued'
            ? 'Prompt enhancement queued…'
            : 'Enhancing with the source portrait…'
        }}
      </p>
      <p
        v-if="enhancementError || enhancementJob?.state === 'failed'"
        class="mc-action-error"
        role="alert"
      >
        {{ enhancementError || enhancementJob?.error || 'Prompt enhancement failed.' }} Your
        direction and prompt remain editable.
      </p>
      <div v-if="enhancedCandidate">
        <p class="mc-muted">
          Your edits were kept. Review the completed enhancement before applying it.
        </p>
        <pre class="mc-enhanced-preview">{{ enhancedCandidate }}</pre>
        <button type="button" class="mc-btn mc-btn-secondary" @click="applyEnhancedPrompt">
          Use enhanced prompt
        </button>
      </div>
      <label :for="`reveal-video-prompt-${entityId}`" class="mc-media-prompt-label"
        >Prompt sent to video generator</label
      >
      <textarea
        :id="`reveal-video-prompt-${entityId}`"
        v-model="revealPrompt"
        rows="5"
        placeholder="Write a reveal prompt…"
        aria-label="Reveal video prompt"
      />
      <p v-if="revealPrompt.trim()" class="mc-muted">
        Edit the prompt as you like. The text shown here is sent when you render.
      </p>
      <p v-if="automaticPromptLoading && !revealPrompt.trim()" class="mc-muted">
        Loading appearance-based prompt…
      </p>
      <div v-if="automaticPromptError && !revealPrompt.trim()" class="mc-media-buttons">
        <p class="mc-muted">{{ automaticPromptError }} Write a prompt or try again.</p>
        <button type="button" class="mc-btn mc-btn-secondary" @click="loadAutomaticPrompt">
          Retry
        </button>
      </div>
      <p
        v-if="!revealPrompt.trim() && !automaticPromptLoading && !automaticPromptError"
        class="mc-muted"
      >
        Enter a prompt before rendering.
      </p>
      <div v-if="canUseAutomaticPrompt" class="mc-media-buttons">
        <button type="button" class="mc-btn mc-btn-secondary" @click="useAutomaticPrompt">
          Use appearance prompt
        </button>
      </div>
      <div class="mc-media-buttons">
        <button
          type="button"
          class="mc-btn"
          :disabled="
            (!sourceImageId && !appearanceReady) ||
            !revealPrompt.trim() ||
            jobs.videoInFlight(campaignId, entityId)
          "
          @click="renderRevealVideo"
        >
          {{ jobs.videoInFlight(campaignId, entityId) ? 'Video queued…' : 'Render reveal video' }}
        </button>
      </div>
      <p v-if="status(videoJob, 'Reveal video')" class="mc-muted">
        {{ status(videoJob, 'Reveal video') }}
      </p>
    </details>
    <p v-if="error" class="mc-action-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.mc-enhanced-preview { white-space: pre-wrap; overflow-wrap: anywhere; }
.mc-media-actions { display: grid; gap: 1rem; }
.mc-media-actions h2, .mc-media-block h3 { margin: 0; font-family: var(--mc-display-font); }
.mc-export-links, .mc-media-buttons, .mc-media-controls { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
.mc-export-links a { display: inline-flex; align-items: center; min-height: 2.25rem; padding: .45rem .75rem; border: 1px solid var(--mc-border); border-radius: var(--mc-radius-sm); background: var(--mc-surface-raised); color: var(--mc-text-secondary); font-size: .85rem; text-decoration: none; }
.mc-export-links a:hover { border-color: var(--mc-interactive); color: var(--mc-text-primary); background: var(--mc-glow-violet); }
.mc-media-block { display: grid; gap: .7rem; padding: 1rem; border: 1px solid var(--mc-border); border-radius: var(--mc-radius); background: var(--mc-surface); }
.mc-media-disclosure { padding: 0; }
.mc-media-summary { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .3rem .8rem; padding: 1rem; cursor: pointer; list-style: none; }
.mc-media-summary::-webkit-details-marker { display: none; }
.mc-media-summary::after { content: '＋'; color: var(--mc-interactive); font-weight: 700; }
.mc-media-disclosure[open] > .mc-media-summary { border-bottom: 1px solid var(--mc-border); }
.mc-media-disclosure[open] > .mc-media-summary::after { content: '−'; }
.mc-media-disclosure > :not(summary) { margin-left: 1rem; margin-right: 1rem; }
.mc-media-disclosure > :last-child { margin-bottom: 1rem; }
.mc-media-summary > span:first-child { font-family: var(--mc-display-font); font-weight: 700; }
.mc-media-image { width: min(100%, 24rem); max-height: 28rem; object-fit: contain; border-radius: var(--mc-radius-sm); }
.mc-media-video { width: min(100%, 36rem); border-radius: var(--mc-radius-sm); }
.mc-media-controls select, .mc-media-controls input, .mc-media-block textarea, .mc-media-link { min-width: 0; padding: .55rem .65rem; border: 1px solid var(--mc-border); border-radius: var(--mc-radius-sm); background: var(--mc-input); color: var(--mc-text-primary); }
.mc-media-block textarea { width: calc(100% - 2rem); box-sizing: border-box; resize: vertical; }
.mc-media-block label { display: block; }
.mc-media-link { width: 100%; }
.mc-media-prompt-label { color: var(--mc-text-secondary); font-size: .85rem; font-weight: 600; }
.mc-media-prompt-history summary { cursor: pointer; color: var(--mc-text-secondary); font-size: .85rem; }
.mc-media-prompt-history pre { margin: .6rem 0 0; padding: .75rem; border: 1px solid var(--mc-border); border-radius: var(--mc-radius-sm); background: var(--mc-input); color: var(--mc-text-primary); font: inherit; font-size: .85rem; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
