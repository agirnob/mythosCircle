<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ApiError, apiFetch } from '../api/client'
import type { components } from '../api/schema'
import type { JournalDraft, JournalEntry, PlaySession } from '../api/journal'
import { requestKey } from '../api/journal'
import { sessionGeneration, SessionChangedError } from '../api/session'
import JournalComposer from '../components/ui/JournalComposer.vue'
import JournalEntries from '../components/ui/JournalEntries.vue'
import TonightFeed from '../components/ui/TonightFeed.vue'
import TonightNotes from '../components/ui/TonightNotes.vue'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useTonightStore } from '../stores/tonight'
import { useWorldStore } from '../stores/world'

const route = useRoute()
const campaignId = computed(() => route.params.id as string)
const tonight = useTonightStore()
const world = useWorldStore()
const campaigns = useCampaignsStore()
const auth = useAuthStore()
const selectedEntity = ref('')
const noteEditorIds = ref<string[]>([])
watch(
  selectedEntity,
  (id) => {
    if (id && !noteEditorIds.value.includes(id)) noteEditorIds.value.push(id)
  },
  { flush: 'sync' },
)
const recoveryTarget = ref('')
const recovered = ref('')
const recoveryStatus = ref('')
const recoveryBusy = ref(false)
const legacyKey = computed(() =>
  auth.account?.id && campaignId.value
    ? `mythoscircle:tonight:${auth.account.id}:${campaignId.value}`
    : '',
)
const entities = computed(() => world.entry(campaignId.value).world?.entities ?? [])
let recoveryVersion = 0
onBeforeUnmount(() => {
  recoveryVersion++
})
watch(
  legacyKey,
  (key) => {
    recoveryVersion++
    selectedEntity.value = ''
    noteEditorIds.value = []
    recoveryTarget.value = ''
    recovered.value = ''
    recoveryStatus.value = ''
    recoveryBusy.value = false
    if (!key) return
    try {
      recovered.value = globalThis.localStorage.getItem(key) ?? ''
    } catch {
      recoveryStatus.value = 'Could not read recovered browser notes.'
    }
  },
  { immediate: true, flush: 'sync' },
)
async function recoverNotes() {
  const key = legacyKey.value
  const text = recovered.value
  const campaign = campaignId.value
  const target = recoveryTarget.value
  const generation = sessionGeneration()
  const version = recoveryVersion
  if (!key || !text || !target || recoveryBusy.value) return
  const active = () =>
    key === legacyKey.value && version === recoveryVersion && generation === sessionGeneration()
  recoveryBusy.value = true
  recoveryStatus.value = ''
  try {
    const source = globalThis.localStorage.getItem(key) ?? ''
    if (source !== text) {
      recovered.value = source
      recoveryStatus.value =
        'Browser notes changed or were removed. Review the current browser notes before appending.'
      return
    }
    if (Array.from(text).length > 20000) {
      recoveryStatus.value =
        'Recovered browser notes exceed 20,000 characters. They are kept here and can be copied; shorten them before saving.'
      return
    }
    const run = await apiFetch<components['schemas']['RunStateResponse']>(
      `/api/campaigns/${encodeURIComponent(campaign)}/run-state`,
    )
    if (!active()) return
    const value = run.session[target]?.notes
    const previous = typeof value === 'string' ? value : ''
    const alreadySaved = previous === text || previous.endsWith(`\n\n${text}`)
    const combined = previous ? `${previous}\n\n${text}` : text
    if (!alreadySaved && Array.from(combined).length > 20000)
      throw new Error(
        'Combined notes exceed 20,000 characters. Shorten the saved entity notes before appending.',
      )
    const result = alreadySaved
      ? await tonight.refreshProjections(campaign)
      : await tonight.saveNotes(campaign, target, combined, previous)
    if (!active()) return
    selectedEntity.value = target
    try {
      if (globalThis.localStorage.getItem(key) === text) {
        globalThis.localStorage.removeItem(key)
        recovered.value = ''
      } else {
        recovered.value = globalThis.localStorage.getItem(key) ?? ''
      }
      recoveryStatus.value = result.refreshed
        ? 'Recovered notes saved to the entity.'
        : 'Recovered notes saved to the entity. Could not refresh the page; reload to see them.'
    } catch {
      recoveryStatus.value =
        'Recovered notes saved to the entity. Could not remove the browser copy.'
    }
  } catch (error) {
    if (!active()) return
    recoveryStatus.value =
      error instanceof ApiError && error.status === 409
        ? 'Entity notes changed. Browser notes are kept; review the entity and retry appending.'
        : error instanceof Error
          ? `Recovery not saved. ${error.message}`
          : 'Recovery not saved. Try again.'
  } finally {
    if (active()) recoveryBusy.value = false
  }
}

const entry = computed(() => tonight.entry(campaignId.value))
const tab = ref<'session' | 'timeline' | 'changes'>('session')
const tabs = ['session', 'timeline', 'changes'] as const
const selectedSession = ref('')
const selected = computed(() =>
  entry.value.sessions.find((session) => session.id === selectedSession.value),
)
const sessionRows = computed(
  () =>
    entry.value.journal[tonight.journalKey({ sessionId: selectedSession.value })]?.entries ?? [],
)
const timelinePage = computed(
  () => entry.value.journal[tonight.journalKey()] ?? { entries: [], nextCursor: null },
)
const timelineGroups = computed(() =>
  [...entry.value.sessions]
    .sort((a, b) => a.play_date.localeCompare(b.play_date) || a.sequence - b.sequence)
    .map((session) => ({
      session,
      entries: timelinePage.value.entries.filter((row) => row.session_id === session.id),
    }))
    .filter((group) => group.entries.length),
)
const liveEntityIds = computed(() => entities.value.map((entity) => entity.id))
const status = ref('')
const journalBusy = ref(false)
const composer = ref<{
  session: PlaySession
  entry?: JournalEntry
  initial?: JournalDraft
  position?: number
  sourceEventId?: string
  requestKey: string
} | null>(null)
const composerError = ref('')
const sessionForm = ref(false)
const editingSession = ref<PlaySession | null>(null)
const sessionTitle = ref('')
const sessionDate = ref(new Date().toISOString().slice(0, 10))
const sessionRequestKey = ref('')
const sessionBusy = ref(false)
const sessionError = ref('')
let initiatingControl: globalThis.HTMLElement | null = null
let scopeVersion = 0
const scope = computed(() => `${auth.account?.id ?? ''}:${campaignId.value}`)
watch(
  scope,
  () => {
    scopeVersion++
    composer.value = null
    composerError.value = ''
    journalBusy.value = false
    selectedSession.value = ''
    status.value = ''
    sessionForm.value = false
    sessionBusy.value = false
    sessionError.value = ''
    tab.value = 'session'
  },
  { flush: 'sync' },
)
onBeforeUnmount(() => {
  scopeVersion++
})
watch(
  campaignId,
  (id) => {
    void world.load(id).catch(() => {})
    void tonight.fetchKinds(id)
    void tonight.load(id).catch(() => {})
    if (!campaigns.current || campaigns.current.id !== id)
      void campaigns.fetchOne(id).catch(() => {})
  },
  { immediate: true },
)
watch(
  () => entry.value.sessions,
  (sessions) => {
    if (!sessions.some((row) => row.id === selectedSession.value)) {
      const requested = typeof route.query.session === 'string' ? route.query.session : ''
      selectedSession.value = sessions.some((row) => row.id === requested)
        ? requested
        : (entry.value.activeSessionId ?? sessions.at(-1)?.id ?? '')
    }
  },
  { immediate: true },
)
watch(
  () => route.query.session,
  (requested) => {
    if (
      typeof requested === 'string' &&
      entry.value.sessions.some((session) => session.id === requested)
    )
      selectedSession.value = requested
  },
  { flush: 'sync' },
)
watch(
  () => [selectedSession.value, route.query.entry, route.query.session] as const,
  async ([id, requestedEntry, requestedSession]) => {
    if (!id) return
    const version = scopeVersion
    const targetId =
      requestedSession === id && typeof requestedEntry === 'string' ? requestedEntry : undefined
    await tonight.fetchJournal(campaignId.value, { sessionId: id }, false, targetId)
    if (
      version !== scopeVersion ||
      selectedSession.value !== id ||
      route.query.entry !== requestedEntry ||
      route.query.session !== requestedSession
    )
      return
    if (targetId) tab.value = 'session'
    if (targetId)
      void nextTick(() => {
        const target = globalThis.document.getElementById(`session-story-${targetId}`)
        target?.focus()
        target?.scrollIntoView?.({ block: 'center' })
      })
  },
  { immediate: true },
)
function switchTab(value: typeof tab.value) {
  tab.value = value
  if (value === 'timeline') void tonight.fetchJournal(campaignId.value)
}
function openSession(sessionId: string) {
  selectedSession.value = sessionId
  switchTab('session')
}
function retryJournal() {
  void tonight.fetchJournal(
    campaignId.value,
    tab.value === 'timeline' ? {} : { sessionId: selectedSession.value },
  )
}
function tabKey(event: globalThis.KeyboardEvent, index: number) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? 2
        : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3
  switchTab(tabs[next]!)
  globalThis.document.getElementById(`tonight-tab-${tabs[next]}`)?.focus()
}
function beginSession(session?: PlaySession) {
  editingSession.value = session ?? null
  sessionTitle.value = session?.title ?? ''
  sessionDate.value = session?.play_date ?? new Date().toISOString().slice(0, 10)
  sessionRequestKey.value = requestKey()
  sessionError.value = ''
  sessionForm.value = true
  void nextTick(() => globalThis.document.getElementById('play-session-title')?.focus())
}
async function saveSession() {
  if (sessionBusy.value || !sessionTitle.value.trim()) return
  const version = scopeVersion,
    campaign = campaignId.value
  sessionBusy.value = true
  sessionError.value = ''
  try {
    const result = editingSession.value
      ? await tonight.updateSession(
          campaign,
          editingSession.value,
          sessionTitle.value,
          sessionDate.value,
        )
      : await tonight.createSession(
          campaign,
          sessionTitle.value,
          sessionDate.value,
          sessionRequestKey.value,
        )
    if (version !== scopeVersion) return
    selectedSession.value = result.id
    sessionForm.value = false
    status.value = 'Play session saved.'
  } catch (error) {
    if (version === scopeVersion)
      sessionError.value = error instanceof Error ? error.message : 'Could not save the session.'
  } finally {
    if (version === scopeVersion) sessionBusy.value = false
  }
}
async function activate() {
  const version = scopeVersion
  try {
    await tonight.activateSession(campaignId.value, selectedSession.value)
    if (version === scopeVersion) status.value = 'Active play session changed.'
  } catch (error) {
    if (version === scopeVersion)
      status.value = error instanceof Error ? error.message : 'Could not activate the session.'
  }
}
function openComposer(
  row?: JournalEntry,
  initial?: JournalDraft,
  position?: number,
  sourceEventId?: string,
) {
  if (composer.value) {
    status.value =
      'Your current event draft is kept. Save it or Cancel before starting another event.'
    void nextTick(() => globalThis.document.getElementById('journal-headline')?.focus())
    return
  }
  const session = row
    ? entry.value.sessions.find((item) => item.id === row.session_id)
    : selected.value
  if (!session) {
    status.value = 'Choose or create a play session before recording an event.'
    return
  }
  initiatingControl = globalThis.document.activeElement as globalThis.HTMLElement | null
  composerError.value = ''
  composer.value = {
    session,
    entry: row,
    initial,
    position,
    sourceEventId,
    requestKey: requestKey(),
  }
}
function cancelComposer() {
  composer.value = null
  composerError.value = ''
  initiatingControl?.focus()
}
function closeNoteEditor(entityId: string) {
  noteEditorIds.value = noteEditorIds.value.filter((id) => id !== entityId)
  if (selectedEntity.value === entityId) selectedEntity.value = ''
  void nextTick(() => globalThis.document.getElementById('notes-entity')?.focus())
}
async function saveEntry(draft: JournalDraft) {
  const current = composer.value
  if (!current || journalBusy.value) return
  const version = scopeVersion,
    campaign = campaignId.value
  journalBusy.value = true
  composerError.value = ''
  try {
    const saved = await tonight.saveJournal(
      campaign,
      current.session.id,
      draft,
      current.requestKey,
      current.entry,
      { position: current.position, source_event_id: current.sourceEventId },
    )
    if (version !== scopeVersion || composer.value !== current) return
    composer.value = null
    status.value = 'Event saved.'
    // Projection refresh cannot turn a confirmed save into a failed save.
    const refreshed = await tonight.fetchJournal(
      campaign,
      tab.value === 'timeline' ? {} : { sessionId: current.session.id },
      false,
      saved.id,
    )
    if (version !== scopeVersion) return
    if (!refreshed) status.value = 'Event saved. Could not refresh the journal; reload to see it.'
    void nextTick(() =>
      globalThis.document
        .getElementById(`${tab.value === 'timeline' ? 'timeline' : 'session'}-story-${saved.id}`)
        ?.focus(),
    )
  } catch (error) {
    if (version !== scopeVersion || error instanceof SessionChangedError) return
    composerError.value =
      error instanceof ApiError && error.status === 409
        ? 'This event changed. Your draft is kept. Cancel and reopen the current event to review it.'
        : error instanceof Error
          ? error.message
          : 'Event not saved. Your draft is kept.'
  } finally {
    if (version === scopeVersion) journalBusy.value = false
  }
}
async function mutateEntry(
  row: JournalEntry,
  action: 'remove' | 'correct' | 'move',
  position?: number,
) {
  if (journalBusy.value) return
  const version = scopeVersion,
    campaign = campaignId.value
  journalBusy.value = true
  try {
    if (action === 'remove') await tonight.removeJournal(campaign, row)
    else if (action === 'correct') await tonight.correctJournal(campaign, row)
    else
      await tonight.saveJournal(
        campaign,
        row.session_id,
        { headline: row.headline, context: row.context, references: row.references },
        requestKey(),
        row,
        { position },
      )
    if (version !== scopeVersion) return
    status.value =
      action === 'correct'
        ? 'Action taken back. Story correction saved.'
        : action === 'remove'
          ? 'Event removed from the story.'
          : 'Story order changed.'
    await tonight.fetchJournal(
      campaign,
      tab.value === 'timeline' ? {} : { sessionId: row.session_id },
      false,
      action === 'remove' ? undefined : row.id,
    )
  } catch (error) {
    if (version === scopeVersion)
      status.value = error instanceof Error ? error.message : 'Could not save this change.'
  } finally {
    if (version === scopeVersion) journalBusy.value = false
  }
}
function copyNotes(entityId: string, name: string, notes: string) {
  openComposer(undefined, {
    headline: `Notes about ${name}`,
    context: notes,
    references: [{ entity_id: entityId, label: name, field: null, start: null, end: null }],
  })
}
function promote(eventId: string, names: string[], details: string[]) {
  switchTab('session')
  openComposer(
    undefined,
    {
      headline: Array.from(`${names.join(', ') || 'World'} — ${details[0] ?? 'Historical event'}`)
        .slice(0, 180)
        .join(''),
      context: details.slice(1).join('\n'),
      references: [],
    },
    undefined,
    eventId,
  )
}
const stateful = computed(() => {
  const run = entry.value.runState
  if (!run) return []
  return entities.value.flatMap((entity) => {
    const image = run.session[entity.id] ?? {},
      knowledge = run.knowledge[entity.id] ?? {}
    const notes = typeof image.notes === 'string' ? image.notes : ''
    const session = Object.entries(image).filter(([key]) => key !== 'notes')
    if (!notes && !session.length && !Object.keys(knowledge).length) return []
    return [
      {
        id: entity.id,
        name: entity.name,
        notes,
        kind: entity.kind,
        session,
        knowledge: Object.entries(knowledge),
      },
    ]
  })
})
</script>

<template>
  <div class="mc-tonight">
    <header class="mc-page-header">
      <div>
        <p v-if="campaigns.current?.id === campaignId" class="mc-eyebrow">
          {{ campaigns.current.title }}
        </p>
        <h1 class="mc-page-title">Tonight</h1>
        <p class="mc-page-description">
          {{
            tab === 'session'
              ? 'The story of your session, as it happened.'
              : tab === 'timeline'
                ? 'Your world’s story, across every play session.'
                : 'What you added, edited, and corrected in the world.'
          }}
        </p>
      </div>
      <p class="mc-page-actions">
        <RouterLink :to="{ name: 'overview', params: { id: campaignId } }" class="mc-link"
          >← Back to the world</RouterLink
        >
      </p>
    </header>
    <div v-if="tab === 'session'" class="mc-session-tools">
      <label for="play-session-select" class="mc-sr-only">Play session</label
      ><select id="play-session-select" v-model="selectedSession">
        <option value="">Choose a play session</option>
        <option v-for="session in entry.sessions" :key="session.id" :value="session.id">
          Session {{ session.sequence }} — {{ session.title }}
        </option>
      </select>
      <button type="button" class="mc-btn mc-btn-secondary" @click="beginSession()">
        New session</button
      ><button
        v-if="selected"
        type="button"
        class="mc-btn mc-btn-secondary"
        @click="beginSession(selected)"
      >
        Edit session</button
      ><button
        type="button"
        class="mc-btn mc-btn-primary"
        :disabled="!selected || journalBusy"
        @click="openComposer()"
      >
        ＋ Record event
      </button>
      <span v-if="selected && entry.activeSessionId === selected.id" class="mc-active-session"
        >Active play session</span
      ><button v-else-if="selected" type="button" class="mc-text-button" @click="activate">
        Use as active session
      </button>
    </div>
    <form v-if="sessionForm" class="mc-session-form" @submit.prevent="saveSession">
      <h2>{{ editingSession ? 'Edit play session' : 'Create a play session' }}</h2>
      <label for="play-session-title">Session name</label
      ><input
        id="play-session-title"
        v-model="sessionTitle"
        required
        maxlength="180"
        :disabled="sessionBusy"
      /><label for="play-session-date">Play date</label
      ><input
        id="play-session-date"
        v-model="sessionDate"
        type="date"
        required
        :disabled="sessionBusy"
      />
      <p v-if="sessionError" role="alert">{{ sessionError }}</p>
      <div class="mc-form-actions">
        <button
          type="button"
          class="mc-btn mc-btn-secondary"
          :disabled="sessionBusy"
          @click="sessionForm = false"
        >
          Cancel</button
        ><button
          type="submit"
          class="mc-btn mc-btn-primary"
          :disabled="sessionBusy || !sessionTitle.trim()"
        >
          Save session
        </button>
      </div>
    </form>
    <div class="mc-tonight-tabs" role="tablist" aria-label="Tonight views">
      <button
        v-for="(name, index) in tabs"
        :id="`tonight-tab-${name}`"
        :key="name"
        type="button"
        role="tab"
        :aria-selected="tab === name"
        :aria-controls="`tonight-panel-${name}`"
        :tabindex="tab === name ? 0 : -1"
        @click="switchTab(name)"
        @keydown="tabKey($event, index)"
      >
        {{ name === 'timeline' ? 'World timeline' : name === 'session' ? 'Session' : 'Changes' }}
      </button>
    </div>
    <p v-if="status" role="status">{{ status }}</p>
    <p v-if="entry.sessionsError" role="alert">
      {{ entry.sessionsError }}
      <button type="button" class="mc-text-button" @click="tonight.fetchSessions(campaignId)">
        Retry play sessions
      </button>
    </p>
    <p v-if="entry.journalError" role="alert">
      {{ entry.journalError }}
      <button type="button" class="mc-text-button" @click="retryJournal">Retry journal</button>
    </p>
    <JournalComposer
      v-if="composer"
      :key="composer.requestKey"
      :session="composer.session"
      :entry="composer.entry"
      :initial="composer.initial"
      :entities="entities"
      :busy="journalBusy"
      :error="composerError"
      @save="saveEntry"
      @cancel="cancelComposer"
    />
    <section
      id="tonight-panel-session"
      role="tabpanel"
      aria-labelledby="tonight-tab-session"
      :hidden="tab !== 'session'"
    >
      <details class="mc-state-disclosure">
        <summary>Current state <span class="mc-muted"> · now</span></summary>
        <ul class="mc-tonight-state">
          <li v-for="row in stateful" :key="row.id" class="mc-tonight-state-row">
            <RouterLink
              :to="{ name: 'entity', params: { id: campaignId, entityId: row.id } }"
              class="mc-link"
              >{{ row.name }}</RouterLink
            ><span v-for="[key, value] in row.session" :key="key" class="mc-tonight-tag"
              >{{ key }}:
              <strong>{{
                typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value)
              }}</strong></span
            ><span v-for="[field, known] in row.knowledge" :key="field" class="mc-tonight-tag"
              >{{ field }}: {{ known ? 'known by party' : 'secret' }}</span
            >
          </li>
        </ul>
        <p class="mc-muted">Current consequences and knowledge. The story is recorded below.</p>
      </details>
      <div v-if="selected" class="mc-journal-heading">
        <h2>Session journal · {{ sessionRows.length }} events</h2>
        <span>{{ selected.play_date }}</span>
      </div>
      <JournalEntries
        id-prefix="session-story"
        :entries="sessionRows"
        :campaign-id="campaignId"
        :live-entity-ids="liveEntityIds"
        :busy="journalBusy"
        @edit="openComposer($event)"
        @insert="(row, position) => openComposer(undefined, undefined, position)"
        @move="(row, position) => mutateEntry(row, 'move', position)"
        @remove="mutateEntry($event, 'remove')"
        @correct="mutateEntry($event, 'correct')"
      />
      <div v-if="!sessionRows.length" class="mc-journal-empty">
        <h3>{{ selected ? 'A fresh page for this session' : 'Name your next play session' }}</h3>
        <p>
          {{
            selected
              ? 'Record the moments you want to remember.'
              : 'Create a named session to start your story journal.'
          }}
        </p>
        <button
          type="button"
          class="mc-btn mc-btn-primary"
          @click="selected ? openComposer() : beginSession()"
        >
          {{ selected ? 'Record event' : 'Create session' }}
        </button>
      </div>
      <div v-if="sessionRows.length" class="mc-journal-foot">
        <span>Story order stays separate from edit time.</span
        ><button type="button" class="mc-text-button" @click="openComposer()">
          ＋ Record event
        </button>
      </div>
      <button
        v-if="entry.journal[tonight.journalKey({ sessionId: selectedSession })]?.nextCursor"
        type="button"
        class="mc-text-button"
        @click="tonight.fetchJournal(campaignId, { sessionId: selectedSession }, true)"
      >
        Load more story events
      </button>
      <details class="mc-reference-disclosure">
        <summary>
          Reference notes <span class="mc-muted"> · kept separately from the story</span>
        </summary>
        <section v-if="recovered" class="mc-legacy-recovery">
          <p>Recovered notes from this browser</p>
          <pre class="mc-notes-prose">{{ recovered }}</pre>
          <button
            type="button"
            class="mc-text-button"
            :disabled="!selected"
            @click="
              openComposer(undefined, {
                headline: 'Recovered session notes',
                context: recovered,
                references: [],
              })
            "
          >
            Copy to journal
          </button>
          <div class="mc-notes-picker">
            <label for="recovery-target">Append to entity</label
            ><select id="recovery-target" v-model="recoveryTarget" :disabled="recoveryBusy">
              <option value="">Choose an entity</option>
              <option v-for="entity in entities" :key="entity.id" :value="entity.id">
                {{ entity.name }}
              </option>
            </select>
          </div>
          <button
            type="button"
            class="mc-btn mc-btn-secondary"
            :disabled="!recoveryTarget || recoveryBusy"
            @click="recoverNotes"
          >
            {{ recoveryBusy ? 'Saving…' : 'Append recovered notes' }}
          </button>
        </section>
        <p v-if="recoveryStatus" role="status">{{ recoveryStatus }}</p>
        <div v-if="entry.runState && entities.length" class="mc-notes-picker">
          <label for="notes-entity">Edit notes for an entity</label
          ><select id="notes-entity" v-model="selectedEntity">
            <option value="">Choose an entity</option>
            <option v-for="entity in entities" :key="entity.id" :value="entity.id">
              {{ entity.name }}
            </option>
          </select>
        </div>
        <TonightNotes
          v-for="noteEntity in entities.filter((item) => noteEditorIds.includes(item.id))"
          v-show="noteEntity.id === selectedEntity"
          :key="noteEntity.id"
          :campaign-id="campaignId"
          :entity-id="noteEntity.id"
          :entity-name="noteEntity.name"
          :saved-text="
            typeof entry.runState?.session[noteEntity.id]?.notes === 'string'
              ? (entry.runState.session[noteEntity.id]!.notes as string)
              : ''
          "
          @cancel="closeNoteEditor(noteEntity.id)"
        />
        <div
          v-for="row in stateful.filter((item) => item.notes)"
          :key="row.id"
          class="mc-reference-note"
        >
          <h3>{{ row.name }}</h3>
          <pre class="mc-notes-prose">{{ row.notes }}</pre>
          <button
            type="button"
            class="mc-text-button"
            :disabled="!selected"
            @click="copyNotes(row.id, row.name, row.notes)"
          >
            Copy to journal
          </button>
        </div>
      </details>
      <p v-if="!entry.runState && !entry.revisions" class="mc-muted">Loading Tonight…</p>
      <p v-if="entry.error" role="alert">Could not load Tonight. {{ entry.error }}</p>
    </section>
    <section
      id="tonight-panel-timeline"
      role="tabpanel"
      aria-labelledby="tonight-tab-timeline"
      :hidden="tab !== 'timeline'"
    >
      <div class="mc-journal-heading">
        <h2>World timeline</h2>
        <span
          >{{ timelineGroups.length }} sessions · {{ timelinePage.entries.length }} story
          events</span
        >
      </div>
      <section v-for="group in timelineGroups" :key="group.session.id" class="mc-timeline-group">
        <header>
          <div>
            <h3>
              <span class="mc-muted">SESSION {{ group.session.sequence }}</span>
              {{ group.session.title }}
            </h3>
            <p class="mc-muted">{{ group.session.play_date }}</p>
          </div>
          <button type="button" class="mc-text-button" @click="openSession(group.session.id)">
            Open session →
          </button>
        </header>
        <JournalEntries
          id-prefix="timeline-story"
          :entries="group.entries"
          :campaign-id="campaignId"
          :live-entity-ids="liveEntityIds"
          :busy="journalBusy"
          @edit="openComposer($event)"
          @insert="
            (row, position) => {
              selectedSession = row.session_id
              openComposer(undefined, undefined, position)
            }
          "
          @move="(row, position) => mutateEntry(row, 'move', position)"
          @remove="mutateEntry($event, 'remove')"
          @correct="mutateEntry($event, 'correct')"
        />
      </section>
      <p v-if="!timelineGroups.length" class="mc-muted">No story events recorded yet.</p>
      <button
        v-if="timelinePage.nextCursor"
        type="button"
        class="mc-text-button"
        @click="tonight.fetchJournal(campaignId, {}, true)"
      >
        Load more story events
      </button>
    </section>
    <section
      id="tonight-panel-changes"
      role="tabpanel"
      aria-labelledby="tonight-tab-changes"
      :hidden="tab !== 'changes'"
    >
      <p class="mc-muted">What changed in the world, ordered by edit time.</p>
      <p v-if="entry.changesError" role="alert">
        {{ entry.changesError }}
        <button
          type="button"
          class="mc-text-button"
          :disabled="entry.changesLoading"
          @click="tonight.moreChanges(campaignId)"
        >
          Retry older changes
        </button>
      </p>
      <TonightFeed
        :revisions="entry.revisions ?? []"
        :allow-promotion="!!selected"
        @promote="promote"
      /><button
        v-if="entry.revisionsCursor"
        type="button"
        class="mc-text-button"
        :disabled="entry.changesLoading"
        @click="tonight.moreChanges(campaignId)"
      >
        {{ entry.changesLoading ? 'Loading older changes…' : 'Load older changes' }}
      </button>
    </section>
  </div>
</template>
<style scoped>
.mc-tonight {
  min-width: 0;
}
.mc-eyebrow,
.mc-timeline-group h3,
.mc-reference-note h3 {
  overflow-wrap: anywhere;
}
.mc-timeline-group header > div {
  min-width: 0;
}
.mc-eyebrow {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--mc-text-muted);
}
.mc-session-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
  margin-bottom: 1.7rem;
}
select,
.mc-session-form input {
  background: var(--mc-surface);
  color: var(--mc-text-primary);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  padding: 0.6rem 0.75rem;
  font: inherit;
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
}
.mc-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}
.mc-active-session {
  color: var(--mc-canonical);
  font-size: 0.75rem;
}
.mc-tonight-tabs {
  display: flex;
  gap: 1.5rem;
  border-bottom: 1px solid var(--mc-border);
  margin-bottom: 1.4rem;
}
.mc-tonight-tabs button {
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--mc-text-muted);
  padding: 0 0.1rem 0.8rem;
  font: inherit;
  font-size: 0.85rem;
  cursor: pointer;
}
.mc-tonight-tabs button[aria-selected='true'] {
  border-bottom-color: var(--mc-canonical);
  color: var(--mc-text-primary);
}
.mc-state-disclosure,
.mc-reference-disclosure {
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  padding: 0.8rem 1rem;
  margin-block: 1.5rem;
  background: var(--mc-surface);
}
summary {
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--mc-text-secondary);
}
.mc-tonight-state {
  list-style: none;
  padding: 0;
  margin-top: 1rem;
}
.mc-tonight-state-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: baseline;
  margin-bottom: 0.5rem;
}
.mc-tonight-tag {
  color: var(--mc-text-muted);
  border: 1px solid var(--mc-border);
  border-radius: 0.3rem;
  padding: 0.1rem 0.4rem;
  font-size: 0.75rem;
}
.mc-journal-heading {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.8rem;
  color: var(--mc-text-muted);
  font-size: 0.75rem;
}
.mc-journal-heading h2 {
  margin: 0;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-weight: 500;
}
.mc-journal-empty {
  border: 1px dashed var(--mc-border);
  border-radius: var(--mc-radius-sm);
  text-align: center;
  padding: 3rem 1rem;
  margin-top: 1rem;
}
.mc-journal-empty h3 {
  margin: 0 0 0.5rem;
  font-size: 1.1rem;
}
.mc-journal-empty p {
  color: var(--mc-text-secondary);
}
.mc-journal-foot {
  border-top: 1px solid var(--mc-border);
  padding-top: 1rem;
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.8rem;
  font-size: 0.75rem;
  color: var(--mc-text-muted);
}
.mc-text-button {
  background: none;
  border: 0;
  padding: 0.2rem 0;
  color: var(--mc-canonical);
  font: inherit;
  font-size: 0.8rem;
  cursor: pointer;
}
.mc-text-button:disabled {
  opacity: 0.45;
  cursor: default;
}
.mc-notes-prose {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
.mc-notes-picker {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-block: 1rem;
  max-width: 28rem;
}
.mc-reference-note {
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--mc-border);
}
.mc-reference-note h3 {
  font-size: 0.9rem;
}
.mc-timeline-group {
  margin-top: 1.5rem;
}
.mc-timeline-group header {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
  border-top: 1px solid var(--mc-border);
  padding-top: 1rem;
}
.mc-timeline-group h3 {
  font-size: 1rem;
  margin: 0;
}
.mc-session-form {
  padding: 1.2rem;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  margin-bottom: 1.2rem;
}
.mc-session-form h2 {
  font-size: 1.1rem;
  margin-top: 0;
}
.mc-session-form label {
  display: block;
  margin-bottom: 0.3rem;
}
.mc-session-form input {
  width: 100%;
  margin-bottom: 0.8rem;
}
.mc-form-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
@media (max-width: 600px) {
  .mc-session-tools select {
    width: 100%;
  }
  .mc-session-tools .mc-btn-primary {
    width: 100%;
  }
  .mc-tonight-tabs {
    gap: 1rem;
  }
}
</style>
