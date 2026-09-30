<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import type { components } from '../api/schema'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useJobsStore } from '../stores/jobs'
import type { BuildInPayload } from '../stores/jobs'
import type { AuthoredFigureSeed } from '../stores/jobs'
import type { FlatSeedEntry } from '../stores/jobs'
import CharacterSheetEditor from '../components/CharacterSheetEditor.vue'
import EntityPicker from '../components/ui/EntityPicker.vue'
import type { EntityPickerOption } from '../components/ui/EntityPicker.vue'
import { useTonightStore } from '../stores/tonight'
import { useWorldStore } from '../stores/world'
import { EDGE_VOCAB } from '../components/profile/profile'
import { connectJobSocket } from '../ws'
import type { WsMessage } from '../ws'

type Job = components['schemas']['JobResponse']

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string

const campaigns = useCampaignsStore()
const jobs = useJobsStore()
const tonight = useTonightStore()
const world = useWorldStore()
const loadError = ref<string | null>(null)

const sections: Array<{ key: SectionKey; label: string; hint: string }> = [
  { key: 'places', label: 'Key places', hint: 'The Saltglass Harbor' },
  { key: 'factions', label: 'Factions', hint: 'The Lantern Guild' },
  { key: 'key_figures', label: 'Key figures', hint: 'Mara Vey, harbor master' },
]
type SectionKey = 'places' | 'factions' | 'key_figures'

const sectionText = ref<Record<SectionKey, string>>({ places: '', factions: '', key_figures: '' })
const quickSlots = ref<Record<SectionKey, number>>({ places: 0, factions: 0, key_figures: 0 })
const generateCounts = ref<Record<SectionKey, number>>({ places: 0, factions: 0, key_figures: 0 })
const MAX_QUICK_SLOTS = 100

function quickLines(key: SectionKey): string[] {
  return sectionText.value[key] ? sectionText.value[key].split(/\r?\n/) : []
}

function lastNamedSlot(lines: string[]): number {
  for (let index = lines.length - 1; index >= 0; index--) {
    if (lines[index]?.trim()) return index + 1
  }
  return 0
}

function slotCount(key: SectionKey): number {
  return Math.max(quickSlots.value[key], lastNamedSlot(quickLines(key)))
}

function setSlotCount(key: SectionKey, value: number) {
  const requested = Number.isFinite(value)
    ? Math.min(MAX_QUICK_SLOTS, Math.max(0, Math.trunc(value)))
    : 0
  const lines = quickLines(key)
  quickSlots.value[key] = Math.max(requested, lastNamedSlot(lines))
}

function setQuickName(key: SectionKey, index: number, value: string) {
  const lines = quickLines(key)
  while (lines.length <= index) lines.push('')
  lines[index] = value.replace(/[\r\n]+/g, ' ')
  sectionText.value[key] = lines.join('\n')
}

function removeQuickName(key: SectionKey, index: number) {
  const previousCount = slotCount(key)
  const lines = quickLines(key)
  lines.splice(index, 1)
  sectionText.value[key] = lines.join('\n')
  quickSlots.value[key] = Math.max(0, previousCount - 1)
}

function syncPastedNames(key: SectionKey) {
  quickSlots.value[key] = Math.max(quickSlots.value[key], quickLines(key).length)
}

function onGenerateCountChange(key: SectionKey) {
  const named =
    splitEntries(sectionText.value[key]).length +
    (key === 'key_figures'
      ? figureEditors.value.length
      : flatDraftsFor(key === 'places' ? 'place' : 'faction').length)
  const value = Number(generateCounts.value[key])
  generateCounts.value[key] = Number.isFinite(value)
    ? Math.min(Math.max(0, MAX_QUICK_SLOTS - named), Math.max(0, Math.trunc(value)))
    : 0
}
type FlatKind = 'place' | 'faction'
interface FlatSeedDraft extends FlatSeedEntry {
  id: number
  kind: FlatKind
}
const flatSeedDrafts = ref<FlatSeedDraft[]>([])
let flatSeedSeq = 0
/**
 * Authored key figures use the SAME sheet editor as the forge (one
 * authoring surface everywhere): each entry is a full editor instance —
 * every record field, per-subsection stat-block toggles, dice boxes,
 * relations with existing/new target modes. */
interface FigureEditor {
  id: number
}
let figureEditorSeq = 0
const figureEditors = ref<FigureEditor[]>([])
const editorRefs = ref<Array<InstanceType<typeof CharacterSheetEditor> | null>>([])

function addFigure() {
  figureEditors.value.push({ id: ++figureEditorSeq })
}
function removeFigure(index: number) {
  figureEditors.value.splice(index, 1)
  editorRefs.value.splice(index, 1)
}
const notes = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)
const draftReady = ref(false)
const draftStorageKey = `mythoscircle:build-in:${campaignId}`

let disconnectSocket: (() => void) | null = null
let statusTimer: ReturnType<typeof globalThis.setInterval> | null = null
let statusPollPending = false
const statusClock = ref(Date.now())
const submittedJobId = ref<string | null>(null)

onMounted(async () => {
  try {
    await Promise.all([
      campaigns.fetchOne(campaignId),
      jobs.syncList(campaignId),
      tonight.fetchKinds(campaignId),
      world.load(campaignId),
    ])
    if (campaigns.error) {
      loadError.value = campaigns.error
      return
    }
    restoreDraft()
    draftReady.value = true
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load the world.'
    return
  }
  disconnectSocket = connectJobSocket(
    campaignId,
    (message) => {
      void onJobMessage(message)
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

interface StoredBuildDraft {
  sectionText?: Partial<Record<SectionKey, string>>
  quickSlots?: Partial<Record<SectionKey, number>>
  generateCounts?: Partial<Record<SectionKey, number>>
  flatSeedDrafts?: Array<Omit<FlatSeedDraft, 'id'> & { id?: number }>
  notes?: string
}

function restoreDraft() {
  const stored: StoredBuildDraft | null = (() => {
    try {
      const raw = globalThis.localStorage.getItem(draftStorageKey)
      return raw ? (JSON.parse(raw) as StoredBuildDraft) : null
    } catch {
      return null
    }
  })()

  const seed = stored ? null : recentJobs.value.find((job) => job.state === 'failed')
  if (stored) {
    sectionText.value = {
      places: stored.sectionText?.places ?? '',
      factions: stored.sectionText?.factions ?? '',
      key_figures: stored.sectionText?.key_figures ?? '',
    }
    for (const key of ['places', 'factions', 'key_figures'] as const) {
      setSlotCount(key, stored.quickSlots?.[key] ?? splitEntries(sectionText.value[key]).length)
      generateCounts.value[key] = stored.generateCounts?.[key] ?? 0
    }
    flatSeedDrafts.value = (stored.flatSeedDrafts ?? []).map((draft) => ({
      ...draft,
      id: ++flatSeedSeq,
    }))
    notes.value = stored.notes ?? ''
    return
  }
  if (seed) {
    const recovered = jobSeed(seed.payload)
    if (recovered) hydrateFromSeed(recovered)
  }
}

function hydrateFromSeed(seed: BuildInPayload) {
  const stringsFor = (entries: unknown[] | undefined) =>
    (entries ?? []).filter((entry): entry is string => typeof entry === 'string').join('\n')
  sectionText.value = {
    places: stringsFor(seed.places),
    factions: stringsFor(seed.factions),
    key_figures: stringsFor(seed.key_figures),
  }
  for (const key of ['places', 'factions', 'key_figures'] as const) {
    setSlotCount(key, splitEntries(sectionText.value[key]).length)
    generateCounts.value[key] = seed.generate_counts?.[key] ?? 0
  }
  const structured = (entries: unknown[] | undefined, kind: FlatKind): FlatSeedDraft[] =>
    (entries ?? []).flatMap((entry) => {
      if (typeof entry !== 'object' || entry === null) return []
      const value = entry as FlatSeedEntry
      return typeof value.name === 'string' ? [{ ...value, id: ++flatSeedSeq, kind }] : []
    })
  flatSeedDrafts.value = [
    ...structured(seed.places, 'place'),
    ...structured(seed.factions, 'faction'),
  ]
  notes.value = seed.notes ?? ''
}

function saveDraft() {
  if (!draftReady.value) return
  try {
    globalThis.localStorage.setItem(
      draftStorageKey,
      JSON.stringify({
        sectionText: sectionText.value,
        quickSlots: quickSlots.value,
        generateCounts: generateCounts.value,
        flatSeedDrafts: flatSeedDrafts.value,
        notes: notes.value,
      } satisfies StoredBuildDraft),
    )
  } catch {
    // Draft persistence is best effort; the build itself remains unaffected.
  }
}

function clearDraft() {
  try {
    globalThis.localStorage.removeItem(draftStorageKey)
  } catch {
    // Ignore unavailable browser storage.
  }
}

watch([sectionText, quickSlots, generateCounts, flatSeedDrafts, notes], saveDraft, { deep: true })

onUnmounted(() => {
  disconnectSocket?.()
  if (statusTimer !== null) globalThis.clearInterval(statusTimer)
})

const recentJobs = computed(() => jobs.buildInJobs(campaignId).slice(0, 10))
const inFlight = computed(() => jobs.buildInInFlight(campaignId))
const statusJob = computed(() =>
  submittedJobId.value
    ? (jobs.byId[submittedJobId.value] ?? null)
    : (recentJobs.value.find((job) => job.state === 'queued' || job.state === 'running') ?? null),
)
const statusElapsed = computed(() => {
  const started = statusJob.value?.started_at ?? statusJob.value?.created_at
  if (!started) return 0
  return Math.max(0, Math.floor((statusClock.value - Date.parse(started)) / 1000))
})

watch(
  inFlight,
  (active) => {
    if (statusTimer !== null) globalThis.clearInterval(statusTimer)
    statusTimer = null
    if (!active) return
    let ticks = 0
    statusTimer = globalThis.setInterval(() => {
      statusClock.value = Date.now()
      ticks += 1
      if (ticks % 3 !== 0 || statusPollPending) return
      statusPollPending = true
      void jobs
        .syncList(campaignId)
        .catch(() => {})
        .finally(() => {
          statusPollPending = false
        })
    }, 1000)
  },
  { immediate: true },
)

function statusPercent(job: Job): number {
  return job.state === 'succeeded' ? 100 : Math.round(job.progress * 100)
}

function statusHeadline(job: Job): string {
  if (job.state === 'queued') return 'Waiting to start'
  if (job.state === 'succeeded') return 'World updated'
  if (job.state === 'failed') return 'Build could not finish'
  if (job.state === 'cancelled') return 'Build cancelled'
  if (job.progress < 0.1) {
    const counts = job.payload?.['generate_counts']
    return counts && typeof counts === 'object' && Object.values(counts).some(Boolean)
      ? 'Inventing names'
      : 'Preparing the world'
  }
  if (job.progress < 0.4) return 'Writing the world'
  if (job.progress < 0.5) return 'Checking details and connections'
  return 'Saving the world'
}

function completedCount(job: Job): number | null {
  const value = job.result?.['entity_count']
  return typeof value === 'number' ? value : null
}
const hasContent = computed(() => {
  const anySection = sections.some(
    (section) => splitEntries(sectionText.value[section.key]).length > 0,
  )
  const anyEditor = editorRefs.value.some((editor) => editor?.hasContent)
  const anyFlatDraft = flatSeedDrafts.value.some((draft) => draft.name.trim().length > 0)
  return (
    anySection ||
    Object.values(generateCounts.value).some(Boolean) ||
    notes.value.trim().length > 0 ||
    anyEditor ||
    anyFlatDraft
  )
})

const buildPreview = computed(() => ({
  places: flatEntries('place').length + generateCounts.value.places,
  factions: flatEntries('faction').length + generateCounts.value.factions,
  figures:
    splitEntries(sectionText.value.key_figures).length +
    figureEditors.value.filter((_, index) => editorRefs.value[index]?.hasContent).length +
    generateCounts.value.key_figures,
  generated: Object.values(generateCounts.value).reduce((sum, count) => sum + count, 0),
  hasNotes: notes.value.trim().length > 0,
}))
const overLimit = computed(() =>
  (['places', 'factions', 'figures'] as const).filter((kind) => buildPreview.value[kind] > 100),
)

function splitEntries(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

const kindsRegistry = computed(() => tonight.kindsFor(campaignId))
const dialLevels = computed(() => kindsRegistry.value?.dial_levels ?? [])

function archetypesFor(kind: FlatKind) {
  return (kindsRegistry.value?.archetypes ?? []).filter((entry) => entry.kind === kind)
}

function normalizeSeedName(value: string): string {
  return value
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^(the|a|an)\s+/, '')
}

function knownTargetKind(targetName: string): string | null {
  const normalized = normalizeSeedName(targetName)
  if (!normalized) return null
  const candidates: Array<{ name: string; kind: string }> = [
    ...(world.entry(campaignId).world?.entities ?? []).map((entity) => ({
      name: entity.name,
      kind: entity.kind,
    })),
    ...flatSeedDrafts.value.map((draft) => ({ name: draft.name, kind: draft.kind })),
    ...splitEntries(sectionText.value.places).map((name) => ({ name, kind: 'place' })),
    ...splitEntries(sectionText.value.factions).map((name) => ({ name, kind: 'faction' })),
    ...splitEntries(sectionText.value.key_figures).map((name) => ({ name, kind: 'character' })),
  ]
  return (
    candidates.find((candidate) => normalizeSeedName(candidate.name) === normalized)?.kind ?? null
  )
}

function relationTypesFor(draft: FlatSeedDraft, targetName = ''): string[] {
  const registry = kindsRegistry.value
  const types = registry?.edge_types ?? EDGE_VOCAB
  const targetKind = knownTargetKind(targetName)
  const targetKinds = targetKind ? [targetKind] : ['character', 'faction', 'place']
  const allowed = types.filter((type) => {
    const rule = registry?.kind_rules[type]
    if (!rule) return true
    const sourceAllowed = !rule.src || rule.src.includes(draft.kind)
    const targetAllowed = !rule.dst || targetKinds.some((kind) => rule.dst?.includes(kind))
    return sourceAllowed && targetAllowed
  })
  return allowed
}

function flatDraftsFor(kind: FlatKind) {
  return flatSeedDrafts.value.filter((draft) => draft.kind === kind)
}

function quickNamesFor(kind: FlatKind): string[] {
  return splitEntries(sectionText.value[kind === 'place' ? 'places' : 'factions'])
}

const entityPickerOptions = computed<EntityPickerOption[]>(() => {
  const options: EntityPickerOption[] = []
  const add = (name: string, kind: string, source: string) => {
    const clean = name.trim()
    if (clean) options.push({ name: clean, kind, source })
  }
  for (const entity of world.entry(campaignId).world?.entities ?? []) {
    add(entity.name, entity.kind, 'in this world')
  }
  for (const name of quickNamesFor('place')) add(name, 'place', 'free text')
  for (const name of quickNamesFor('faction')) add(name, 'faction', 'free text')
  for (const name of splitEntries(sectionText.value.key_figures))
    add(name, 'character', 'free text')
  for (const draft of flatSeedDrafts.value) add(draft.name, draft.kind, 'shaped entry')
  const seen = new Set<string>()
  return options.filter((option) => {
    const key = `${option.kind}:${normalizeSeedName(option.name)}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})

function pickerOptionsFor(kind?: FlatKind): EntityPickerOption[] {
  return kind
    ? entityPickerOptions.value.filter((option) => option.kind === kind)
    : entityPickerOptions.value
}

function addFlatSeed(kind: FlatKind) {
  const first = archetypesFor(kind)[0]
  flatSeedDrafts.value.push({
    id: ++flatSeedSeq,
    kind,
    name: '',
    description: '',
    archetype: first?.name,
    dial: first?.default_dial ?? dialLevels.value[0],
  })
}

function removeFlatSeed(id: number) {
  flatSeedDrafts.value = flatSeedDrafts.value.filter((draft) => draft.id !== id)
}

function addFlatRelation(draft: FlatSeedDraft) {
  draft.relations = [
    ...(draft.relations ?? []),
    { type: EDGE_VOCAB[0] ?? 'relationship', target_name: '' },
  ]
}

function removeFlatRelation(draft: FlatSeedDraft, index: number) {
  draft.relations = (draft.relations ?? []).filter((_, relationIndex) => relationIndex !== index)
}

function flatEntries(kind: FlatKind): (string | FlatSeedEntry)[] {
  const textKey = kind === 'place' ? 'places' : 'factions'
  const structured = flatDraftsFor(kind)
    .filter((draft) => draft.name.trim().length > 0)
    .map((draft) => ({
      name: draft.name,
      description: draft.description,
      archetype: draft.archetype,
      dial: draft.dial,
      relations: draft.relations?.filter((relation) => relation.target_name?.trim()),
    }))
  // A shaped entry is the canonical version of a same-named quick entry.
  // Keeping both sends a short legacy row before the dialled row and can
  // make the validator inspect the wrong record.
  const structuredNames = new Set(structured.map((entry) => normalizeSeedName(entry.name)))
  const quickEntries = splitEntries(sectionText.value[textKey]).filter(
    (name) => !structuredNames.has(normalizeSeedName(name)),
  )
  return [...quickEntries, ...structured]
}

/** The form as the enqueue payload — what `submit` sends and what a
 * build-in job row carries. Flat kinds mix quick legacy strings with
 * structured registry-backed entries; key figures mix textarea lines with
 * entries authored in the shared sheet editor. */
function formSeed(): BuildInPayload {
  const figures: (string | AuthoredFigureSeed)[] = splitEntries(sectionText.value.key_figures)
  for (const editor of editorRefs.value) {
    if (!editor) continue
    if (!editor.hasContent) continue
    figures.push(editor.buildFigure())
  }
  return {
    places: flatEntries('place'),
    factions: flatEntries('faction'),
    key_figures: figures,
    generate_counts: { ...generateCounts.value },
    notes: notes.value.trim(),
  }
}

/** A job row's key-figure entry read back as the authored seed shape;
 * null for anything the authored form could not have produced. */
function figureSeed(payload: unknown): AuthoredFigureSeed | null {
  if (typeof payload !== 'object' || payload === null) return null
  const entry = payload as Record<string, unknown>
  if (typeof entry['name'] !== 'string' || !entry['name'].trim()) return null
  return entry as unknown as AuthoredFigureSeed
}

/** A job row's payload read back as a seed; null when it is not that shape. */
function jobSeed(payload: unknown): BuildInPayload | null {
  if (typeof payload !== 'object' || payload === null) return null
  const record = payload as Record<string, unknown>
  const flatEntriesFromJob = (key: string): (string | FlatSeedEntry)[] | null => {
    const value = record[key]
    if (!Array.isArray(value)) return null
    return value.flatMap((entry: unknown): (string | FlatSeedEntry)[] => {
      if (typeof entry === 'string') return [entry]
      if (typeof entry !== 'object' || entry === null) return []
      const flat = entry as Record<string, unknown>
      return typeof flat['name'] === 'string' && flat['name'].trim()
        ? [flat as unknown as FlatSeedEntry]
        : []
    })
  }
  const figureEntriesFromJob = (key: string): (string | AuthoredFigureSeed)[] | null => {
    const value = record[key]
    if (!Array.isArray(value)) return null
    return value.flatMap((entry: unknown): (string | AuthoredFigureSeed)[] => {
      if (typeof entry === 'string') return [entry]
      const figure = figureSeed(entry)
      return figure ? [figure] : []
    })
  }
  const places = flatEntriesFromJob('places')
  const factions = flatEntriesFromJob('factions')
  const keyFigures = figureEntriesFromJob('key_figures')
  const notes = record['notes']
  if (!places || !factions || !keyFigures || typeof notes !== 'string') return null
  const rawCounts = record['generate_counts']
  const generate_counts =
    typeof rawCounts === 'object' && rawCounts !== null
      ? (rawCounts as BuildInPayload['generate_counts'])
      : undefined
  return { places, factions, key_figures: keyFigures, notes, generate_counts }
}

function sameEntry(
  a: string | AuthoredFigureSeed | FlatSeedEntry,
  b: string | AuthoredFigureSeed | FlatSeedEntry,
): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

function sameSeed(a: BuildInPayload, b: BuildInPayload): boolean {
  return (
    a.notes === b.notes &&
    (['places', 'factions', 'key_figures'] as const).every(
      (key) => (a.generate_counts?.[key] ?? 0) === (b.generate_counts?.[key] ?? 0),
    ) &&
    (['places', 'factions', 'key_figures'] as const).every((key) => {
      const left = a[key] ?? []
      const right = b[key] ?? []
      return left.length === right.length && left.every((entry, i) => sameEntry(entry, right[i]))
    })
  )
}

/**
 * WS dispatch: the jobs store absorbs every frame. The seed text is only
 * spent once the world actually took it — a build-in that FAILS or is
 * cancelled leaves the form intact so the DM retries without retyping
 * (the candidates ask box's rule, spec-3.1). A succeeding job clears the
 * form only while it still holds exactly the seed that job built: text
 * typed for the next batch is never thrown away.
 */
async function onJobMessage(message: WsMessage) {
  await jobs.handleWsMessage(campaignId, message)
  if (message.type !== 'job_done') return
  const job = jobs.byId[message.job_id]
  if (!job || job.kind !== 'build_in') return
  const built = jobSeed(job.payload)
  if (built && sameSeed(built, formSeed())) {
    figureEditors.value = []
    editorRefs.value = []
    sectionText.value = { places: '', factions: '', key_figures: '' }
    quickSlots.value = { places: 0, factions: 0, key_figures: 0 }
    generateCounts.value = { places: 0, factions: 0, key_figures: 0 }
    notes.value = ''
    clearDraft()
  }
}

async function submit() {
  error.value = null
  submitting.value = true
  saveDraft()
  try {
    const submitted = await jobs.submitBuildIn(campaignId, formSeed())
    submittedJobId.value = submitted.id
    statusClock.value = Date.now()
    await nextTick()
    globalThis.document.getElementById('mc-build-status')?.scrollIntoView?.({
      behavior: 'smooth',
      block: 'nearest',
    })
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not enqueue the build-in.'
  } finally {
    submitting.value = false
  }
}

function stateLabel(job: Job): string {
  return job.state === 'queued' ? `Queued (position ${job.queue_position ?? '…'})` : job.state
}

interface ContextSummary {
  entities: number
  by_kind?: Record<string, number>
  retrieval_cap?: number
  truncated?: boolean
}

/** What the build saw: the committed world it arrived at (owner note 4). */
function contextLabel(result: unknown): string | null {
  const ctx = (result as { context?: ContextSummary } | null)?.context
  if (!ctx || typeof ctx.entities !== 'number') return null
  const kinds = Object.entries(ctx.by_kind ?? {})
    .map(([kind, n]) => `${n} ${kind}${n === 1 ? '' : 's'}`)
    .join(', ')
  const seen = `${ctx.entities} ${ctx.entities === 1 ? 'entity' : 'entities'}${
    kinds ? ` (${kinds})` : ''
  }`
  return ctx.truncated
    ? `${seen} — retrieval cap ${ctx.retrieval_cap ?? '?'} reached, the model saw a neighborhood`
    : `${seen} — full retrieval (cap ${ctx.retrieval_cap ?? '?'})`
}

interface MergeAudit {
  merged?: unknown[] | number
  unchanged?: unknown[] | number
  dropped_edges?: unknown[] | number
  twins_dropped?: unknown[] | number
  edge_kind_dropped?: unknown[] | number
}

/** What the build changed: the per-wave upsert audit (merged/unchanged…). */
function mergeLabel(wave: 'wave1' | 'wave2', audit: unknown): string | null {
  const a = (audit ?? null) as MergeAudit | null
  if (!a) return null
  const count = (value: unknown[] | number | undefined): number =>
    Array.isArray(value) ? value.length : typeof value === 'number' ? value : 0
  const bits = [
    `merged ${count(a.merged)}`,
    `unchanged ${count(a.unchanged)}`,
    `dropped edges ${count(a.dropped_edges)}`,
  ]
  const twins = count(a.twins_dropped)
  if (twins) bits.push(`twins dropped ${twins}`)
  const kindDrops = count(a.edge_kind_dropped)
  if (kindDrops) bits.push(`kind-rule drops ${kindDrops}`)
  return `${wave === 'wave1' ? 'Wave 1' : 'Wave 2'}: ${bits.join(' · ')}`
}

function mergeLines(result: unknown): string[] {
  const merge = (result as { merge?: Record<'wave1' | 'wave2', unknown> } | null)?.merge
  if (!merge) return []
  return (['wave1', 'wave2'] as const)
    .map((wave) => mergeLabel(wave, merge[wave]))
    .filter((line): line is string => line !== null)
}
</script>

<template>
  <section class="mc-build-page">
    <header class="mc-build-header">
      <p class="mc-eyebrow">World creation</p>
      <h1 class="mc-display-title">Guided build-in</h1>
      <p class="mc-build-lede">
        Give the world a few anchors. The build will connect them into a living campaign.
      </p>
    </header>

    <ol class="mc-build-stepper" aria-label="Build stages">
      <li class="mc-build-step-active"><span>1</span> Seed the world</li>
      <li><span>2</span> Shape entities</li>
      <li><span>3</span> Review the build</li>
    </ol>

    <div v-if="loadError" class="card">
      <p class="error">{{ loadError }}</p>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>
    <p v-else-if="campaigns.loading || !campaigns.current" class="muted">Loading world seed…</p>
    <template v-else>
      <div class="mc-build-layout">
        <div class="mc-build-main">
          <div class="card seed">
            <h2>{{ campaigns.current.title }}</h2>
            <p class="muted">{{ campaigns.current.description || 'No description.' }}</p>
            <p>
              <RouterLink
                :to="{ name: 'overview', params: { id: campaignId } }"
                class="mc-btn mc-btn-secondary"
                >Open overview</RouterLink
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

          <form class="card mc-build-form" @submit.prevent="submit">
            <div v-for="section in sections" :key="section.key" class="mc-build-section">
              <div class="mc-quick-heading">
                <div>
                  <h2>{{ section.label }}</h2>
                  <p>Let the model invent names, or add your own anchors below.</p>
                </div>
                <label class="mc-slot-count">
                  <span>Generate how many?</span>
                  <input
                    v-model.number="generateCounts[section.key]"
                    type="number"
                    min="0"
                    :max="MAX_QUICK_SLOTS"
                    :aria-label="`Generate ${section.label.toLowerCase()}`"
                    @change="onGenerateCountChange(section.key)"
                  />
                </label>
              </div>
              <p v-if="generateCounts[section.key]" class="mc-generated-hint">
                The model will invent {{ generateCounts[section.key] }}
                {{ section.key === 'key_figures' ? 'figures' : section.key }} and build full records
                for them.
              </p>
              <div v-if="slotCount(section.key)" class="mc-quick-rows">
                <div
                  v-for="index in slotCount(section.key)"
                  :key="`${section.key}-${index}`"
                  class="mc-quick-row"
                >
                  <label :for="`quick-${section.key}-${index}`" class="sr-only"
                    ><span>{{ section.label }} {{ index }}</span></label
                  >
                  <input
                    :id="`quick-${section.key}-${index}`"
                    type="text"
                    class="mc-seed-input"
                    :value="quickLines(section.key)[index - 1] ?? ''"
                    :placeholder="section.hint"
                    @input="
                      setQuickName(
                        section.key,
                        index - 1,
                        ($event.target as HTMLInputElement).value,
                      )
                    "
                  />
                  <button
                    type="button"
                    class="mc-quick-remove"
                    :aria-label="`Remove ${section.label} ${index}`"
                    @click="removeQuickName(section.key, index - 1)"
                  >
                    Remove
                  </button>
                </div>
              </div>
              <div class="mc-quick-tools">
                <button
                  type="button"
                  class="mc-btn mc-btn-secondary"
                  :disabled="slotCount(section.key) >= MAX_QUICK_SLOTS"
                  @click="setSlotCount(section.key, slotCount(section.key) + 1)"
                >
                  + Add a named
                  {{
                    section.key === 'key_figures'
                      ? 'figure'
                      : section.key === 'places'
                        ? 'place'
                        : 'faction'
                  }}
                </button>
                <details class="mc-paste-list">
                  <summary>Paste a list instead</summary>
                  <label
                    ><span>{{ section.label }}</span>
                    <textarea
                      v-model="sectionText[section.key]"
                      :placeholder="`One ${section.key === 'key_figures' ? 'figure' : section.key === 'places' ? 'place' : 'faction'} per line`"
                      rows="3"
                      @input="syncPastedNames(section.key)"
                    />
                  </label>
                </details>
              </div>
              <p
                v-if="section.key === 'places' || section.key === 'factions'"
                class="mc-build-section-help"
              >
                Want to direct the details? Add a shaped entry with an archetype, dial, and
                relations.
              </p>
              <div
                v-if="section.key === 'places' || section.key === 'factions'"
                class="mc-flat-seeds"
              >
                <article
                  v-for="draft in flatDraftsFor(section.key === 'places' ? 'place' : 'faction')"
                  :key="draft.id"
                  class="mc-flat-seed"
                >
                  <div class="mc-flat-seed-heading">
                    <span class="mc-card-kicker">{{ draft.kind }}</span>
                    <button type="button" class="link" @click="removeFlatSeed(draft.id)">
                      Remove
                    </button>
                  </div>
                  <div class="mc-flat-seed-grid">
                    <label>
                      <span>Name</span>
                      <EntityPicker
                        v-model="draft.name"
                        :options="pickerOptionsFor(draft.kind)"
                        placeholder="Choose or type a name"
                      />
                    </label>
                    <label>
                      <span>Archetype</span>
                      <select v-model="draft.archetype" class="mc-seed-input">
                        <option
                          v-for="entry in archetypesFor(draft.kind)"
                          :key="entry.name"
                          :value="entry.name"
                        >
                          {{ entry.name }}
                        </option>
                      </select>
                    </label>
                    <label class="mc-flat-seed-description">
                      <span>Description</span>
                      <textarea
                        v-model="draft.description"
                        class="mc-seed-input"
                        rows="2"
                        placeholder="What makes it matter?"
                      />
                    </label>
                    <label>
                      <span>Dial</span>
                      <select v-model="draft.dial" class="mc-seed-input">
                        <option v-for="level in dialLevels" :key="level" :value="level">
                          {{ level }}
                        </option>
                      </select>
                    </label>
                  </div>
                  <div class="mc-flat-relations">
                    <div class="mc-flat-relations-heading">
                      <span>Relations</span>
                      <button type="button" class="link" @click="addFlatRelation(draft)">
                        + add relation
                      </button>
                    </div>
                    <div
                      v-for="(relation, relationIndex) in draft.relations ?? []"
                      :key="relationIndex"
                      class="mc-flat-relation"
                    >
                      <select
                        v-model="relation.type"
                        class="mc-seed-input"
                        aria-label="Relation type"
                      >
                        <option
                          v-for="type in relationTypesFor(draft, relation.target_name)"
                          :key="type"
                          :value="type"
                        >
                          {{ type }}
                        </option>
                      </select>
                      <EntityPicker
                        :model-value="relation.target_name ?? ''"
                        :options="pickerOptionsFor()"
                        aria-label="Relation target"
                        placeholder="Choose or type a target"
                        @update:model-value="relation.target_name = $event"
                      />
                      <input
                        v-model.number="relation.counter"
                        class="mc-seed-input mc-relation-counter"
                        type="number"
                        aria-label="Relation counter"
                        placeholder="Counter"
                      />
                      <button
                        type="button"
                        class="link"
                        :aria-label="`Remove relation ${relationIndex + 1}`"
                        @click="removeFlatRelation(draft, relationIndex)"
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                </article>
              </div>
              <button
                v-if="section.key === 'places' || section.key === 'factions'"
                type="button"
                class="mc-btn mc-btn-secondary mc-add-flat-seed"
                @click="addFlatSeed(section.key === 'places' ? 'place' : 'faction')"
              >
                + shaped {{ section.key === 'places' ? 'place' : 'faction' }}
              </button>
            </div>
            <div class="authored">
              <h2>Authored key figures</h2>
              <p class="muted small">
                Optional. A figure here is YOUR fact — the model builds the rest of the world around
                it and your text commits verbatim. A relation naming someone who does not exist yet
                (a city, a cult) makes the build create them; the relation's type decides what
                (located in → a place). Plain lines in Key figures above stay free-form.
              </p>
              <article v-for="(figure, index) in figureEditors" :key="figure.id" class="figure">
                <div class="figure-head">
                  <span class="muted small">Authored figure {{ index + 1 }}</span>
                  <button type="button" class="link" @click="removeFigure(index)">
                    ✕ remove figure
                  </button>
                </div>
                <CharacterSheetEditor
                  :ref="
                    (el) => (editorRefs[index] = el as InstanceType<typeof CharacterSheetEditor>)
                  "
                  :campaign-id="campaignId"
                />
              </article>
              <button type="button" class="link" @click="addFigure">+ authored figure</button>
            </div>
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
            <div class="mc-build-summary" aria-live="polite">
              <div>
                <p class="mc-card-kicker">Ready to build</p>
                <strong
                  >{{ buildPreview.places + buildPreview.factions + buildPreview.figures }} entities
                  requested</strong
                >
                <p>
                  {{ buildPreview.generated }} invented by the model; your named anchors and
                  campaign seed shape the rest{{ buildPreview.hasNotes ? ' with your notes' : '' }}.
                </p>
              </div>
              <ul>
                <li>{{ buildPreview.places }} places</li>
                <li>{{ buildPreview.factions }} factions</li>
                <li>{{ buildPreview.figures }} figures</li>
              </ul>
            </div>
            <p v-if="overLimit.length" class="error">
              Keep each group to 100 or fewer, including your named entries:
              {{ overLimit.join(', ') }}.
            </p>
            <p v-if="error" class="error">{{ error }}</p>
            <button
              type="submit"
              class="mc-btn mc-build-submit"
              :disabled="submitting || inFlight || !hasContent || overLimit.length > 0"
            >
              {{ submitting ? 'Enqueuing…' : inFlight ? 'Building…' : 'Build my world' }}
            </button>
            <section
              v-if="statusJob"
              id="mc-build-status"
              class="mc-build-status"
              aria-live="polite"
              aria-label="Build status"
            >
              <div class="mc-build-status-head">
                <div>
                  <p class="mc-card-kicker">Build status</p>
                  <h2>{{ statusHeadline(statusJob) }}</h2>
                </div>
                <span class="mc-build-status-time">{{ statusElapsed }}s elapsed</span>
              </div>
              <template v-if="statusJob.state === 'queued' || statusJob.state === 'running'">
                <p v-if="statusJob.state === 'queued'" class="muted small">
                  Queue position {{ statusJob.queue_position ?? 'pending' }}. Your entries are saved
                  while this runs.
                </p>
                <p v-else class="muted small">
                  {{ statusPercent(statusJob) }}% through the build stages. Larger requests can take
                  a few minutes; this status refreshes automatically.
                </p>
                <div
                  class="mc-build-progress"
                  role="progressbar"
                  :aria-valuenow="statusPercent(statusJob)"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  aria-label="Build progress"
                >
                  <span :style="{ width: `${statusPercent(statusJob)}%` }" />
                </div>
              </template>
              <p v-else-if="statusJob.state === 'succeeded'" class="mc-build-status-success">
                <template v-if="completedCount(statusJob) !== null"
                  >{{ completedCount(statusJob) }}
                  {{ completedCount(statusJob) === 1 ? 'entity was' : 'entities were' }} added to
                  the world.</template
                >
                <template v-else>The build was added to the world.</template>
                <RouterLink :to="{ name: 'world', params: { id: campaignId } }"
                  >View world →</RouterLink
                >
              </p>
              <p v-else-if="statusJob.error" class="error">{{ statusJob.error }}</p>
              <p v-if="statusJob.state === 'failed'" class="muted small">
                Your entries are still here, so you can retry.
              </p>
            </section>
          </form>
        </div>
      </div>
    </template>

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
          <template v-if="job.state === 'succeeded' && job.result">
            <dt>World at build</dt>
            <dd v-if="contextLabel(job.result)">{{ contextLabel(job.result) }}</dd>
            <dt v-if="mergeLines(job.result).length > 0">What changed</dt>
            <dd v-if="mergeLines(job.result).length > 0">
              <span v-for="line in mergeLines(job.result)" :key="line">{{ line }}<br /></span>
              <span class="muted small"
                >Places and factions merge into the existing world — nothing is duplicated.
                Characters never merge: every build commits them as new entries (regenerate or edit
                an existing character to change it).</span
              >
            </dd>
          </template>
        </dl>
      </article>
      <p class="muted small">
        The screen is not blocked — the job runs in the background and updates live.
      </p>
    </div>
  </section>
</template>

<style scoped>
.mc-build-page {
  max-width: 980px;
}
.seed,
.mc-build-form,
.job {
  padding: 1.3rem;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  background: var(--mc-surface);
}
.seed {
  background:
    radial-gradient(circle at 100% 0%, rgba(139, 108, 255, 0.12), transparent 23rem),
    var(--mc-surface);
}
.seed > p {
  margin: 0.55rem 0;
}
.seed .lore {
  padding: 0.8rem 1rem;
  border-left: 2px solid var(--mc-interactive);
  color: var(--mc-text-secondary);
  background: var(--mc-surface-raised);
}
.mc-build-form > button[type='submit'] {
  justify-self: start;
}
.mc-build-form > button[type='submit']:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.mc-build-status {
  display: grid;
  gap: 0.65rem;
  padding: 1rem 1.1rem;
  border: 1px solid var(--mc-border-bright);
  border-radius: var(--mc-radius);
  background: var(--mc-surface-raised);
}
.mc-build-status-head {
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 0.75rem;
}
.mc-build-status h2 {
  margin: 0;
  font-family: var(--mc-display-font);
  font-size: 1.25rem;
}
.mc-build-status-time {
  color: var(--mc-text-muted);
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.mc-build-status .muted,
.mc-build-status .error,
.mc-build-status-success {
  margin: 0;
}
.mc-build-progress {
  height: 0.5rem;
  overflow: hidden;
  border-radius: 999px;
  background: var(--mc-border);
}
.mc-build-progress span {
  display: block;
  height: 100%;
  min-width: 0.3rem;
  border-radius: inherit;
  background: var(--mc-interactive);
  transition: width 0.3s ease;
}
.mc-build-status-success a {
  margin-left: 0.4rem;
  color: var(--mc-interactive-bright);
}
.mc-build-header {
  margin-bottom: 1.5rem;
}
.mc-eyebrow,
.mc-card-kicker {
  margin: 0 0 0.45rem;
  color: var(--mc-interactive-bright);
  font-size: var(--mc-meta-size);
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.mc-build-header h1 {
  margin: 0;
  font-size: var(--mc-page-title-size);
  line-height: 1.05;
}
.mc-build-lede {
  max-width: 54ch;
  margin: 0.65rem 0 0;
  color: var(--mc-text-secondary);
  font-size: 1.05rem;
}
.mc-build-stepper {
  display: flex;
  gap: 0;
  margin: 0 0 1.5rem;
  padding: 0.45rem;
  list-style: none;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  background: rgba(14, 23, 36, 0.72);
  overflow-x: auto;
}
.mc-build-stepper li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: max-content;
  padding: 0.55rem 0.8rem;
  color: var(--mc-text-muted);
  font-size: 0.85rem;
}
.mc-build-stepper span {
  display: grid;
  width: 1.45rem;
  height: 1.45rem;
  place-items: center;
  border: 1px solid var(--mc-border-bright);
  border-radius: 50%;
  font-size: 0.75rem;
}
.mc-build-stepper .mc-build-step-active {
  color: var(--mc-text-primary);
  border-radius: var(--mc-radius-sm);
  background: rgba(139, 108, 255, 0.14);
}
.mc-build-stepper .mc-build-step-active span {
  border-color: var(--mc-interactive);
  background: var(--mc-interactive);
  color: #fff;
}
.mc-build-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
  gap: 1.25rem;
}
.mc-build-main,
.mc-build-form {
  display: grid;
  gap: 1rem;
}
.mc-build-section {
  display: grid;
  gap: 0.7rem;
  padding: 1.25rem 0;
  border-bottom: 1px solid var(--mc-border);
}
.mc-quick-heading {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 1rem;
}
.mc-quick-heading h2 {
  margin: 0;
  font-family: var(--mc-display-font);
  font-size: 1.35rem;
}
.mc-quick-heading p {
  margin: 0.2rem 0 0;
  color: var(--mc-text-muted);
  font-size: 0.85rem;
}
.mc-slot-count {
  width: 8.8rem;
  flex: none;
  color: var(--mc-text-secondary);
  font-size: 0.8rem;
}
.mc-slot-count input {
  width: 100%;
  padding: 0.55rem 0.65rem;
  border: 1px solid var(--mc-border-bright);
  border-radius: var(--mc-radius-sm);
  background: var(--mc-input);
  color: var(--mc-text-primary);
}
.mc-quick-rows {
  display: grid;
  gap: 0.5rem;
}
.mc-quick-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.55rem;
}
.mc-generated-hint {
  margin: 0;
  color: var(--mc-interactive-bright);
  font-size: 0.85rem;
}
.mc-quick-remove {
  border: 0;
  background: transparent;
  color: var(--mc-text-muted);
  cursor: pointer;
}
.mc-quick-remove:hover {
  color: var(--mc-danger);
}
.mc-quick-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1rem;
}
.mc-quick-tools .mc-btn {
  font-size: 0.85rem;
  padding: 0.45rem 0.75rem;
}
.mc-paste-list {
  color: var(--mc-text-secondary);
  font-size: 0.85rem;
}
.mc-paste-list summary {
  cursor: pointer;
}
.mc-paste-list[open] {
  flex-basis: 100%;
}
.mc-paste-list label {
  margin-top: 0.6rem;
}
.mc-paste-list textarea {
  width: 100%;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.mc-build-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
  padding: 1rem;
  border: 1px solid var(--mc-border-bright);
  border-radius: var(--mc-radius-sm);
  background: var(--mc-surface-raised);
}
.mc-build-summary strong {
  font-family: var(--mc-display-font);
  font-size: 1.2rem;
}
.mc-build-summary p:last-child {
  margin: 0.2rem 0 0;
  color: var(--mc-text-secondary);
  font-size: 0.85rem;
}
.mc-build-summary ul {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.mc-build-summary li {
  padding: 0.3rem 0.55rem;
  border: 1px solid var(--mc-border);
  border-radius: 999px;
  color: var(--mc-text-secondary);
  font-size: 0.8rem;
}
.mc-build-section-help {
  margin: 0;
  color: var(--mc-text-muted);
  font-size: 0.8rem;
}
.mc-flat-seeds {
  display: grid;
  gap: 0.65rem;
}
.mc-flat-seed {
  display: grid;
  gap: 0.65rem;
  padding: 0.8rem;
  border: 1px solid var(--mc-border-bright);
  border-radius: var(--mc-radius-sm);
  background: rgba(10, 20, 33, 0.6);
}
.mc-flat-seed-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.mc-flat-seed-heading .mc-card-kicker {
  margin: 0;
}
.mc-flat-seed-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 0.75fr);
  gap: 0.65rem;
}
.mc-flat-seed-grid label {
  display: grid;
  gap: 0.25rem;
}
.mc-flat-seed-grid label > span {
  color: var(--mc-text-muted);
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.mc-flat-seed-description {
  grid-column: span 2;
}
.mc-seed-input {
  width: 100%;
  min-width: 0;
  padding: 0.55rem 0.65rem;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  background: var(--mc-input);
  color: var(--mc-text-primary);
}
.mc-seed-input:focus {
  border-color: var(--mc-interactive);
  outline: none;
  box-shadow: 0 0 0 3px var(--mc-glow-violet);
}
.mc-add-flat-seed {
  justify-self: start;
  padding: 0.45rem 0.75rem;
  font-size: 0.85rem;
}
.mc-flat-relations {
  display: grid;
  gap: 0.5rem;
  padding-top: 0.65rem;
  border-top: 1px solid var(--mc-border);
}
.mc-flat-relations-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--mc-text-muted);
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.mc-flat-relation {
  display: grid;
  grid-template-columns: minmax(130px, 0.75fr) minmax(0, 1.5fr) 6rem auto;
  gap: 0.5rem;
  align-items: center;
}
.mc-relation-counter {
  width: 6rem;
}
.seed h2 {
  margin-top: 0;
}
form {
  display: grid;
  gap: 1rem;
}
.authored {
  border: 1px dashed #2c3038;
  border-radius: 8px;
  padding: 0.75rem;
  display: grid;
  gap: 0.75rem;
}
.authored h2 {
  margin: 0;
  font-size: 1.05rem;
}
.figure {
  border: 1px solid #2c3038;
  border-radius: 6px;
  padding: 0.6rem;
  display: grid;
  gap: 0.5rem;
}
.figure-head {
  display: grid;
  grid-template-columns: 2fr auto auto;
  gap: 0.5rem;
  align-items: center;
}
.row {
  display: grid;
  grid-template-columns: 1fr 2fr auto;
  gap: 0.5rem;
  align-items: center;
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
@media (max-width: 760px) {
  .mc-quick-heading {
    align-items: start;
    flex-direction: column;
  }
  .mc-flat-seed-grid {
    grid-template-columns: 1fr;
  }
  .mc-flat-seed-description {
    grid-column: auto;
  }
  .mc-flat-relation {
    grid-template-columns: 1fr 1fr;
  }
  .mc-flat-relation .mc-relation-counter {
    width: auto;
  }
}
</style>
