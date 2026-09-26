<script setup lang="ts">
/**
 * Entity detail (rebuild §15) — a DM-readable character sheet, not a raw
 * property list.
 *
 * Reads the same world-store snapshot + portrait projection as the world
 * view, plus the v3 Tonight projections (AD-26..29): the run-state joins
 * the session image + knowledge toggles onto the sheet, and the ONLY
 * mutations this surface owns are the Tier-2 gestures — consequence
 * verbs, knowledge flips, the record's dial (AD-36), and new relations
 * through the kinds-matrix composer (AD-31/32). Everything else (editing,
 * regeneration, portraits) stays in the existing surfaces, linked from
 * here.
 */
import { computed, onMounted, ref } from 'vue'
import EntityStatBlock from '../components/ui/EntityStatBlock.vue'

import VueFlowGraph from '../components/graph/VueFlowGraph.vue'
import { avatarInitial, buildWorldGraph } from '../components/graph/graphModel'
import type { GraphRenderEdge, GraphRenderNode, OneHop } from '../components/graph/graphModel'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import type { components } from '../api/schema'
import { ApiError } from '../api/client'
import DialPicker from '../components/ui/DialPicker.vue'
import EdgeComposer from '../components/ui/EdgeComposer.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import EntityEditor from '../components/ui/EntityEditor.vue'
import ErrorState from '../components/ui/ErrorState.vue'
import KnowledgeChip from '../components/ui/KnowledgeChip.vue'
import SectionHeader from '../components/ui/SectionHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import VerbRow from '../components/ui/VerbRow.vue'
import { asString, edgeLabel } from '../components/profile/profile'
import { useCampaignsStore } from '../stores/campaigns'
import { useTonightStore } from '../stores/tonight'
import { useWorldStore } from '../stores/world'

type EdgeExport = components['schemas']['EdgeExport']

const route = useRoute()
const campaignId = route.params.id as string
const entityId = route.params.entityId as string

const world = useWorldStore()
const campaigns = useCampaignsStore()
const tonight = useTonightStore()
const router = useRouter()

/**
 * Relationship web (rebuild §21) — the whole-world graph focused on this
 * entity: the 1-hop set renders at full opacity with edge labels, the rest
 * dims. Clicking another node opens that entity's detail page, making the
 * graph a navigation surface. Same store-only input as GraphView.
 */
const baseGraph = computed(() =>
  entry.value.world ? buildWorldGraph(entry.value.world, null) : null,
)
const focusOneHop = computed<OneHop | null>(() =>
  entry.value.world ? buildWorldGraph(entry.value.world, entityId).oneHop : null,
)
const graphNodes = computed<GraphRenderNode[]>(() =>
  (baseGraph.value?.nodes ?? []).map((node) => ({
    ...node,
    portraitUrl: node.hasPortrait ? world.portraitSrc(campaignId, node.id) : null,
    initial: avatarInitial(node.name),
  })),
)
const graphEdges = computed<GraphRenderEdge[]>(() => baseGraph.value?.edges ?? [])
const nodeNameById = computed<Record<string, string>>(() => {
  const names: Record<string, string> = {}
  for (const node of graphNodes.value) names[node.id] = node.name
  return names
})

function onGraphRefocus(id: string) {
  if (id !== entityId) {
    void router.push({ name: 'entity', params: { id: campaignId, entityId: id } })
  }
}

onMounted(() => {
  void world.load(campaignId)
  if (!campaigns.current || campaigns.current.id !== campaignId) {
    void campaigns.fetchOne(campaignId)
  }
  void world.fetchMedia(campaignId)
  // v3 Tonight (AD-34/35): registry refetch per walk mount; the run-state
  // + feed projections join the sheet.
  void tonight.fetchKinds(campaignId)
  void tonight.load(campaignId).catch(() => {})
})

const entry = computed(() => world.entry(campaignId))
const entity = computed(() => entry.value.world?.entities.find((e) => e.id === entityId) ?? null)
const data = computed(() => (entity.value?.data ?? {}) as Record<string, unknown>)

const titleMeta = computed(() => {
  const parts = [
    entity.value?.kind,
    asString(data.value['role']) ?? asString(data.value['class']) ?? undefined,
    typeof data.value['level'] === 'number' ? `Level ${data.value['level']}` : undefined,
  ].filter(Boolean)
  return parts.join(' · ')
})

const touching = computed<EdgeExport[]>(
  () => entry.value.world?.edges.filter((e) => e.src === entityId || e.dst === entityId) ?? [],
)

function nameOf(id: string): string {
  return entry.value.world?.entities.find((e) => e.id === id)?.name ?? id
}

const story = computed(() => ({
  secret: asString(data.value['secret']),
  rumor: asString(data.value['rumor']),
  hook: asString(data.value['party_hook']),
}))

const integration = computed<Record<string, unknown>>(() => {
  // Mirrors WorldView: world-integration lives in the nested
  // `world_integration` object, not top-level (spec-3.3 contract order).
  const block = data.value['world_integration']
  return typeof block === 'object' && block !== null && !Array.isArray(block)
    ? (block as Record<string, unknown>)
    : {}
})

const worldBlock = computed(() => ({
  location: asString(integration.value['current_location']),
  reputation: asString(integration.value['reputation']),
  factions: asString(integration.value['factions']),
  reaction: asString(integration.value['reaction_matrix']),
  onDefeat: asString(integration.value['on_defeat']),
  alignment: asString(data.value['alignment']),
}))

// ---------------------------------------------------------------------------
// v3 Tonight (AD-26..29, AD-32/34/36)
// ---------------------------------------------------------------------------

const tonightEntry = computed(() => tonight.entry(campaignId))

/** The entity's current session image — absent keys read as not applied. */
const sessionImage = computed<Record<string, unknown>>(
  () => tonightEntry.value.runState?.session[entityId] ?? {},
)

/** The per-secret knowledge markers (AD-29) — absent field = secret. */
const knownFields = computed<Record<string, boolean>>(
  () => tonightEntry.value.runState?.knowledge[entityId] ?? {},
)

/** The record's dial (AD-36 top-level key) — authored-or-absent. */
const dial = computed<string | null>(() => {
  const value = data.value['dial']
  return typeof value === 'string' && value !== '' ? value : null
})

/** The registry's closed dial set (AD-34 payload) — the picker's options. */
const dialLevels = computed<string[]>(() => tonight.kindsFor(campaignId)?.dial_levels ?? [])

/** Non-boolean session markers render as small facts (the AD-27 scar
 * `hp: -12`, allegiance strings, …). */
const sessionFacts = computed<[string, unknown][]>(() =>
  Object.entries(sessionImage.value).filter(([, value]) => value !== true && value !== false),
)

/** Edge-composer candidates: every other entity in the campaign. */
const edgeCandidates = computed<{ id: string; name: string; kind: string }[]>(() =>
  (entry.value.world?.entities ?? [])
    .filter((candidate) => candidate.id !== entityId)
    .map((candidate) => ({ id: candidate.id, name: candidate.name, kind: candidate.kind })),
)

const edgeBusy = ref(false)
const actionError = ref<string | null>(null)
const edgeError = ref<string | null>(null)
const editMode = ref(false)
const editBusy = ref(false)
const editError = ref<string | null>(null)

function startEdit() {
  editError.value = null
  editMode.value = true
}

function cancelEdit() {
  editError.value = null
  editMode.value = false
}

async function saveEdit(patch: Record<string, unknown>) {
  editError.value = null
  editBusy.value = true
  try {
    // AD-36/FR10: one atomic revision — the base pins the snapshot the
    // editor opened against (409 = the world moved under it).
    await world.updateEntity(campaignId, entityId, patch, entry.value.world?.revision?.id ?? null)
    editMode.value = false
  } catch (err) {
    editError.value = messageFrom(err, 'Could not save the edit.')
    if (err instanceof ApiError && err.status === 409) {
      void world.requestRefetch(campaignId)
    }
  } finally {
    editBusy.value = false
  }
}

const relationCounterEdge = ref<string | null>(null)
const relationCounterInput = ref<number>(1)

function startRelationCounter(edge: { id: string; counter: number }) {
  relationCounterEdge.value = edge.id
  relationCounterInput.value = edge.counter
}

async function saveRelationCounter(edgeId: string) {
  if (edgeBusy.value) return
  await setRelationCounter(edgeId, relationCounterInput.value)
  relationCounterEdge.value = null
}

async function deleteRelation(edgeId: string) {
  edgeError.value = null
  edgeBusy.value = true
  try {
    await world.deleteEdge(campaignId, edgeId)
  } catch (err) {
    edgeError.value = messageFrom(err, 'Could not delete that relation.')
  } finally {
    edgeBusy.value = false
  }
}

async function setRelationCounter(edgeId: string, counter: number) {
  edgeError.value = null
  edgeBusy.value = true
  try {
    await world.updateEdgeCounter(campaignId, edgeId, counter)
  } catch (err) {
    edgeError.value = messageFrom(err, 'Could not update that relation.')
  } finally {
    edgeBusy.value = false
  }
}

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

async function fireVerb(update: Record<string, unknown>) {
  actionError.value = null
  try {
    await tonight.fireVerb(campaignId, entityId, update)
  } catch (err) {
    actionError.value = messageFrom(err, 'Could not apply that consequence.')
  }
}

async function flipKnowledge(field: string) {
  actionError.value = null
  const current = knownFields.value[field] ?? false
  try {
    await tonight.toggleKnowledge(campaignId, entityId, field, !current)
  } catch (err) {
    actionError.value = messageFrom(err, 'Could not flip that knowledge marker.')
  }
}

async function setDial(level: string) {
  actionError.value = null
  try {
    // AD-36: the dial is a top-level record key — PATCH it on; the base
    // pins the snapshot the sheet rendered (409 = the world moved).
    await world.updateEntity(
      campaignId,
      entityId,
      { dial: level },
      entry.value.world?.revision?.id ?? null,
    )
  } catch (err) {
    actionError.value = messageFrom(err, 'Could not set the dial.')
  }
}

async function createEdge(edge: {
  src: string
  dst: string
  type: string
  counter: number
  reason: string
}) {
  edgeError.value = null
  edgeBusy.value = true
  try {
    await world.addEdge(campaignId, edge)
  } catch (err) {
    edgeError.value = messageFrom(err, 'Could not add that relation.')
  } finally {
    edgeBusy.value = false
  }
}
</script>

<template>
  <div>
    <p class="mc-muted">
      <RouterLink :to="{ name: 'overview', params: { id: campaignId } }" class="mc-link">
        ← {{ campaigns.current?.title ?? 'World' }}
      </RouterLink>
    </p>

    <ErrorState v-if="entry.error" title="Could not load this world." :message="entry.error">
      <template #actions>
        <button type="button" class="mc-btn mc-btn-secondary" @click="() => world.load(campaignId)">
          Try again
        </button>
      </template>
    </ErrorState>
    <EmptyState
      v-else-if="entry.notFound || (entry.world && !entity)"
      title="Character not found"
      body="It may have been deleted since you opened this page."
    />
    <template v-else-if="entity">
      <header class="mc-entity-hero">
        <img
          v-if="world.portraitSrc(campaignId, entity.id)"
          :src="world.portraitSrc(campaignId, entity.id)!"
          :alt="`Portrait of ${entity.name}`"
          class="mc-entity-hero-portrait"
        />
        <div v-else class="mc-entity-hero-portrait mc-entity-hero-fallback" aria-hidden="true">
          {{ entity.name.charAt(0).toUpperCase() }}
        </div>
        <div>
          <StatusBadge variant="canon">Canon</StatusBadge>
          <h1 class="mc-entity-hero-name">{{ entity.name }}</h1>
          <p v-if="titleMeta" class="mc-entity-hero-meta">{{ titleMeta }}</p>
          <p v-if="worldBlock.alignment" class="mc-muted">{{ worldBlock.alignment }}</p>
          <p v-if="worldBlock.location" class="mc-muted">{{ worldBlock.location }}</p>
          <p class="mc-entity-hero-actions">
            <button v-if="!editMode" type="button" class="mc-btn" @click="startEdit">Edit</button>
            <RouterLink
              :to="{ name: 'forge', params: { id: campaignId } }"
              class="mc-btn mc-btn-secondary"
            >
              Regenerate
            </RouterLink>
          </p>
        </div>
      </header>

      <template v-if="editMode">
        <SectionHeader title="Edit" meta="one revision per save" />
        <p v-if="editError" class="mc-action-error" role="alert">{{ editError }}</p>
        <EntityEditor
          :entity="entity"
          :dial-levels="dialLevels"
          :busy="editBusy"
          @save="saveEdit"
          @cancel="cancelEdit"
        />
      </template>
      <template v-else>
        <section v-if="entity.text">
          <SectionHeader title="Description" />
          <p class="mc-body">{{ entity.text }}</p>
        </section>

        <section v-if="asString(data['appearance'])">
          <SectionHeader title="Appearance" />
          <p class="mc-body">{{ asString(data['appearance']) }}</p>
        </section>

        <section v-if="asString(data['personality'])">
          <SectionHeader title="Personality" />
          <p class="mc-body">{{ asString(data['personality']) }}</p>
        </section>

        <section v-if="asString(data['background'])">
          <SectionHeader title="Background" />
          <p class="mc-body">{{ asString(data['background']) }}</p>
        </section>

        <section v-if="asString(data['goals'])">
          <SectionHeader title="Goals" />
          <p class="mc-body">{{ asString(data['goals']) }}</p>
        </section>

        <section v-if="asString(data['voice_style']) || asString(data['catchphrases'])">
          <SectionHeader title="Voice" />
          <p v-if="asString(data['voice_style'])" class="mc-body">
            {{ asString(data['voice_style']) }}
          </p>
          <p v-if="asString(data['catchphrases'])" class="mc-catchphrases">
            “{{ asString(data['catchphrases']) }}”
          </p>
        </section>

        <section v-if="story.secret || story.rumor || story.hook">
          <SectionHeader title="The story" />
          <div class="mc-story-grid">
            <article v-if="story.secret" class="mc-story-card">
              <h3>Secret</h3>
              <p>{{ story.secret }}</p>
            </article>
            <article v-if="story.rumor" class="mc-story-card">
              <h3>Rumor</h3>
              <p>{{ story.rumor }}</p>
            </article>
            <article v-if="story.hook" class="mc-story-card">
              <h3>Party hook</h3>
              <p>{{ story.hook }}</p>
            </article>
          </div>
          <p class="mc-story-chips">
            <KnowledgeChip
              v-if="story.secret"
              field="secret"
              :known="knownFields['secret'] ?? false"
              @toggle="flipKnowledge('secret')"
            />
            <KnowledgeChip
              v-if="story.rumor"
              field="rumor"
              :known="knownFields['rumor'] ?? false"
              @toggle="flipKnowledge('rumor')"
            />
            <KnowledgeChip
              v-if="story.hook"
              field="party_hook"
              :known="knownFields['party_hook'] ?? false"
              @toggle="flipKnowledge('party_hook')"
            />
          </p>
        </section>

        <section
          v-if="
            worldBlock.reputation ||
            worldBlock.factions ||
            worldBlock.location ||
            worldBlock.reaction ||
            worldBlock.onDefeat ||
            touching.length > 0
          "
        >
          <SectionHeader title="World" />
          <dl
            v-if="worldBlock.reputation || worldBlock.factions || worldBlock.location"
            class="mc-facts"
          >
            <div v-if="worldBlock.location">
              <dt>Current location</dt>
              <dd>{{ worldBlock.location }}</dd>
            </div>
            <div v-if="worldBlock.factions">
              <dt>Factions</dt>
              <dd>{{ worldBlock.factions }}</dd>
            </div>
            <div v-if="worldBlock.reputation">
              <dt>Reputation</dt>
              <dd>{{ worldBlock.reputation }}</dd>
            </div>
          </dl>
          <p v-if="worldBlock.reaction" class="mc-body">Reactions — {{ worldBlock.reaction }}</p>
          <p v-if="worldBlock.onDefeat" class="mc-body">On defeat — {{ worldBlock.onDefeat }}</p>
          <ul v-if="touching.length > 0" class="mc-rel-list">
            <li v-for="edge in touching" :key="edge.id" class="mc-rel-row">
              <span v-if="edge.src === entityId">
                → {{ edgeLabel(edge.type, edge.counter) }} · {{ nameOf(edge.dst) }}
              </span>
              <span v-else>
                ← {{ edgeLabel(edge.type, edge.counter) }} · {{ nameOf(edge.src) }}
              </span>
              <template v-if="relationCounterEdge === edge.id">
                <input
                  v-model.number="relationCounterInput"
                  type="number"
                  class="mc-rel-counter-input"
                  aria-label="Counter"
                />
                <button
                  type="button"
                  class="mc-link mc-muted"
                  :disabled="edgeBusy"
                  @click="saveRelationCounter(edge.id)"
                >
                  Save
                </button>
                <button type="button" class="mc-link mc-muted" @click="relationCounterEdge = null">
                  Cancel
                </button>
              </template>
              <button
                v-else
                type="button"
                class="mc-link mc-muted"
                :disabled="edgeBusy"
                @click="startRelationCounter(edge)"
              >
                Edit counter
              </button>
              <button
                type="button"
                class="mc-link mc-muted"
                :disabled="edgeBusy"
                @click="deleteRelation(edge.id)"
              >
                Delete
              </button>
            </li>
          </ul>
          <div class="mc-edge-composer-block">
            <h4 class="mc-edge-composer-title">Add relation</h4>
            <p v-if="edgeError" class="mc-action-error" role="alert">{{ edgeError }}</p>
            <EdgeComposer
              :kinds="tonight.kindsFor(campaignId)"
              :src-entity="{ id: entity.id, kind: entity.kind, name: entity.name }"
              :candidates="edgeCandidates"
              :busy="edgeBusy"
              @create="createEdge"
            />
          </div>
        </section>

        <section v-if="tonightEntry.runState || actionError">
          <SectionHeader title="Tonight" meta="session state" />
          <p v-if="actionError" class="mc-action-error" role="alert">{{ actionError }}</p>
          <VerbRow :session="sessionImage" @fire="fireVerb" />
          <ul v-if="sessionFacts.length > 0" class="mc-session-facts">
            <li v-for="[key, value] in sessionFacts" :key="key" class="mc-session-fact">
              <span class="mc-session-key">{{ key }}</span>
              <span class="mc-session-value">{{ String(value) }}</span>
            </li>
          </ul>
          <p v-else class="mc-muted">No consequences yet.</p>
          <p v-if="dialLevels.length > 0" class="mc-dial-line">
            <DialPicker :levels="dialLevels" :current="dial" @change="setDial" />
          </p>
        </section>

        <section v-if="graphNodes.length > 0">
          <SectionHeader title="Relationship web" :meta="`${touching.length}`">
            <template #actions>
              <RouterLink
                :to="{ name: 'graph', params: { id: campaignId }, query: { focus: entityId } }"
                class="mc-link mc-muted"
              >
                Open full graph →
              </RouterLink>
            </template>
          </SectionHeader>
          <div class="mc-mini-graph">
            <VueFlowGraph
              :nodes="graphNodes"
              :edges="graphEdges"
              :focus-id="entityId"
              :one-hop="focusOneHop"
              :labels-visible="true"
              :node-name-by-id="nodeNameById"
              @refocus="onGraphRefocus"
            />
          </div>
        </section>

        <section v-if="data['stat_block']">
          <SectionHeader title="Stat block" :meta="'5e'" />
          <EntityStatBlock :block="data['stat_block']" />
        </section>
      </template>
    </template>
    <p v-else class="mc-muted">Loading character…</p>
  </div>
</template>

<style scoped>
.mc-entity-hero {
  display: flex;
  gap: 1.5rem;
  align-items: flex-start;
  margin: 1rem 0 0.5rem;
}
.mc-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--mc-gap-sm);
  margin: 0.75rem 0;
}
.mc-facts div {
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  padding: 0.6rem 0.75rem;
}
.mc-facts dt {
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-facts dd {
  margin: 0.25rem 0 0;
  line-height: 1.5;
}
.mc-catchphrases {
  font-style: italic;
  color: var(--mc-text-secondary);
}
.mc-entity-hero-portrait {
  width: 180px;
  height: 180px;
  object-fit: cover;
  border-radius: var(--mc-radius);
  border: 1px solid var(--mc-border);
  background: var(--mc-surface);
  flex: none;
}
.mc-entity-hero-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 3rem;
  color: var(--mc-text-muted);
}
.mc-entity-hero-name {
  margin: 0.5rem 0 0;
  font-size: var(--mc-page-title-size);
}
.mc-entity-hero-meta {
  margin: 0.35rem 0 0;
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-entity-hero-actions {
  display: flex;
  gap: var(--mc-gap-sm);
  margin: 1rem 0 0;
}
.mc-body {
  font-size: var(--mc-body-size);
  line-height: 1.6;
  max-width: 65ch;
}
.mc-story-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--mc-gap);
}
.mc-story-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--mc-gap-sm);
  margin: 0.5rem 0 0;
}
.mc-action-error {
  margin: 0.5rem 0 0;
  color: var(--mc-danger);
  font-size: 0.85rem;
}
.mc-session-facts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--mc-gap-sm);
  margin: 0.5rem 0 0;
  padding: 0;
  list-style: none;
}
.mc-session-fact {
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: 999px;
  padding: 0.2rem 0.6rem;
  font-size: var(--mc-meta-size);
}
.mc-session-key {
  color: var(--mc-text-muted);
}
.mc-session-value {
  color: var(--mc-text-primary);
  margin-left: 0.3rem;
}
.mc-dial-line {
  display: flex;
  align-items: center;
  gap: var(--mc-gap-sm);
  margin: 0.75rem 0 0;
}
.mc-edge-composer-block {
  margin-top: 0.75rem;
  padding: 0.9rem 1rem;
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
}
.mc-edge-composer-title {
  margin: 0 0 0.6rem;
  font-size: 0.9rem;
  font-weight: 600;
}
.mc-story-card {
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  padding: 1rem 1.1rem;
}
.mc-story-card h3 {
  margin: 0 0 0.5rem;
  font-size: var(--mc-meta-size);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-story-card p {
  margin: 0;
  line-height: 1.55;
}
.mc-rel-list {
  list-style: none;
  padding: 0;
  margin: 0.75rem 0 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  color: var(--mc-text-secondary);
}
.mc-rel-row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
}
.mc-rel-counter-input {
  background: var(--mc-input);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  color: var(--mc-text-primary);
  width: 4rem;
  padding: 0.2rem 0.4rem;
}
.mc-link {
  background: none;
  border: none;
  color: var(--mc-interactive);
  cursor: pointer;
  padding: 0;
  font: inherit;
}
.mc-mini-graph {
  /* Definite height: the Vue Flow canvas collapses to 0px in an
   * auto-height parent (same constraint as GraphView's 64vh). */
  height: 340px;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  overflow: hidden;
  background: var(--mc-surface);
}
</style>
