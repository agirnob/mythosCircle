<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import type { components } from '../api/schema'
import { ApiError, apiFetch } from '../api/client'
import StatBlock from '../components/StatBlock.vue'
import { useAuthStore } from '../stores/auth'
import { hasNonBlankAppearance } from '../lib/appearance'
import { useJobsStore } from '../stores/jobs'
import { useWorldStore } from '../stores/world'
import { connectJobSocket } from '../ws'

type WorldExport = components['schemas']['WorldExport']
type EntityExport = components['schemas']['EntityExport']
type EdgeExport = components['schemas']['EdgeExport']

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string

const world = useWorldStore()
const jobs = useJobsStore()

let disconnectSocket: (() => void) | null = null
let connectedCampaign: string | null = null
let disposed = false

/**
 * Idempotent mount/retry sequence: load, then subscribe. The socket gate
 * runs AFTER the load so a foreign/unknown campaign (404) or a failed
 * load never opens a socket — the WS handshake would 4401 and force a
 * logout. The gate tests notFound/error rather than a null snapshot: a
 * remount whose load coalesced into an in-flight fetch (world still
 * null, trailing fetch owed) must still subscribe. The small window
 * between the snapshot read and the socket open is accepted residual
 * risk — the onReconnect re-sync covers a commit landing inside it.
 */
async function start() {
  await world.load(campaignId)
  if (disposed) return
  const entry = world.entry(campaignId)
  if (entry.notFound || entry.error) return
  // Spec-4.1: the media manifest is fetched separately from the snapshot
  // (export stays media-free). Decorative — failures never break the view.
  void world.fetchMedia(campaignId)
  // The job list is loaded here, not only after an enqueue (2026-09-11): a
  // re-roll stages a PROPOSAL whose only trace on this screen is its job, so
  // a page load after the job finished has to find it. Decorative — a failed
  // sync never breaks the world view and the socket still delivers new jobs.
  void jobs.syncList(campaignId).catch(() => {})
  if (connectedCampaign === campaignId) return
  connectedCampaign = campaignId
  disconnectSocket = connectJobSocket(
    campaignId,
    (message) => {
      void world.handleJobMessage(campaignId, message)
    },
    {
      onReconnect: () => {
        world.requestRefetch(campaignId)
      },
      onAuthFailure: () => {
        const auth = useAuthStore()
        auth.account = null
        void router.push({ name: 'login' })
      },
    },
  )
}

/**
 * A post-connect refetch resolving 404 (campaign deleted or ownership
 * revoked) must tear the socket down — otherwise it keeps receiving
 * unrelated frames and refetching forever.
 */
watch(
  () => world.entry(campaignId).notFound,
  (notFound) => {
    if (notFound && connectedCampaign === campaignId) {
      disconnectSocket?.()
      disconnectSocket = null
      connectedCampaign = null
    }
  },
)

onMounted(() => {
  void start()
})

onUnmounted(() => {
  disposed = true
  disconnectSocket?.()
})

const entry = computed(() => world.entry(campaignId))
const exportData = computed<WorldExport | null>(() => entry.value.world)
const revision = computed(() => exportData.value?.revision ?? null)
const entities = computed<EntityExport[]>(() => exportData.value?.entities ?? [])

/** Entities grouped by kind, kinds in first-seen (rowid) order. */
const kinds = computed(() => {
  const groups = new Map<string, EntityExport[]>()
  for (const entity of entities.value) {
    const group = groups.get(entity.kind)
    if (group) {
      group.push(entity)
    } else {
      groups.set(entity.kind, [entity])
    }
  }
  return [...groups.entries()]
})

const nameById = computed(() => {
  const names = new Map<string, string>()
  for (const entity of entities.value) {
    names.set(entity.id, entity.name)
  }
  return names
})

/**
 * Frontend mirror of 2.6's `_edge_label` (AD-23): neutral types render
 * bare; debt/grudge/loyalty/ally/enemy carry the counter.
 */
const COUNTER_TYPES: ReadonlySet<string> = new Set([
  'debt',
  'grudge',
  'loyalty',
  'ally_of',
  'enemy_of',
  'controls',
  'worships',
  'protects',
])

function edgeLabel(edge: EdgeExport): string {
  return COUNTER_TYPES.has(edge.type) ? `${edge.type}(${edge.counter})` : edge.type
}

/**
 * The closed edge vocabulary (AD-5, 16 members since 2026-09-13) — the
 * add-relation picker. Mirrors app/store/commit.py EDGE_TYPES: the six
 * role-bearing types (bases_at, controls, employs, worships, hails_from,
 * protects) were added to absorb the meanings that used to collapse into
 * ``relationship``.
 */
const EDGE_VOCAB: readonly string[] = [
  'relationship',
  'debt',
  'grudge',
  'loyalty',
  'member_of',
  'located_in',
  'rival_of',
  'kin_of',
  'ally_of',
  'enemy_of',
  'bases_at',
  'controls',
  'employs',
  'worships',
  'hails_from',
  'protects',
]

interface RelationLine {
  edgeId: string
  srcName: string
  dstName: string
  label: string
  type: string
  counter: number
  /** True when this card's entity is the edge's source (arrow direction). */
  outbound: boolean
}

const relationsByEntity = computed(() => {
  const lines = new Map<string, RelationLine[]>()
  for (const edge of exportData.value?.edges ?? []) {
    // A self-loop (src === dst) must land once, not twice, or the row
    // doubles and the v-for key collides.
    const endpoints = edge.src === edge.dst ? [edge.src] : [edge.src, edge.dst]
    for (const endpoint of endpoints) {
      const list = lines.get(endpoint)
      const line: RelationLine = {
        edgeId: edge.id,
        srcName: nameById.value.get(edge.src) ?? '(unknown)',
        dstName: nameById.value.get(edge.dst) ?? '(unknown)',
        label: edgeLabel(edge),
        type: edge.type,
        counter: edge.counter,
        outbound: endpoint === edge.src,
      }
      if (list) {
        list.push(line)
      } else {
        lines.set(endpoint, [line])
      }
    }
  }
  return lines
})

function relationsFor(entityId: string): RelationLine[] {
  return relationsByEntity.value.get(entityId) ?? []
}

// ---------------------------------------------------------------------------
// Inline relation editing (spec-3-4, FR9): every mutation goes through the
// world store's edge actions (the backend commit path) and comes back as a
// coalesced refetch. Errors render inline on the owning card.
// ---------------------------------------------------------------------------

const EDGE_DIRECTIONS = ['outbound', 'inbound'] as const

const addingFor = ref<string | null>(null)
const addType = ref<string>(EDGE_VOCAB[0])
const addTargetId = ref<string>('')
const addCounter = ref<number>(1)
const addDirection = ref<(typeof EDGE_DIRECTIONS)[number]>('outbound')

const editingEdgeId = ref<string | null>(null)
const editCounter = ref<number>(1)

const relationBusy = ref(false)
const relationErrors = ref<Record<string, string>>({})

/**
 * Only AR24 sectioned records are regenerable (the enqueue validator
 * 422s everything else — a build-in entity has no sectioned profile).
 * The AR19/AR24 core markers: non-blank name, role, personality, secret.
 */
function isRegenerable(entity: EntityExport): boolean {
  const data = entity.data
  if (typeof data !== 'object' || data === null) return false
  return ['name', 'role', 'personality', 'secret'].every(
    (key) => typeof (data as Record<string, unknown>)[key] === 'string',
  )
}

/** Spec-3.5: entity-id -> in-flight regeneration (whole or per-section). */
const regeneratingId = ref<string | null>(null)
const regenerateErrors = ref<Record<string, string>>({})

/**
 * Entity-id -> the re-roll notice to render on its card (spec-3.5).
 *
 * The click only ENQUEUES, and a re-roll stages a PROPOSAL: this world stays
 * byte-identical until the DM accepts it on the accept screen. So the job —
 * never the button — is the source of truth, and the notice is what tells
 * the DM a proposal exists at all (dogfood 2026-09-11: picking a section and
 * clicking Regenerate left the screen indistinguishable before and after,
 * because the only feedback was a button label that flipped back in a tick).
 *
 * A succeeded notice clears once the world has committed past the re-roll
 * that produced it — i.e. once the DM has accepted something.
 */
const regenerateNotices = computed(() => {
  const out: Record<string, { kind: 'running' | 'ready' | 'failed'; label: string; error: string }> = {}
  const committedAt = revision.value?.created_at ?? ''
  const regenJobs = jobs
    .forCampaign(campaignId)
    .filter((job) => job.kind === 'regenerate')
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
  for (const job of regenJobs) {
    const payload = job.payload as { target?: { id?: string }; sections?: string[] } | null
    const targetId = payload?.target?.id
    if (!targetId) continue
    const sections = payload?.sections ?? []
    const label = sections.length
      ? sections.map((section) => FIELD_LABELS[section] ?? section).join(', ')
      : 'whole character'
    if (job.state === 'queued' || job.state === 'running') {
      out[targetId] = { kind: 'running', label, error: '' }
    } else if (job.state === 'succeeded') {
      const finishedAt = job.finished_at ?? job.created_at
      if (finishedAt > committedAt) out[targetId] = { kind: 'ready', label, error: '' }
      else delete out[targetId]
    } else {
      out[targetId] = { kind: 'failed', label, error: job.error ?? 'unknown error' }
    }
  }
  return out
})

/** The regenerable AR24 content sections (spec-3.5 REGEN_SECTIONS): the
 * identity anchor (name, role, level_cr, race_type, class_profession,
 * alignment) is NOT regenerable — hand-edit territory. */
const REGEN_SECTIONS = [
  'personality',
  'secret',
  'rumor',
  'party_hook',
  'appearance',
  'background',
  'goals',
  'relationships',
  'voice_style',
  'catchphrases',
  'stat_block',
  'world_integration',
  'boss',
] as const

/** Entity-id -> the regen scope: '' = whole character, otherwise exactly
 * one section (the backend re-rolls exactly the listed sections and
 * preserves everything else byte-identical). */
const regenScope = ref<Record<string, string>>({})

/**
 * Regeneration (spec-3.5): whole-character or one section. Stages a
 * regenerate job whose new proposal surfaces on the accept screen
 * (CandidatesView). The committed entity is untouched until the DM
 * accepts the proposal.
 */
async function regenerateEntity(entityId: string) {
  regenerateErrors.value[entityId] = ''
  regeneratingId.value = entityId
  const scope = regenScope.value[entityId] ?? ''
  const sections = scope === '' ? null : [scope]
  try {
    await jobs.submitRegenerate(campaignId, { kind: 'entity', id: entityId }, sections)
    await jobs.syncList(campaignId)
  } catch (err) {
    regenerateErrors.value[entityId] =
      err instanceof ApiError ? err.message : 'Could not regenerate the entity.'
  } finally {
    regeneratingId.value = null
  }
}

// ---------------------------------------------------------------------------
// Portraits (spec-4.1, FR12/AD-10): the committed entity's AR24
// `appearance` is the prompt source — the backend rejects any enqueue
// whose appearance is blank, and the card's button mirrors that gate (a
// forced enqueue would 422, so the button is disabled until an
// appearance exists). The manifest renders the latest portrait; image
// jobs show their queue position / progress / failure inline.
// ---------------------------------------------------------------------------

const portraitErrors = ref<Record<string, string>>({})

/** Terminal image-job states (the jobs store's TERMINAL_STATES, mirrored
 * locally — the store does not export it). */
const PORTRAIT_TERMINAL: ReadonlySet<string> = new Set(['succeeded', 'failed', 'cancelled'])

/** A failed ENQUEUE leaves stale red text; it is cleared when the
 * entity's latest image job reaches a terminal state (a re-trigger
 * landed one way or another) or when the media manifest changes (a new
 * portrait arrived). Watches the whole campaign's signature — cheap and
 * covers every entity card at once. */
watch(
  () => [
    jobs
      .forCampaign(campaignId)
      .filter(
        (job) => (job.kind === 'image' || job.kind === 'video') && PORTRAIT_TERMINAL.has(job.state),
      )
      .map((job) => `${job.id}:${job.state}`)
      .join('|'),
    world
      .mediaFor(campaignId)
      .map((row) => row.id)
      .join('|'),
  ],
  () => {
    portraitErrors.value = {}
    // A manifest change can orphan a minted link (newest portrait
    // replaced, row deleted) — drop kept links so the next click
    // re-mints against the current manifest, never a stale file.
    portraitLinks.value = {}
  },
)

/** The entity's latest manifest row (newest created_at), or null. */
function portraitFor(entity: EntityExport) {
  return world.portraitFor(campaignId, entity.id)
}

/** The same-origin file URL — the session cookie (path /api) authenticates it. */
function portraitUrl(entity: EntityExport): string {
  const row = portraitFor(entity)
  return row
    ? `/api/campaigns/${encodeURIComponent(campaignId)}/media/${encodeURIComponent(row.entity_id)}/${row.filename}`
    : ''
}

/** Spec-5-2: signed Forge portrait URL per entity — minted on click
 * (the URL is absolute + expiring, pasted into Forge's per-unit
 * portrait override), then kept for one-click re-copy. Failures render
 * inline on the owning card; a clipboard denial keeps the link visible
 * for manual copy. */
const portraitLinks = ref<Record<string, string>>({})
const portraitLinkBusy = ref<string | null>(null)
const portraitLinkErrors = ref<Record<string, string>>({})

async function copyText(text: string, entityId: string) {
  try {
    await globalThis.navigator.clipboard.writeText(text)
  } catch {
    portraitLinkErrors.value[entityId] = 'Copy failed — the link is shown below; copy it manually.'
  }
}

async function fetchPortraitLink(entityId: string) {
  portraitLinkErrors.value[entityId] = ''
  const existing = portraitLinks.value[entityId]
  if (existing) {
    await copyText(existing, entityId)
    return
  }
  portraitLinkBusy.value = entityId
  try {
    const body = await apiFetch<{ url: string; expires_at: string }>(
      `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}/portrait-url`,
    )
    portraitLinks.value[entityId] = body.url
    await copyText(body.url, entityId)
  } catch (err) {
    portraitLinkErrors.value[entityId] =
      err instanceof ApiError ? err.message : 'Could not get the portrait link.'
  } finally {
    portraitLinkBusy.value = null
  }
}

/** Spec-5.1: pure-projection download links. Same-origin GETs — the
 * session cookie (path /api) authenticates them; `download` saves the
 * attachment without navigation. */
function worldExportUrl(format: 'markdown' | 'html'): string {
  return `/api/campaigns/${encodeURIComponent(campaignId)}/export?format=${format}`
}

function entityExportUrl(entityId: string, format: 'markdown' | 'html' | 'owlbear'): string {
  return `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}/export?format=${format}`
}

function entityHasAppearance(entity: EntityExport): boolean {
  const data = entity.data
  if (typeof data !== 'object' || data === null) return false
  return hasNonBlankAppearance((data as Record<string, unknown>)['appearance'])
}

/** The latest image job for this entity (newest first) — status source. */
function portraitJobFor(entityId: string) {
  return (
    jobs
      .forCampaign(campaignId)
      .filter((job) => {
        if (job.kind !== 'image') return false
        const payload = job.payload as { entity_id?: string } | null
        return payload?.entity_id === entityId
      })
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
  )
}

/** The card's portrait status line: queue position while pending, the
 * failure/queue/running message — RENDERED whenever the latest image
 * job is failed, even when an older portrait exists (the DM must see a
 * failed re-generation; acceptance criterion 4). */
function portraitStatus(entity: EntityExport): string | null {
  const job = portraitJobFor(entity.id)
  if (!job) {
    return entityHasAppearance(entity) ? null : 'Add an appearance to generate a portrait.'
  }
  if (job.state === 'queued') return `Portrait queued — position ${job.queue_position ?? '…'}`
  if (job.state === 'running') return 'Generating portrait…'
  if (job.state === 'failed') return `Portrait failed: ${job.error ?? 'unknown error'}`
  return null
}

/** The failed-generation message alone — shown even when a portrait
 * renders (a failed re-generation must not be hidden by the old image). */
function portraitFailure(entity: EntityExport): string | null {
  const job = portraitJobFor(entity.id)
  if (job?.state !== 'failed') return null
  return `Portrait failed: ${job.error ?? 'unknown error'}`
}

async function generatePortrait(entity: EntityExport) {
  if (jobs.portraitInFlight(campaignId, entity.id)) return
  if (!entityHasAppearance(entity)) return // the backend gate, mirrored
  portraitErrors.value[entity.id] = ''
  try {
    await jobs.submitPortrait(campaignId, entity.id)
    await jobs.syncList(campaignId)
  } catch (err) {
    portraitErrors.value[entity.id] =
      err instanceof ApiError ? err.message : 'Could not generate the portrait.'
  }
}

// ---------------------------------------------------------------------------
// Reveal video (spec-4.2, beta): boss-tier cards only — the DM triggers
// the generation from the card, and the clip renders inline. The prompt
// is a backend projection of the committed AR24 record (appearance +
// boss + identity); the button mirrors the enqueue gates (boss-tier
// role + usable prompt: non-blank appearance + boss section) plus the
// in-flight discipline.
// ---------------------------------------------------------------------------

/** The AR24 boss-section keys the reveal prompt joins (the backend's
 * BOSS_PROMPT_KEYS mirror) — one non-blank value makes a usable prompt. */
const BOSS_PROMPT_FIELDS = [
  'lair_actions',
  'legendary_actions',
  'immunities',
  'vulnerabilities',
] as const

const videoErrors = ref<Record<string, string>>({})

/** Spec-4.6: per-entity reveal-prompt textarea errors and local edits.
 * The textarea renders the latest committed draft result unless the DM
 * has hand-edited it (draftPromptTexts wins while non-empty); a fresh
 * draft clears the local edit so the new draft shows. */
const draftPromptErrors = ref<Record<string, string>>({})
const draftPromptTexts = ref<Record<string, string>>({})

/** The textarea's current value for this entity: the DM's local edit
 * when present (hand-written or edited), else the latest succeeded
 * draft, else empty. Never a media row (spec-4.6: a draft is a job
 * result; session-only durability). */
function revealPromptText(entity: EntityExport): string {
  const local = draftPromptTexts.value[entity.id]
  if (local !== undefined) return local
  return jobs.videoPromptFor(campaignId, entity.id) ?? ''
}

/** The latest video_prompt draft job for this entity — status source. */
function draftPromptJobFor(entityId: string) {
  return (
    jobs
      .forCampaign(campaignId)
      .filter((job) => {
        if (job.kind !== 'video_prompt') return false
        const payload = job.payload as { entity_id?: string } | null
        return payload?.entity_id === entityId
      })
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
  )
}

/** The card's draft status line: queue position while pending, the
 * failure message — mirroring the reveal-video status discipline. */
function draftPromptStatus(entity: EntityExport): string | null {
  const job = draftPromptJobFor(entity.id)
  if (!job) return null
  if (job.state === 'queued') return `Reveal prompt queued — position ${job.queue_position ?? '…'}`
  if (job.state === 'running') return 'Drafting reveal prompt…'
  if (job.state === 'failed') return `Reveal prompt failed: ${job.error ?? 'unknown error'}`
  return null
}

/** True while a draft is pending — the draft button's in-flight gate. */
function draftPromptInFlight(entity: EntityExport): boolean {
  return jobs.videoPromptInFlight(campaignId, entity.id)
}

/** The draft failure line alone — mirroring ``videoFailure``. */
function draftPromptFailure(entity: EntityExport): string | null {
  const job = draftPromptJobFor(entity.id)
  if (job?.state !== 'failed') return null
  return `Reveal prompt failed: ${job.error ?? 'unknown error'}`
}

async function draftRevealVideoPrompt(entity: EntityExport) {
  if (!isBossTier(entity)) return // the backend gate, mirrored
  if (draftPromptInFlight(entity)) return
  if (!entityHasAppearance(entity)) return // the backend gate, mirrored
  draftPromptErrors.value[entity.id] = ''
  delete draftPromptTexts.value[entity.id] // a fresh draft replaces the old text
  try {
    await jobs.submitRevealVideoPrompt(campaignId, entity.id)
    await jobs.syncList(campaignId)
  } catch (err) {
    draftPromptErrors.value[entity.id] =
      err instanceof ApiError ? err.message : 'Could not draft the reveal video prompt.'
  }
}

function isBossTier(entity: EntityExport): boolean {
  const data = entity.data as Record<string, unknown> | null | undefined
  return typeof data?.role === 'string' && BOSS_ROLES.has(data.role)
}

/** True iff the committed AR24 record can produce a reveal prompt: a
 * non-blank appearance AND at least one non-blank documented boss value
 * (the backend's ``bbeg_video_prompt`` gate, mirrored). */
function hasVideoPrompt(entity: EntityExport): boolean {
  const data = entity.data as Record<string, unknown> | null | undefined
  if (!data) return false
  const boss = data['boss']
  if (typeof boss !== 'object' || boss === null) return false
  const record = boss as Record<string, unknown>
  return BOSS_PROMPT_FIELDS.some(
    (field) => typeof record[field] === 'string' && String(record[field]).trim() !== '',
  )
}

/** The entity's latest video manifest row (newest created_at), or null. */
function videoFor(entity: EntityExport) {
  return world.videoFor(campaignId, entity.id)
}

/** The same-origin file URL — the session cookie (path /api) authenticates it. */
function videoUrl(entity: EntityExport): string {
  const row = videoFor(entity)
  return row
    ? `/api/campaigns/${encodeURIComponent(campaignId)}/media/${encodeURIComponent(row.entity_id)}/${row.filename}`
    : ''
}

/** The latest video job for this entity (newest first) — status source. */
function videoJobFor(entityId: string) {
  return (
    jobs
      .forCampaign(campaignId)
      .filter((job) => {
        if (job.kind !== 'video') return false
        const payload = job.payload as { entity_id?: string } | null
        return payload?.entity_id === entityId
      })
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
  )
}

/** The card's reveal-video status line: queue position while pending,
 * the failure message — RENDERED whenever the latest video job is
 * failed, even when an older clip exists (the DM must see a failed
 * re-generation). */
function videoStatus(entity: EntityExport): string | null {
  const job = videoJobFor(entity.id)
  if (!job) {
    // With the spec-4.6 draft surface a boss only needs a non-blank
    // appearance to draft; the boss-section requirement applies to the
    // legacy one-shot render only.
    return entityHasAppearance(entity) ? null : 'Add an appearance to draft a reveal video prompt.'
  }
  if (job.state === 'queued') return `Reveal video queued — position ${job.queue_position ?? '…'}`
  if (job.state === 'running') return 'Generating reveal video…'
  if (job.state === 'failed') return `Reveal video failed: ${job.error ?? 'unknown error'}`
  return null
}

/** The failed-generation message alone — shown even when a clip renders
 * (a failed re-generation must not be hidden by the old video). */
function videoFailure(entity: EntityExport): string | null {
  const job = videoJobFor(entity.id)
  if (job?.state !== 'failed') return null
  return `Reveal video failed: ${job.error ?? 'unknown error'}`
}

async function generateRevealVideo(entity: EntityExport) {
  if (!isBossTier(entity)) return // the backend gate, mirrored
  if (jobs.videoInFlight(campaignId, entity.id)) return
  if (!entityHasAppearance(entity)) return // the backend gate, mirrored
  const prompt = revealPromptText(entity).trim()
  if (!prompt && !hasVideoPrompt(entity)) return // the backend gate, mirrored
  videoErrors.value[entity.id] = ''
  try {
    await jobs.submitRevealVideo(campaignId, entity.id, prompt || undefined)
    await jobs.syncList(campaignId)
  } catch (err) {
    videoErrors.value[entity.id] =
      err instanceof ApiError ? err.message : 'Could not generate the reveal video.'
  }
}

// ---------------------------------------------------------------------------
// Destructive actions: entity delete, single-portrait delete, and the
// compensating-commit undo. Each confirms first (globalThis.confirm), and each
// mutation goes through the world store's REST actions — the refetch, never
// local mutation, is what updates the screen, and a failure renders inline
// instead of looking like a success.
// ---------------------------------------------------------------------------

/** Entity-id -> a delete is in flight for that card (one at a time). */
const deletingId = ref<string | null>(null)
/** Entity-id -> entity-deletion errors (the card-level error surface). */
const deleteErrors = ref<Record<string, string>>({})
const undoing = ref(false)
const undoError = ref<string | null>(null)

/**
 * Delete the committed entity. ``base_revision`` is the revision the card
 * rendered: a head that moved since is a 409 and nothing leaves.
 *
 * AD-5: the destructive cascade is opt-in and the DM must see the affected
 * neighbors first — the card's own relation lines are that listing, so the
 * confirmation names them and the cascade rides only when there are any.
 */
async function removeEntity(entity: EntityExport) {
  if (deletingId.value) return
  const relations = relationsFor(entity.id)
  const cascade = relations.length > 0
  const neighbors = [
    ...new Set(
      relations.map((relation) => (relation.outbound ? relation.dstName : relation.srcName)),
    ),
  ]
  const affected = relations.length
    ? ` This also removes ${relations.length} relation${relations.length === 1 ? '' : 's'}: ${neighbors.join(', ')}.`
    : ''
  const confirmed = globalThis.confirm(`Delete ${entity.name}?${affected}`)
  if (!confirmed) return
  deletingId.value = entity.id
  deleteErrors.value[entity.id] = ''
  try {
    await world.deleteEntity(campaignId, entity.id, revision.value?.id, cascade)
  } catch (err) {
    deleteErrors.value[entity.id] =
      err instanceof ApiError ? err.message : 'Could not delete the entity.'
    // A 409 means this card is behind the world (moved head, or relations
    // the snapshot does not show) — resync so the next click is not a dead
    // end, and so its confirmation names the real neighbors.
    if (err instanceof ApiError && err.status === 409) {
      void world.requestRefetch(campaignId)
    }
  } finally {
    deletingId.value = null
  }
}

/**
 * Delete the single-portrait row the card renders (the entity's newest
 * image-kind row). There is NO undo for this: the file is gone and media
 * rows are not world graph — regeneration is the recovery, so the card
 * falls back to 'No portrait.'.
 */
async function removePortrait(entity: EntityExport) {
  if (deletingId.value) return
  const row = portraitFor(entity)
  if (!row) return
  const confirmed = globalThis.confirm(
    `Delete the portrait for ${entity.name}? The image file is permanently deleted — generate a new portrait to replace it.`,
  )
  if (!confirmed) return
  deletingId.value = entity.id
  portraitErrors.value[entity.id] = ''
  try {
    await world.deleteMedia(campaignId, entity.id, row.id)
  } catch (err) {
    portraitErrors.value[entity.id] =
      err instanceof ApiError ? err.message : 'Could not delete the portrait.'
  } finally {
    deletingId.value = null
  }
}

/**
 * Undo the revision the view is showing — one compensating commit, so the
 * world returns to its previous state and a NEW revision becomes the head.
 * A 409 means the world committed past the drawn revision; the mapper
 * carries no head id on the wire, so the snapshot refetches to catch the
 * view up and the DM can act on what is actually there.
 */
async function undoCommit() {
  const target = revision.value
  if (undoing.value || !target) return
  const confirmed = globalThis.confirm(
    `Undo the last commit (revision ${target.id})? The world returns to its previous state as a new compensating revision.`,
  )
  if (!confirmed) return
  undoing.value = true
  undoError.value = null
  try {
    await world.undoLastCommit(campaignId, target.id)
  } catch (err) {
    undoError.value = err instanceof ApiError ? err.message : 'Could not undo the last commit.'
    if (err instanceof ApiError && err.status === 409) {
      void world.requestRefetch(campaignId)
    }
  } finally {
    undoing.value = false
  }
}

function relationTargets(entityId: string): EntityExport[] {
  // Any existing entity except the card's own — a self-loop is not an
  // edge into existing world state (the store rejects it outright).
  return entities.value.filter((entity) => entity.id !== entityId)
}

function startAdd(entityId: string) {
  relationErrors.value[entityId] = '' // errors stay keyed per card
  addingFor.value = entityId
  addType.value = EDGE_VOCAB[0]
  addTargetId.value = relationTargets(entityId)[0]?.id ?? ''
  addCounter.value = 1
  addDirection.value = 'outbound'
}

function cancelAdd() {
  addingFor.value = null
}

async function submitAdd(entityId: string) {
  if (!addTargetId.value || relationBusy.value) return
  relationBusy.value = true
  relationErrors.value[entityId] = ''
  try {
    // Outbound wires entity -> target; inbound wires target -> entity.
    const [src, dst] =
      addDirection.value === 'outbound'
        ? [entityId, addTargetId.value]
        : [addTargetId.value, entityId]
    await world.addEdge(campaignId, { src, dst, type: addType.value, counter: addCounter.value })
    addingFor.value = null
  } catch (err) {
    relationErrors.value[entityId] =
      err instanceof ApiError ? err.message : 'Could not add the relation.'
  } finally {
    relationBusy.value = false
  }
}

function startEdit(relation: RelationLine) {
  relationErrors.value = {}
  editingEdgeId.value = relation.edgeId
  editCounter.value = relation.counter
}

function cancelEdit() {
  editingEdgeId.value = null
}

async function saveCounter(entityId: string, relation: RelationLine) {
  if (relationBusy.value) return
  relationBusy.value = true
  relationErrors.value[entityId] = ''
  try {
    await world.updateEdgeCounter(campaignId, relation.edgeId, editCounter.value)
    editingEdgeId.value = null
  } catch (err) {
    relationErrors.value[entityId] =
      err instanceof ApiError ? err.message : 'Could not update the relation.'
  } finally {
    relationBusy.value = false
  }
}

async function removeEdge(entityId: string, relation: RelationLine) {
  if (relationBusy.value) return
  relationBusy.value = true
  relationErrors.value[entityId] = ''
  try {
    await world.deleteEdge(campaignId, relation.edgeId)
  } catch (err) {
    relationErrors.value[entityId] =
      err instanceof ApiError ? err.message : 'Could not delete the relation.'
  } finally {
    relationBusy.value = false
  }
}

// ---------------------------------------------------------------------------
// Profile hand editing (spec-3.6, FR10): inline per-field editing of a
// committed entity's AR24 record + text, saved as ONE committed revision
// through the world store's updateEntity PATCH. Drafts seed from RAW
// committed values (numbers/objects stringify — a present value never
// renders blank). Structured blocks (stat_block / world_integration /
// boss) edit as pretty JSON. Bare records (factions, places, build-in
// shells) stay editable: only changed fields are sent, and the backend's
// conditional validation never forces a shape they lack.
// ---------------------------------------------------------------------------

const IDENTITY_FIELDS = ['level_cr', 'race_type', 'class_profession', 'alignment'] as const
const LORE_FIELDS = [
  'appearance',
  'background',
  'goals',
  'relationships',
  'voice_style',
  'catchphrases',
] as const
const BOSS_ROLES = new Set(['BBEG', 'Monster'])

const FIELD_LABELS: Record<string, string> = {
  text: 'Text',
  name: 'Name',
  role: 'Role',
  level_cr: 'Level/CR',
  race_type: 'Race/Type',
  class_profession: 'Class / Profession',
  alignment: 'Alignment',
  personality: 'Personality',
  secret: 'Secret',
  rumor: 'Rumor',
  party_hook: 'Party hook',
  appearance: 'Appearance',
  background: 'Background',
  goals: 'Goals',
  relationships: 'Relationships',
  voice_style: 'Voice style',
  catchphrases: 'Catchphrases',
  reputation: 'Reputation',
  factions: 'Factions',
  current_location: 'Current location',
  reaction_matrix: 'Reaction matrix',
  on_defeat: 'On defeat',
  stat_block: 'Stat block',
  world_integration: 'World integration',
  boss: 'Boss',
}

/** World-integration subfields in contract order (spec-3.3). */
const WORLD_INTEGRATION_FIELDS = [
  'reputation',
  'factions',
  'current_location',
  'reaction_matrix',
  'on_defeat',
] as const

/** Editable scalar string fields (identity anchor + narrative lore). */
const CORE_FIELDS = ['personality', 'secret', 'rumor', 'party_hook'] as const

/** Editable scalar string fields (AR19 core + identity anchor + narrative lore). */
const SCALAR_FIELDS = ['name', 'role', ...CORE_FIELDS, ...IDENTITY_FIELDS, ...LORE_FIELDS] as const

/** Structured blocks edited as pretty JSON. */
const JSON_FIELDS = ['stat_block', 'world_integration', 'boss'] as const

const editingProfileId = ref<string | null>(null)
const profileDrafts = ref<Record<string, Record<string, string>>>({})
const profileInitials = ref<Record<string, Record<string, string>>>({})
const profileBusy = ref(false)
const profileErrors = ref<Record<string, string>>({})
const profileConflict = ref<Record<string, string>>({})
const profileReloading = ref<Record<string, boolean>>({})
/** entityId -> the revision id the editor was OPENED against (the
 * optimistic-concurrency base). Captured at seed time, never re-read:
 * a WS refetch landing mid-edit moves the live snapshot's head, and
 * sending THAT would defeat the stale-base 409 — the seed-time drafts
 * would merge silently over the moved head (spec-3-6 Never list). */
const profileBases = ref<Record<string, string | null>>({})

function rawString(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function startProfileEdit(entity: EntityExport) {
  const data = (entity.data ?? {}) as Record<string, unknown>
  const drafts: Record<string, string> = {}
  for (const field of SCALAR_FIELDS) drafts[field] = rawString(data[field])
  drafts['text'] = entity.text ?? ''
  for (const field of JSON_FIELDS) drafts[field] = rawString(data[field])
  // Unknown keys edit as one "additional data" JSON object (spec-2.7
  // deferral resolution): keys added/changed land in the PATCH; keys
  // removed send null (delete).
  drafts['__extras'] = rawString(extrasObject(entity))
  editingProfileId.value = entity.id
  profileDrafts.value[entity.id] = drafts
  profileInitials.value[entity.id] = { ...drafts }
  profileBases.value[entity.id] = revision.value?.id ?? null
  profileErrors.value[entity.id] = ''
  profileConflict.value[entity.id] = ''
}

function cancelProfileEdit() {
  const id = editingProfileId.value
  editingProfileId.value = null
  if (id) {
    delete profileDrafts.value[id]
    delete profileInitials.value[id]
    delete profileBases.value[id]
    delete profileErrors.value[id]
    delete profileConflict.value[id]
  }
}

function isProfileEdited(entityId: string): boolean {
  const drafts = profileDrafts.value[entityId]
  const initials = profileInitials.value[entityId]
  if (!drafts || !initials) return false
  return Object.keys(drafts).some((field) => drafts[field] !== initials[field])
}

async function saveProfile(entity: EntityExport) {
  if (profileBusy.value) return
  profileErrors.value[entity.id] = ''
  profileConflict.value[entity.id] = ''
  const drafts = profileDrafts.value[entity.id]
  const initials = profileInitials.value[entity.id]
  if (!drafts || !initials) return
  const patch: Record<string, unknown> = {}
  for (const field of SCALAR_FIELDS) {
    if (drafts[field] !== initials[field]) patch[field] = drafts[field]
  }
  for (const field of JSON_FIELDS) {
    if (drafts[field] === initials[field]) continue
    const trimmed = drafts[field].trim()
    if (trimmed === '') {
      patch[field] = null // explicit delete (EDIT_ROLE_UNBOSS / removing a block)
      continue
    }
    try {
      patch[field] = JSON.parse(trimmed)
    } catch {
      profileErrors.value[entity.id] = `${FIELD_LABELS[field] ?? field} is not valid JSON.`
      return
    }
  }
  if (drafts['__extras'] !== initials['__extras']) {
    try {
      const extras = JSON.parse(drafts['__extras']) as unknown
      const initialExtras = JSON.parse(initials['__extras'] ?? '{}') as Record<string, unknown>
      // The box edits ONE object of unknown keys: a non-object parse
      // (`5`, `"s"`, a list) would either vanish into an empty patch
      // (Save closing with zero feedback) or land numeric-string keys
      // in the entity's data — reject it like the JSON_FIELDS branch.
      if (extras === null || typeof extras !== 'object' || Array.isArray(extras)) {
        profileErrors.value[entity.id] = 'Additional data must be a JSON object.'
        return
      }
      for (const key of Object.keys(extras)) patch[key] = (extras as Record<string, unknown>)[key]
      for (const key of Object.keys(initialExtras)) {
        if (!(key in (extras as Record<string, unknown>))) patch[key] = null // removed -> delete the data key
      }
    } catch {
      profileErrors.value[entity.id] = 'Additional data is not valid JSON.'
      return
    }
  }
  if (drafts['text'] !== initials['text']) {
    // Clear = null (the str|null wire contract): an empty string would
    // mint a revision changing None->'' while rendering identically.
    patch['text'] = drafts['text'].trim() === '' ? null : drafts['text']
  }
  // Role flip away from BBEG/Monster with a boss block present: strip it in
  // the same PATCH (the spec EDIT_ROLE_UNBOSS row — a boss section would
  // otherwise 422 the merge).
  if ('role' in patch && typeof patch.role === 'string' && !BOSS_ROLES.has(patch.role)) {
    const data = (entity.data ?? {}) as Record<string, unknown>
    if ('boss' in data && !('boss' in patch)) patch['boss'] = null
  }
  if (Object.keys(patch).length === 0) {
    cancelProfileEdit()
    return
  }
  profileBusy.value = true
  try {
    await world.updateEntity(campaignId, entity.id, patch, profileBases.value[entity.id])
    cancelProfileEdit()
  } catch (err) {
    const message = err instanceof ApiError ? err.message : 'Could not save the edit.'
    if (err instanceof ApiError && err.status === 409) {
      profileConflict.value[entity.id] = message
    } else {
      profileErrors.value[entity.id] = message
    }
  } finally {
    profileBusy.value = false
  }
}

/** Reload (rebase) after a 409: await the fresh snapshot, THEN close — a
 * fast re-edit must not send the stale base_revision again. */
async function rebaseAndClose(entityId: string) {
  profileReloading.value[entityId] = true
  try {
    await world.fetchSnapshot(campaignId)
    // fetchSnapshot coalesces: when another fetch is in flight the call
    // returns before any data lands. Wait for quiescence so the error
    // read below is OUR rebase's settled outcome, not a previous
    // fetch's — otherwise a stale null closes (discarding the draft)
    // through a fetch that then fails.
    await world.waitUntilQuiet(campaignId)
    // fetchSnapshot never throws — it records failures in entry.error.
    // Close only on a SUCCESSFUL rebase: on failure the DM's draft and
    // the conflict context survive for a retry (never a silent wipe).
    if (world.entry(campaignId).error !== null) return
    cancelProfileEdit()
  } finally {
    profileReloading.value[entityId] = false
  }
}

function dataKeys(entity: EntityExport): string[] {
  // Truly additional keys only: scalar profile fields (name, role,
  // personality, …) live in data by contract (generate parity) but
  // already render as profile rows — re-dumping them here duplicated
  // the whole profile as JSON (dogfood 2026-09-09). Structured blocks
  // render as their own sections below.
  const data = (entity.data ?? {}) as Record<string, unknown>
  const RESERVED = new Set(['kind', 'edges', 'text', 'base_revision'])
  return Object.keys(data).filter(
    (key) =>
      !(JSON_FIELDS as readonly string[]).includes(key) &&
      !(SCALAR_FIELDS as readonly string[]).includes(key) &&
      !RESERVED.has(key),
  )
}

function profileScalarFields(entity: EntityExport): string[] {
  const data = (entity.data ?? {}) as Record<string, unknown>
  return SCALAR_FIELDS.filter((field) => data[field] !== undefined && data[field] !== null)
}

function jsonBlockPresent(entity: EntityExport, field: string): boolean {
  const data = (entity.data ?? {}) as Record<string, unknown>
  return data[field] !== undefined && data[field] !== null
}
function profileFieldValue(entity: EntityExport, field: string): string {
  const data = (entity.data ?? {}) as Record<string, unknown>
  const value = data[field]
  return typeof value === 'string' ? value : rawString(value)
}

/** Present world-integration entries as [label, display] rows — the
 * block renders like the profile, not as raw JSON (dogfood 2026-09-09).
 * Unknown subkeys survive with their raw key (forward-compat). */
function worldIntegrationEntries(entity: EntityExport): Array<[string, string]> {
  const data = (entity.data ?? {}) as Record<string, unknown>
  const block = data['world_integration']
  if (typeof block !== 'object' || block === null || Array.isArray(block)) return []
  const record = block as Record<string, unknown>
  const rows: Array<[string, string]> = []
  for (const field of WORLD_INTEGRATION_FIELDS) {
    const value = record[field]
    if (typeof value === 'string' && value.trim() !== '') {
      rows.push([FIELD_LABELS[field] ?? field, value])
    }
  }
  for (const key of Object.keys(record)) {
    if ((WORLD_INTEGRATION_FIELDS as readonly string[]).includes(key)) continue
    const value = record[key]
    if (value !== undefined && value !== null) rows.push([key, rawString(value)])
  }
  return rows
}

function extrasObject(entity: EntityExport): Record<string, unknown> {
  const data = (entity.data ?? {}) as Record<string, unknown>
  const extras: Record<string, unknown> = {}
  for (const key of dataKeys(entity)) extras[key] = data[key]
  return extras
}

function additionalDataBlock(entity: EntityExport): string {
  return JSON.stringify(extrasObject(entity), null, 2)
}
</script>

<template>
  <section>
    <h1>{{ exportData?.campaign.title ?? 'World' }}</h1>

    <div v-if="entry.notFound" class="card">
      <p class="error">World not found.</p>
      <p class="muted">This campaign does not exist or belongs to another DM.</p>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>
    <p v-else-if="entry.loading && !exportData" class="muted">Loading the world…</p>
    <div v-else-if="entry.error && !exportData" class="card">
      <p class="error">{{ entry.error }}</p>
      <button type="button" @click="start">Retry</button>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>
    <template v-else-if="exportData">
      <p v-if="entry.error" class="error sync-failed">
        Live sync failed — showing the last synced world. ({{ entry.error }})
        <button type="button" @click="world.requestRefetch(campaignId)">Retry</button>
      </p>
      <div class="card">
        <p class="muted">
          Theme: <strong>{{ exportData.campaign.theme }}</strong>
        </p>
        <p v-if="exportData.campaign.description" class="muted">
          {{ exportData.campaign.description }}
        </p>
        <p v-if="exportData.campaign.custom_lore" class="lore">
          {{ exportData.campaign.custom_lore }}
        </p>
        <p v-if="revision" class="muted small mono">Revision {{ revision.id }}</p>
        <p class="muted small">Relations are editable — the world updates live as jobs commit.</p>
        <p>
          <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }" class="cta secondary">
            Open build-in
          </RouterLink>
          <RouterLink
            :to="{ name: 'candidates', params: { id: campaignId } }"
            class="cta secondary"
          >
            Open candidates
          </RouterLink>
        </p>
        <p class="export-actions">
          <a class="link" :href="worldExportUrl('markdown')" download>Export Markdown</a>
          <a class="link" :href="worldExportUrl('html')" download>Export HTML</a>
          <!-- Undo is a world-level mutation, not a per-entity one: one
               compensating commit reverts the last revision. Rendered only
               when a revision exists — with an empty log there is nothing
               to undo (the route's "No revision to undo."). -->
          <button
            v-if="revision"
            type="button"
            class="link"
            :disabled="undoing"
            @click="undoCommit"
          >
            {{ undoing ? 'Undoing…' : 'Undo last commit' }}
          </button>
        </p>
        <p v-if="undoError" class="error">{{ undoError }}</p>
      </div>

      <div v-if="entities.length === 0" class="card">
        <p class="muted">This world is still empty — nothing has been built yet.</p>
        <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }" class="cta">
          Open build-in
        </RouterLink>
      </div>

      <template v-else>
        <section v-for="[kind, group] in kinds" :key="kind" class="kind-group">
          <h2>
            {{ kind }} <span class="muted small">({{ group.length }})</span>
          </h2>
          <article v-for="entity in group" :key="entity.id" class="card entity">
            <h3>
              {{ entity.name }}
              <button
                v-if="isRegenerable(entity)"
                type="button"
                class="link"
                :disabled="regeneratingId !== null"
                @click="regenerateEntity(entity.id)"
              >
                {{ regeneratingId === entity.id ? 'Regenerating…' : 'Regenerate' }}
              </button>
              <select
                v-if="isRegenerable(entity)"
                v-model="regenScope[entity.id]"
                :disabled="regeneratingId !== null"
                aria-label="Regenerate scope"
                title="Whole character or one section"
              >
                <option value="">Whole character</option>
                <option v-for="section in REGEN_SECTIONS" :key="section" :value="section">
                  {{ FIELD_LABELS[section] ?? section }}
                </option>
              </select>
              <button
                v-if="editingProfileId !== entity.id"
                type="button"
                class="link"
                :disabled="profileBusy"
                @click="startProfileEdit(entity)"
              >
                Edit profile
              </button>
              <a class="link" :href="entityExportUrl(entity.id, 'markdown')" download> Markdown </a>
              <a class="link" :href="entityExportUrl(entity.id, 'html')" download> Sheet (HTML) </a>
              <a class="link" :href="entityExportUrl(entity.id, 'owlbear')" download>
                Owlbear (Forge)
              </a>
              <span class="muted small">Forge: file → Import paste · link → portrait override</span>
              <!-- Destructive, so last in the row and labelled to disambiguate
                   it from the relation/portrait deletes below. The DELETE
                   cascades over the entity's relations in one revision. -->
              <button
                type="button"
                class="link"
                :disabled="deletingId !== null"
                @click="removeEntity(entity)"
              >
                {{ deletingId === entity.id ? 'Deleting…' : 'Delete entity' }}
              </button>
            </h3>
            <div class="portrait">
              <img
                v-if="portraitFor(entity)"
                :src="portraitUrl(entity)"
                :alt="`${entity.name} portrait`"
                class="portrait-img"
              />
              <p v-else-if="world.mediaFetchFailed(campaignId)" class="muted">
                Portrait list unavailable.
              </p>
              <p v-else class="muted">No portrait.</p>
              <p class="portrait-actions">
                <button
                  type="button"
                  class="link"
                  :disabled="
                    jobs.portraitInFlight(campaignId, entity.id) || !entityHasAppearance(entity)
                  "
                  @click="generatePortrait(entity)"
                >
                  {{
                    jobs.portraitInFlight(campaignId, entity.id)
                      ? portraitJobFor(entity.id)?.state === 'running'
                        ? 'Generating portrait…'
                        : 'Portrait queued…'
                      : 'Generate portrait'
                  }}
                </button>
                <button
                  type="button"
                  class="link"
                  :disabled="portraitLinkBusy === entity.id"
                  @click="fetchPortraitLink(entity.id)"
                >
                  {{
                    portraitLinkBusy === entity.id
                      ? 'Getting portrait link…'
                      : portraitLinks[entity.id]
                        ? 'Copy portrait link'
                        : 'Get portrait link'
                  }}
                </button>
                <!-- Single-portrait delete: only the entity's newest
                     image-kind manifest row (a video row is never the
                     portrait). No undo — the file is gone; regenerate
                     to replace it. -->
                <button
                  v-if="portraitFor(entity)"
                  type="button"
                  class="link"
                  :disabled="deletingId !== null"
                  @click="removePortrait(entity)"
                >
                  {{ deletingId === entity.id ? 'Deleting…' : 'Delete portrait' }}
                </button>
              </p>
              <!-- The failed-generation message renders REGARDLESS of an
                   existing portrait (a failed re-generation must not hide
                   behind the old image); queue/running progress renders
                   only while no portrait exists yet. -->
              <p v-if="portraitFailure(entity)" class="error">
                {{ portraitFailure(entity) }}
              </p>
              <p
                v-else-if="portraitStatus(entity) && !portraitFor(entity)"
                class="muted small status"
              >
                {{ portraitStatus(entity) }}
              </p>
              <p v-if="portraitErrors[entity.id]" class="error">
                {{ portraitErrors[entity.id] }}
              </p>
              <input
                v-if="portraitLinks[entity.id]"
                :value="portraitLinks[entity.id]"
                readonly
                :aria-label="`${entity.name} portrait link`"
                class="portrait-link"
                @focus="($event.target as HTMLInputElement).select()"
              />
              <p v-if="portraitLinkErrors[entity.id]" class="error">
                {{ portraitLinkErrors[entity.id] }}
              </p>
            </div>
            <!-- Reveal video (spec-4.2/4.6, beta): boss-tier cards only. The
                 clip renders inline; a failed re-generation renders over
                 an existing clip; queue/running progress renders only
                 while no clip exists yet. Spec-4.6 adds the two-phase
                 surface: 'Draft reveal prompt' authors a MiniMax-I2VA
                 draft from the committed character + writing guide, the
                 textarea lets the DM review/edit (or hand-write), and
                 'Render reveal video' sends the approved prompt. -->
            <div v-if="isBossTier(entity)" class="reveal-video">
              <video
                v-if="videoFor(entity)"
                :src="videoUrl(entity)"
                controls
                preload="metadata"
                :aria-label="`${entity.name} reveal video`"
                class="reveal-video-clip"
              ></video>
              <p v-else-if="world.mediaFetchFailed(campaignId)" class="muted">
                Media list unavailable.
              </p>
              <p class="reveal-video-actions">
                <button
                  v-if="entityHasAppearance(entity)"
                  type="button"
                  class="link"
                  :disabled="draftPromptInFlight(entity)"
                  @click="draftRevealVideoPrompt(entity)"
                >
                  {{
                    draftPromptInFlight(entity)
                      ? draftPromptJobFor(entity.id)?.state === 'running'
                        ? 'Drafting reveal prompt…'
                        : 'Reveal prompt queued…'
                      : 'Draft reveal prompt'
                  }}
                </button>
                <textarea
                  v-if="entityHasAppearance(entity)"
                  :value="revealPromptText(entity)"
                  :aria-label="`${entity.name} reveal video prompt (optional)`"
                  class="reveal-prompt-textarea"
                  @input="
                    draftPromptTexts[entity.id] = ($event.target as HTMLTextAreaElement).value
                  "
                ></textarea>
                <button
                  type="button"
                  class="link"
                  :disabled="
                    jobs.videoInFlight(campaignId, entity.id) ||
                    !entityHasAppearance(entity) ||
                    (!revealPromptText(entity).trim() && !hasVideoPrompt(entity))
                  "
                  @click="generateRevealVideo(entity)"
                >
                  {{
                    jobs.videoInFlight(campaignId, entity.id)
                      ? videoJobFor(entity.id)?.state === 'running'
                        ? 'Generating reveal video…'
                        : 'Reveal video queued…'
                      : 'Render reveal video'
                  }}
                </button>
              </p>
              <p v-if="draftPromptFailure(entity)" class="error">
                {{ draftPromptFailure(entity) }}
              </p>
              <p v-else-if="draftPromptStatus(entity)" class="muted small status">
                {{ draftPromptStatus(entity) }}
              </p>
              <p v-if="draftPromptErrors[entity.id]" class="error">
                {{ draftPromptErrors[entity.id] }}
              </p>
              <p v-if="videoFailure(entity)" class="error">
                {{ videoFailure(entity) }}
              </p>
              <p v-else-if="videoStatus(entity) && !videoFor(entity)" class="muted small status">
                {{ videoStatus(entity) }}
              </p>
              <p v-if="videoErrors[entity.id]" class="error">
                {{ videoErrors[entity.id] }}
              </p>
            </div>
            <p v-if="entity.text" class="text">{{ entity.text }}</p>
            <p v-else class="muted">No description.</p>
            <p v-if="regenerateErrors[entity.id]" class="error">
              {{ regenerateErrors[entity.id] }}
            </p>
            <p v-else-if="regenerateNotices[entity.id]" class="muted small status">
              <template v-if="regenerateNotices[entity.id].kind === 'ready'">
                Re-roll of {{ regenerateNotices[entity.id].label }} is ready —
                <RouterLink :to="{ name: 'candidates', params: { id: campaignId } }">
                  review and accept it
                </RouterLink>
                (this world changes only when you accept).
              </template>
              <template v-else-if="regenerateNotices[entity.id].kind === 'failed'">
                Re-roll of {{ regenerateNotices[entity.id].label }} failed:
                {{ regenerateNotices[entity.id].error }}
              </template>
              <template v-else>
                Re-rolling {{ regenerateNotices[entity.id].label }}…
              </template>
            </p>
            <p v-if="deleteErrors[entity.id]" class="error">{{ deleteErrors[entity.id] }}</p>
            <div
              v-if="
                editingProfileId !== entity.id &&
                (profileScalarFields(entity).length > 0 || dataKeys(entity).length > 0)
              "
              class="profile"
            >
              <h4>Profile</h4>
              <dl>
                <template v-for="field in profileScalarFields(entity)" :key="field">
                  <dt>{{ FIELD_LABELS[field] ?? field }}</dt>
                  <dd>{{ profileFieldValue(entity, field) }}</dd>
                </template>
              </dl>
              <div v-if="jsonBlockPresent(entity, 'world_integration')" class="profile-block">
                <h4>World integration</h4>
                <dl>
                  <template
                    v-for="([label, value], index) in worldIntegrationEntries(entity)"
                    :key="index"
                  >
                    <dt>{{ label }}</dt>
                    <dd>{{ value }}</dd>
                  </template>
                </dl>
              </div>
              <div v-if="jsonBlockPresent(entity, 'boss')" class="profile-block">
                <h4>Boss</h4>
                <pre>{{ profileFieldValue(entity, 'boss') }}</pre>
              </div>
              <div v-if="dataKeys(entity).length > 0" class="profile-block additional">
                <h4>Additional data</h4>
                <pre>{{ additionalDataBlock(entity) }}</pre>
              </div>
            </div>
            <div v-else-if="editingProfileId === entity.id" class="profile-editor">
              <h4>Edit profile</h4>
              <label v-for="field in SCALAR_FIELDS" :key="field" class="field">
                <span>{{ FIELD_LABELS[field] ?? field }}</span>
                <select
                  v-if="field === 'role'"
                  v-model="profileDrafts[entity.id].role"
                  aria-label="Role"
                >
                  <option value="">—</option>
                  <option v-for="role in ['NPC', 'BBEG', 'Monster']" :key="role" :value="role">
                    {{ role }}
                  </option>
                </select>
                <textarea
                  v-else
                  v-model="profileDrafts[entity.id][field]"
                  :aria-label="FIELD_LABELS[field] ?? field"
                ></textarea>
              </label>
              <label class="field">
                <span>Text</span>
                <textarea v-model="profileDrafts[entity.id].text" aria-label="Text"></textarea>
              </label>
              <label v-for="field in JSON_FIELDS" :key="field" class="field">
                <span>{{ FIELD_LABELS[field] ?? field }} (JSON)</span>
                <textarea
                  v-model="profileDrafts[entity.id][field]"
                  class="json"
                  :aria-label="`${field} (JSON)`"
                ></textarea>
              </label>
              <label class="field">
                <span>Additional data (JSON)</span>
                <textarea
                  v-model="profileDrafts[entity.id].__extras"
                  class="json"
                  aria-label="Additional data (JSON)"
                ></textarea>
              </label>
              <p class="actions">
                <button
                  type="button"
                  :disabled="
                    profileBusy || profileReloading[entity.id] || !isProfileEdited(entity.id)
                  "
                  @click="saveProfile(entity)"
                >
                  {{ profileBusy ? 'Saving…' : 'Save' }}
                </button>
                <button
                  type="button"
                  :disabled="profileBusy || profileReloading[entity.id]"
                  @click="cancelProfileEdit"
                >
                  Cancel
                </button>
              </p>
              <p v-if="profileErrors[entity.id]" class="error">{{ profileErrors[entity.id] }}</p>
              <p v-if="profileConflict[entity.id]" class="error conflict">
                {{ profileConflict[entity.id] }}
                <button
                  type="button"
                  :disabled="profileReloading[entity.id]"
                  @click="rebaseAndClose(entity.id)"
                >
                  {{ profileReloading[entity.id] ? 'Reloading…' : 'Reload' }}
                </button>
                <button
                  type="button"
                  :disabled="profileReloading[entity.id]"
                  @click="cancelProfileEdit"
                >
                  Discard
                </button>
              </p>
            </div>
            <StatBlock
              v-if="entity.data && entity.data['stat_block']"
              :block="entity.data['stat_block']"
            />
            <div class="relations">
              <h4>Relations</h4>
              <p v-if="relationsFor(entity.id).length === 0" class="muted">No relations yet.</p>
              <p v-for="relation in relationsFor(entity.id)" :key="relation.edgeId" class="mono">
                <template v-if="editingEdgeId === relation.edgeId">
                  <input
                    v-model.number="editCounter"
                    class="counter-input"
                    type="number"
                    aria-label="Counter"
                  />
                  <button
                    type="button"
                    :disabled="relationBusy"
                    @click="saveCounter(entity.id, relation)"
                  >
                    Save
                  </button>
                  <button type="button" :disabled="relationBusy" @click="cancelEdit">Cancel</button>
                </template>
                <template v-else>
                  {{ relation.srcName }} --{{ relation.label }}--&gt; {{ relation.dstName }}
                  <button
                    v-if="COUNTER_TYPES.has(relation.type)"
                    type="button"
                    class="link"
                    :disabled="relationBusy"
                    @click="startEdit(relation)"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    class="link"
                    :disabled="relationBusy"
                    @click="removeEdge(entity.id, relation)"
                  >
                    Delete
                  </button>
                </template>
              </p>
              <form
                v-if="addingFor === entity.id"
                class="add-relation"
                @submit.prevent="submitAdd(entity.id)"
              >
                <select v-model="addDirection" aria-label="Direction">
                  <option v-for="direction in EDGE_DIRECTIONS" :key="direction" :value="direction">
                    {{ direction }}
                  </option>
                </select>
                <select v-model="addType" aria-label="Relation type">
                  <option v-for="edgeType in EDGE_VOCAB" :key="edgeType" :value="edgeType">
                    {{ edgeType }}
                  </option>
                </select>
                <select v-model="addTargetId" aria-label="Target entity">
                  <option
                    v-for="target in relationTargets(entity.id)"
                    :key="target.id"
                    :value="target.id"
                  >
                    {{ target.name }}
                  </option>
                </select>
                <input
                  v-model.number="addCounter"
                  class="counter-input"
                  type="number"
                  aria-label="Counter"
                />
                <button type="submit" :disabled="relationBusy || !addTargetId">Add</button>
                <button type="button" :disabled="relationBusy" @click="cancelAdd">Cancel</button>
              </form>
              <button
                v-else
                type="button"
                class="link"
                :disabled="relationBusy"
                @click="startAdd(entity.id)"
              >
                Add relation
              </button>
              <p v-if="relationErrors[entity.id]" class="error">{{ relationErrors[entity.id] }}</p>
            </div>
          </article>
        </section>
      </template>
    </template>
    <p v-else class="muted">Loading the world…</p>
  </section>
</template>

<style scoped>
.kind-group h2 {
  margin-top: 1.5rem;
  text-transform: capitalize;
}
.entity h3 {
  margin: 0.25rem 0;
}
.text {
  white-space: pre-wrap;
}
.portrait {
  margin: 0.5rem 0;
}
.portrait-img {
  max-width: 12rem;
  max-height: 12rem;
  border-radius: 0.5rem;
  display: block;
}
.portrait p {
  margin: 0.15rem 0;
}
.portrait-actions {
  margin-top: 0.25rem;
}
.portrait-actions .link:disabled {
  color: #484f58;
  cursor: default;
}
.portrait-link {
  display: block;
  width: 100%;
  margin-top: 0.25rem;
  font-size: 0.8rem;
  font-family: monospace;
}
.portrait .status {
  font-size: 0.8rem;
}
.reveal-video {
  margin: 0.5rem 0;
}
.reveal-video-clip {
  display: block;
  max-width: 16rem;
  width: 100%;
  border-radius: 0.5rem;
  background: #000;
}
.reveal-video p {
  margin: 0.15rem 0;
}
.reveal-video-actions {
  margin-top: 0.25rem;
}
.reveal-video-actions .link:disabled {
  color: #484f58;
  cursor: default;
}
.reveal-prompt-textarea {
  display: block;
  width: 100%;
  min-height: 4.5rem;
  font-size: 0.8rem;
  margin-top: 0.25rem;
}
.reveal-prompt-textarea:disabled {
  background: #1f2328;
  color: #9aa0a6;
}
.relations h4 {
  margin: 0.5rem 0 0.25rem;
  font-size: 0.85rem;
  color: #9aa0a6;
}
.relations p {
  margin: 0.1rem 0;
  font-size: 0.85rem;
}
.link {
  margin-left: 0.4rem;
  padding: 0;
  border: none;
  background: none;
  color: #58a6ff;
  cursor: pointer;
  font-size: 0.85rem;
  text-decoration: none;
}
.link:disabled {
  color: #484f58;
  cursor: default;
}
.counter-input {
  width: 5rem;
}
.add-relation {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 0.4rem 0;
}
.add-relation select,
.add-relation input {
  font-size: 0.85rem;
}
.relations .error {
  font-size: 0.85rem;
}

.profile h4,
.profile-editor h4 {
  margin: 0.75rem 0 0.25rem;
  font-size: 0.85rem;
  color: #9aa0a6;
}
.profile dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.15rem 0.75rem;
  font-size: 0.85rem;
}
.profile dt {
  color: #9aa0a6;
  font-weight: 600;
}
.profile dd {
  margin: 0;
  white-space: pre-wrap;
}
.profile pre {
  margin: 0.25rem 0 0;
  font-size: 0.8rem;
  background: #14171c;
  padding: 0.5rem 0.6rem;
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre-wrap;
}
.profile-editor .field {
  display: block;
  margin: 0.4rem 0;
}
.profile-editor .field span {
  display: block;
  font-size: 0.8rem;
  color: #9aa0a6;
  margin-bottom: 0.15rem;
}
.profile-editor textarea {
  width: 100%;
  min-height: 3.2rem;
  font-size: 0.85rem;
  resize: vertical;
}
.profile-editor textarea.json {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.profile-editor select {
  font-size: 0.85rem;
}
.profile-editor .actions {
  display: flex;
  gap: 0.5rem;
  margin: 0.6rem 0 0.3rem;
}
.conflict {
  border: 1px solid #2c3038;
  border-left: 3px solid #d29922;
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
  display: grid;
  gap: 0.4rem;
}
.conflict button {
  justify-self: start;
}

.sync-failed {
  border: 1px solid #2c3038;
  border-left: 3px solid #ff7b72;
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
}
</style>
