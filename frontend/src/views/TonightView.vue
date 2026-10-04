<script setup lang="ts">
/**
 * Tonight (v3 Tier-1/2 read surface; AD-28/35) — one page for the
 * run-state and the recent-changes feed, so the overview stays a walk.
 *
 * The feed renders event lines verbatim: verb commits and their
 * take-backs BOTH read `edited` (AD-27 — the page never distinguishes
 * undo from edit, it shows what the API sends). The state summary joins
 * the run-state's entity ids to names through the world store.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ApiError, apiFetch } from '../api/client'
import type { components } from '../api/schema'
import { sessionGeneration } from '../api/session'
import { useAuthStore } from '../stores/auth'
import { RouterLink, useRoute } from 'vue-router'

import EmptyState from '../components/ui/EmptyState.vue'
import ErrorState from '../components/ui/ErrorState.vue'
import SectionHeader from '../components/ui/SectionHeader.vue'
import TonightFeed from '../components/ui/TonightFeed.vue'
import TonightNotes from '../components/ui/TonightNotes.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
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
const openedEntityIds = ref<string[]>([])
watch(
  selectedEntity,
  (id) => {
    if (id && !openedEntityIds.value.includes(id)) openedEntityIds.value.push(id)
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
    openedEntityIds.value = []
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

watch(
  campaignId,
  (id) => {
    void world.load(id).catch(() => {})
    void tonight.fetchKinds(id)
    void tonight.load(id).catch(() => {})
    if (!campaigns.current || campaigns.current.id !== id) {
      void campaigns.fetchOne(id).catch(() => {})
    }
  },
  { immediate: true },
)

const entry = computed(() => tonight.entry(campaignId.value))
const revisions = computed(() => entry.value.revisions ?? [])
const runState = computed(() => entry.value.runState)

/** Entities that currently carry session state or knowledge toggles,
 * joined to names for the summary chips. */
const stateful = computed<
  {
    id: string
    name: string
    notes: string
    kind: string
    session: [string, unknown][]
    knowledge: [string, boolean][]
  }[]
>(() => {
  const names = new Map((world.entry(campaignId.value).world?.entities ?? []).map((e) => [e.id, e]))
  const out: {
    id: string
    name: string
    notes: string
    kind: string
    session: [string, unknown][]
    knowledge: [string, boolean][]
  }[] = []
  const run = runState.value
  if (!run) return out
  for (const [entityId, image] of Object.entries(run.session)) {
    const entity = names.get(entityId)
    if (!entity) continue
    out.push({
      id: entityId,
      name: entity?.name ?? entityId,
      kind: entity.kind,
      notes: typeof image.notes === 'string' ? image.notes : '',
      session: Object.entries(image).filter(([key]) => key !== 'notes'),
      knowledge: [],
    })
  }
  for (const [entityId, toggles] of Object.entries(run.knowledge)) {
    const entity = names.get(entityId)
    if (!entity) continue
    let row = out.find((candidate) => candidate.id === entityId)
    if (!row) {
      row = {
        id: entityId,
        name: entity?.name ?? entityId,
        kind: entity.kind,
        notes: '',
        session: [],
        knowledge: [],
      }
      out.push(row)
    }
    row.knowledge = Object.entries(toggles)
  }
  for (const id of openedEntityIds.value) {
    const selected = names.get(id)
    if (selected && !out.some((row) => row.id === selected.id)) {
      out.push({
        id: selected.id,
        name: selected.name,
        kind: selected.kind,
        notes: '',
        session: [],
        knowledge: [],
      })
    }
  }
  return out
})
</script>

<template>
  <div>
    <header class="mc-page-header">
      <div>
        <h1 class="mc-page-title">Tonight</h1>
        <p v-if="campaigns.current?.id === campaignId" class="mc-page-description">
          Session state and recent changes for {{ campaigns.current.title }}.
        </p>
      </div>
      <p class="mc-page-actions">
        <RouterLink :to="{ name: 'overview', params: { id: campaignId } }" class="mc-link">
          ← Back to the world
        </RouterLink>
      </p>
    </header>

    <section v-if="recovered" class="mc-legacy-recovery">
      <p>Recovered notes from this browser</p>
      <pre class="mc-notes-prose">{{ recovered }}</pre>
      <div class="mc-notes-picker">
        <label for="recovery-target">Append to entity</label>
        <select id="recovery-target" v-model="recoveryTarget" :disabled="recoveryBusy">
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
      <label for="notes-entity">Add notes for an entity</label>
      <select id="notes-entity" v-model="selectedEntity">
        <option value="">Choose an entity</option>
        <option v-for="entity in entities" :key="entity.id" :value="entity.id">
          {{ entity.name }}
        </option>
      </select>
    </div>

    <template v-if="stateful.length > 0">
      <SectionHeader title="State" meta="now" />
      <ul class="mc-tonight-state">
        <li v-for="row in stateful" :key="row.id" class="mc-tonight-state-row">
          <StatusBadge variant="neutral">{{ row.kind }}</StatusBadge>
          <RouterLink
            :to="{ name: 'entity', params: { id: campaignId, entityId: row.id } }"
            class="mc-link"
          >
            {{ row.name }}
          </RouterLink>
          <span v-for="[key, value] in row.session" :key="key" class="mc-tonight-tag">
            {{ key }}:
            <strong>{{
              typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value)
            }}</strong>
          </span>
          <span
            v-for="[field, known] in row.knowledge"
            :key="field"
            class="mc-tonight-tag"
            :class="known ? 'mc-tonight-tag-known' : 'mc-tonight-tag-secret'"
          >
            {{ field }}: {{ known ? 'known by party' : 'secret' }}
          </span>
          <TonightNotes
            :campaign-id="campaignId"
            :entity-id="row.id"
            :entity-name="row.name"
            :saved-text="row.notes"
          />
        </li>
      </ul>
    </template>
    <template v-else-if="entry.error">
      <ErrorState title="Could not load Tonight." :message="entry.error">
        <template #actions>
          <button type="button" class="mc-btn mc-btn-secondary" @click="tonight.load(campaignId)">
            Try again
          </button>
        </template>
      </ErrorState>
    </template>
    <template v-else-if="!entry.runState && !entry.revisions">
      <p class="mc-muted">Loading Tonight…</p>
    </template>

    <SectionHeader title="Recent changes" :meta="`${revisions.length}`" />
    <EmptyState
      v-if="revisions.length === 0"
      title="No changes yet"
      body="Build, edit, or fire a consequence and it shows up here."
    />
    <TonightFeed v-else :revisions="revisions" />
  </div>
</template>

<style scoped>
.mc-notes-picker {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  max-width: 28rem;
  margin-block: 1rem;
}
.mc-notes-picker label {
  font-weight: 600;
}
.mc-notes-picker select {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 0.6rem 0.75rem;
  color: var(--mc-text-primary);
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  font: inherit;
}
.mc-notes-picker select:focus-visible {
  outline: 2px solid var(--mc-canonical);
  outline-offset: 2px;
}

.mc-notes-prose {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
.mc-legacy-recovery {
  margin-bottom: 1rem;
}
.mc-tonight-state {
  list-style: none;
  margin: 0 0 1rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--mc-gap-sm);
}
.mc-tonight-state-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
  padding: 0.45rem 0.75rem;
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
}
.mc-tonight-tag {
  font-size: var(--mc-meta-size);
  letter-spacing: 0.04em;
  color: var(--mc-text-muted);
  padding: 0.1rem 0.45rem;
  border: 1px solid var(--mc-border);
  border-radius: 999px;
}
.mc-tonight-tag-known {
  color: var(--mc-canonical);
  border-color: var(--mc-canonical);
}
.mc-tonight-tag-secret {
  color: var(--mc-text-muted);
}
</style>
