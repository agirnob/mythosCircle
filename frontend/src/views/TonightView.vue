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
import { computed, watch } from 'vue'
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
    kind: string
    session: [string, unknown][]
    knowledge: [string, boolean][]
  }[]
>(() => {
  const names = new Map((world.entry(campaignId.value).world?.entities ?? []).map((e) => [e.id, e]))
  const out: {
    id: string
    name: string
    kind: string
    session: [string, unknown][]
    knowledge: [string, boolean][]
  }[] = []
  const run = runState.value
  if (!run) return out
  for (const [entityId, image] of Object.entries(run.session)) {
    const entity = names.get(entityId)
    out.push({
      id: entityId,
      name: entity?.name ?? entityId,
      kind: entity?.kind ?? 'entity',
      session: Object.entries(image),
      knowledge: [],
    })
  }
  for (const [entityId, toggles] of Object.entries(run.knowledge)) {
    const entity = names.get(entityId)
    let row = out.find((candidate) => candidate.id === entityId)
    if (!row) {
      row = {
        id: entityId,
        name: entity?.name ?? entityId,
        kind: entity?.kind ?? 'entity',
        session: [],
        knowledge: [],
      }
      out.push(row)
    }
    row.knowledge = Object.entries(toggles)
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

    <TonightNotes :campaign-id="campaignId" />

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
