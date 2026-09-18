<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api/client'
import type { components } from '../api/schema'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useJobsStore } from '../stores/jobs'
import type { BuildInPayload } from '../stores/jobs'
import type { AuthoredFigureSeed } from '../stores/jobs'
import CharacterSheetEditor from '../components/CharacterSheetEditor.vue'
import { connectJobSocket } from '../ws'
import type { WsMessage } from '../ws'

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

onUnmounted(() => {
  disconnectSocket?.()
})

const recentJobs = computed(() => jobs.buildInJobs(campaignId).slice(0, 10))
const inFlight = computed(() => jobs.buildInInFlight(campaignId))
const hasContent = computed(() => {
  const anySection = sections.some(
    (section) => splitEntries(sectionText.value[section.key]).length > 0,
  )
  const anyEditor = editorRefs.value.some((editor) => editor?.hasContent)
  return anySection || notes.value.trim().length > 0 || anyEditor
})

function splitEntries(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

/** The form as the enqueue payload — what `submit` sends and what a
 * build-in job row carries. Key figures mix legacy strings (textarea
 * lines) with entries authored in the shared sheet editor. */
function formSeed(): BuildInPayload {
  const figures: (string | AuthoredFigureSeed)[] = splitEntries(
    sectionText.value.key_figures,
  )
  for (const editor of editorRefs.value) {
    if (!editor) continue
    if (!editor.hasContent) continue
    figures.push(editor.buildFigure())
  }
  return {
    places: splitEntries(sectionText.value.places),
    factions: splitEntries(sectionText.value.factions),
    key_figures: figures,
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
  const entries = (key: string): (string | AuthoredFigureSeed)[] | null => {
    const value = record[key]
    if (!Array.isArray(value)) return null
    return value.flatMap((entry: unknown): (string | AuthoredFigureSeed)[] => {
      if (typeof entry === 'string') return [entry]
      const figure = figureSeed(entry)
      return figure ? [figure] : []
    })
  }
  const places = entries('places')
  const factions = entries('factions')
  const keyFigures = entries('key_figures')
  const notes = record['notes']
  if (
    !places ||
    !places.every((entry) => typeof entry === 'string') ||
    !factions ||
    !factions.every((entry) => typeof entry === 'string') ||
    !keyFigures ||
    typeof notes !== 'string'
  )
    return null
  return { places, factions, key_figures: keyFigures, notes }
}

function sameEntry(a: string | AuthoredFigureSeed, b: string | AuthoredFigureSeed): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

function sameSeed(a: BuildInPayload, b: BuildInPayload): boolean {
  return (
    a.notes === b.notes &&
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
    notes.value = ''
  }
}

async function submit() {
  error.value = null
  submitting.value = true
  try {
    await jobs.submitBuildIn(campaignId, formSeed())
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
      <div class="authored">
        <h2>Authored key figures</h2>
        <p class="muted small">
          Optional. A figure here is YOUR fact — the model builds the rest of the world around
          it and your text commits verbatim. A relation naming someone who does not exist yet
          (a city, a cult) makes the build create them; the relation's type decides what
          (located in → a place). Plain lines in Key figures above stay free-form.
        </p>
        <article
          v-for="(figure, index) in figureEditors"
          :key="figure.id"
          class="figure"
        >
          <div class="figure-head">
            <span class="muted small">Authored figure {{ index + 1 }}</span>
            <button
              type="button"
              class="link"
              @click="
                figureEditors.splice(index, 1);
                editorRefs.splice(index, 1)
              "
            >
              ✕ remove figure
            </button>
          </div>
          <CharacterSheetEditor
            :ref="(el) => (editorRefs[index] = el as InstanceType<typeof CharacterSheetEditor>)"
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
      <p v-if="error" class="error">{{ error }}</p>
      <button type="submit" :disabled="submitting || inFlight || !hasContent">
        {{ submitting ? 'Enqueuing…' : inFlight ? 'Building…' : 'Build my world' }}
      </button>
      <p v-if="inFlight" class="muted small">
        Still building — your seed text stays here, and a failed build keeps it for the retry.
      </p>
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
          <template v-if="job.state === 'succeeded' && job.result">
            <dt>World at build</dt>
            <dd v-if="contextLabel(job.result)">{{ contextLabel(job.result) }}</dd>
            <dt v-if="mergeLines(job.result).length > 0">What changed</dt>
            <dd v-if="mergeLines(job.result).length > 0">
              <span v-for="line in mergeLines(job.result)" :key="line">{{ line }}<br /></span>
              <span class="muted small"
                >Places and factions merge into the existing world — nothing is duplicated.
                Characters never merge: every build commits them as new entries (regenerate or
                edit an existing character to change it).</span
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
</style>
