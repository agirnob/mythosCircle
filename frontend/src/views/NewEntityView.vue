<script setup lang="ts">
/**
 * Entity detail (rebuild §15) — a DM-readable character sheet, not a raw
 * property list.
 *
 * Reads the same world-store snapshot + portrait projection as the world
 * view. Editing, regeneration, and portrait generation stay in the existing
 * surfaces (linked from here) until the later rebuild stages — this view
 * never mutates world state.
 */
import { computed, onMounted } from 'vue'
import EntityStatBlock from '../components/ui/EntityStatBlock.vue'

import VueFlowGraph from '../components/graph/VueFlowGraph.vue'
import { avatarInitial, buildWorldGraph } from '../components/graph/graphModel'
import type { GraphRenderEdge, GraphRenderNode, OneHop } from '../components/graph/graphModel'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import type { components } from '../api/schema'
import EmptyState from '../components/ui/EmptyState.vue'
import ErrorState from '../components/ui/ErrorState.vue'
import SectionHeader from '../components/ui/SectionHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import { asString, edgeLabel } from '../components/profile/profile'
import { useCampaignsStore } from '../stores/campaigns'
import { useWorldStore } from '../stores/world'

type EdgeExport = components['schemas']['EdgeExport']

const route = useRoute()
const campaignId = route.params.id as string
const entityId = route.params.entityId as string

const world = useWorldStore()
const campaigns = useCampaignsStore()
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
})

const entry = computed(() => world.entry(campaignId))
const entity = computed(
  () => entry.value.world?.entities.find((e) => e.id === entityId) ?? null,
)
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
</script>

<template>
  <div>
    <p class="mc-muted">
      <RouterLink :to="{ name: 'overview', params: { id: campaignId } }" class="mc-link">
        ← {{ campaigns.current?.title ?? 'World' }}
      </RouterLink>
    </p>

    <ErrorState
      v-if="entry.error"
      title="Could not load this world."
      :message="entry.error"
    >
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
            <RouterLink :to="{ name: 'world', params: { id: campaignId } }" class="mc-btn">
              Edit
            </RouterLink>
            <RouterLink
              :to="{ name: 'forge', params: { id: campaignId } }"
              class="mc-btn mc-btn-secondary"
            >
              Regenerate
            </RouterLink>
          </p>
        </div>

      </header>
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
        <dl v-if="worldBlock.reputation || worldBlock.factions || worldBlock.location" class="mc-facts">
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
          <li v-for="edge in touching" :key="edge.id">
            <span v-if="edge.src === entityId">
              → {{ edgeLabel(edge.type, edge.counter) }} · {{ nameOf(edge.dst) }}
            </span>
            <span v-else> ← {{ edgeLabel(edge.type, edge.counter) }} · {{ nameOf(edge.src) }} </span>
          </li>
        </ul>
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
