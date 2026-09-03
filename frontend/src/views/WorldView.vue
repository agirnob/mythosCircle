<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import type { components } from '../api/schema'
import StatBlock from '../components/StatBlock.vue'
import { useAuthStore } from '../stores/auth'
import { useWorldStore } from '../stores/world'
import { connectJobSocket } from '../ws'

type WorldExport = components['schemas']['WorldExport']
type EntityExport = components['schemas']['EntityExport']
type EdgeExport = components['schemas']['EdgeExport']

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string

const world = useWorldStore()

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
])

function edgeLabel(edge: EdgeExport): string {
  return COUNTER_TYPES.has(edge.type) ? `${edge.type}(${edge.counter})` : edge.type
}

interface RelationLine {
  edgeId: string
  srcName: string
  dstName: string
  label: string
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
        <p class="muted small">Read-only view — the world updates live as build-in jobs commit.</p>
        <p>
          <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }" class="cta secondary">
            Open build-in
          </RouterLink>
        </p>
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
            <h3>{{ entity.name }}</h3>
            <p v-if="entity.text" class="text">{{ entity.text }}</p>
            <p v-else class="muted">No description.</p>
            <StatBlock
              v-if="entity.data && entity.data['stat_block']"
              :block="entity.data['stat_block']"
            />
            <div v-if="relationsFor(entity.id).length > 0" class="relations">
              <h4>Relations</h4>
              <p v-for="relation in relationsFor(entity.id)" :key="relation.edgeId" class="mono">
                {{ relation.srcName }} --{{ relation.label }}--&gt; {{ relation.dstName }}
              </p>
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
.relations h4 {
  margin: 0.5rem 0 0.25rem;
  font-size: 0.85rem;
  color: #9aa0a6;
}
.relations p {
  margin: 0.1rem 0;
  font-size: 0.85rem;
}
.sync-failed {
  border: 1px solid #2c3038;
  border-left: 3px solid #ff7b72;
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
}
</style>
