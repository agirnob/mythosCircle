<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import type { components } from '../api/schema'
import { ApiError } from '../api/client'
import StatBlock from '../components/StatBlock.vue'
import { useAuthStore } from '../stores/auth'
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

/** The closed Phase-1 edge vocabulary (AD-5) — the add-relation picker. */
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

/** Spec-3.5: entity-id -> in-flight whole-character regeneration. */
const regeneratingId = ref<string | null>(null)
const regenerateErrors = ref<Record<string, string>>({})

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

/**
 * Whole-character regeneration (spec-3.5): stage a regenerate job whose
 * new proposal surfaces on the accept screen (CandidatesView). The
 * committed entity is untouched until the DM accepts the proposal.
 */
async function regenerateEntity(entityId: string) {
  regenerateErrors.value[entityId] = ''
  regeneratingId.value = entityId
  try {
    await jobs.submitRegenerate(campaignId, { kind: 'entity', id: entityId }, null)
    await jobs.syncList(campaignId)
  } catch (err) {
    regenerateErrors.value[entityId] =
      err instanceof ApiError ? err.message : 'Could not regenerate the entity.'
  } finally {
    regeneratingId.value = null
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
            </h3>
            <p v-if="entity.text" class="text">{{ entity.text }}</p>
            <p v-else class="muted">No description.</p>
            <p v-if="regenerateErrors[entity.id]" class="error">
              {{ regenerateErrors[entity.id] }}
            </p>
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
.sync-failed {
  border: 1px solid #2c3038;
  border-left: 3px solid #ff7b72;
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
}
</style>
