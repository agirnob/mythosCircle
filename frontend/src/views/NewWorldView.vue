<script setup lang="ts">
/**
 * World overview (rebuild §13) — campaign identity, counts, creation
 * entry points, browsable entity sections.
 *
 * The overview previews each kind; the World route lists every entity and
 * offers campaign exports. Both read the same world-store snapshot.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import type { components } from '../api/schema'
import EntityCard from '../components/ui/EntityCard.vue'
import EntityGrid from '../components/ui/EntityGrid.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import ErrorState from '../components/ui/ErrorState.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import SectionHeader from '../components/ui/SectionHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import { asString } from '../components/profile/profile'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useJobsStore } from '../stores/jobs'
import { useWorldStore } from '../stores/world'
import { connectJobSocket } from '../ws'

type EntityExport = components['schemas']['EntityExport']

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string
const props = withDefaults(defineProps<{ mode?: 'overview' | 'directory' }>(), { mode: 'overview' })
const isDirectory = computed(() => props.mode === 'directory')
const search = ref('')

const world = useWorldStore()
const campaigns = useCampaignsStore()
const jobs = useJobsStore()

let disconnectSocket: (() => void) | null = null
let disposed = false

async function start() {
  await world.load(campaignId)
  if (disposed) return
  const entry = world.entry(campaignId)
  if (entry.notFound || entry.error) return
  if (!campaigns.current || campaigns.current.id !== campaignId) {
    await campaigns.fetchOne(campaignId)
  }
  void world.fetchMedia(campaignId)
  void jobs.syncList(campaignId).catch(() => {})
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

onMounted(() => {
  void start()
})
onUnmounted(() => {
  disposed = true
  disconnectSocket?.()
})

const entry = computed(() => world.entry(campaignId))
const entities = computed<EntityExport[]>(() => entry.value.world?.entities ?? [])
const edges = computed(() => entry.value.world?.edges ?? [])

const kinds = computed(() => {
  const groups = new Map<string, EntityExport[]>()
  for (const entity of entities.value) {
    const group = groups.get(entity.kind)
    if (group) group.push(entity)
    else groups.set(entity.kind, [entity])
  }
  return [...groups.entries()]
})
const directoryKinds = computed(() => {
  const term = search.value.trim().toLocaleLowerCase()
  if (!term) return kinds.value
  return kinds.value
    .map(
      ([kind, list]) =>
        [
          kind,
          list.filter((entity) =>
            [entity.name, kind, describe(entity) ?? ''].some((value) =>
              value.toLocaleLowerCase().includes(term),
            ),
          ),
        ] as [string, EntityExport[]],
    )
    .filter(([, list]) => list.length > 0)
})
const recentEntities = computed(() => entities.value.slice(-3).reverse())

const activeJobs = computed(() =>
  jobs.forCampaign(campaignId).filter((j) => j.state === 'queued' || j.state === 'running'),
)

function describe(entity: EntityExport): string | undefined {
  const data = entity.data as Record<string, unknown>
  return asString(data['personality']) ?? asString(data['description']) ?? entity.text ?? undefined
}

function metaFor(entity: EntityExport): string {
  const data = entity.data as Record<string, unknown>
  const role = asString(data['role'])
  const cls = asString(data['class'])
  const level = data['level']
  const parts = [entity.kind, role ?? cls ?? undefined]
  if (typeof level === 'number') parts.push(`Level ${level}`)
  return parts.filter(Boolean).join(' · ')
}

function relationInfo(entity: EntityExport): string {
  const count = edges.value.filter((e) => e.src === entity.id || e.dst === entity.id).length
  const location = asString((entity.data as Record<string, unknown>)['current_location'])
  const rel = `${count} relationship${count === 1 ? '' : 's'}`
  return location ? `${rel} · ${location}` : rel
}

function sectionTitle(kind: string): string {
  if (kind === 'character') return 'Characters'
  if (kind === 'place') return 'Places'
  if (kind === 'faction') return 'Factions'
  return kind
}

function worldExportUrl(format: 'markdown' | 'html'): string {
  return `/api/campaigns/${encodeURIComponent(campaignId)}/export?format=${format}`
}
</script>

<template>
  <div class="mc-world-overview" :class="{ 'mc-world-directory': isDirectory }">
    <p class="mc-eyebrow">{{ isDirectory ? 'World directory' : 'Campaign atlas' }}</p>
    <PageHeader
      :title="isDirectory ? 'World' : (campaigns.current?.title ?? 'Loading world…')"
      :description="
        isDirectory
          ? `Every person, place, and faction in ${campaigns.current?.title ?? 'this campaign'}.`
          : campaigns.current?.description || campaigns.current?.theme
      "
    >
      <template #actions>
        <a
          v-if="isDirectory"
          :href="worldExportUrl('markdown')"
          download
          class="mc-btn mc-btn-secondary"
          >Export Markdown</a
        >
        <a
          v-if="isDirectory"
          :href="worldExportUrl('html')"
          download
          class="mc-btn mc-btn-secondary"
          >Export HTML</a
        >
        <RouterLink
          v-if="!isDirectory"
          :to="{ name: 'ask', params: { id: campaignId } }"
          class="mc-btn"
        >
          Ask the world…
        </RouterLink>
        <RouterLink
          v-if="!isDirectory"
          :to="{ name: 'add-character', params: { id: campaignId } }"
          class="mc-btn mc-btn-secondary"
        >
          + Create
        </RouterLink>
      </template>
    </PageHeader>

    <ErrorState v-if="entry.error" title="Could not load this world." :message="entry.error">
      <template #actions>
        <button type="button" class="mc-btn mc-btn-secondary" @click="() => world.load(campaignId)">
          Try again
        </button>
      </template>
    </ErrorState>
    <EmptyState
      v-else-if="entry.notFound"
      title="World not found"
      body="It may have been deleted, or you may not have access to it."
    />

    <template v-else-if="entry.world">
      <p class="mc-overview-counts">
        <StatusBadge variant="canon">
          {{ entities.length }} {{ entities.length === 1 ? 'entity' : 'entities' }}
        </StatusBadge>
        <StatusBadge variant="neutral">
          {{ edges.length }} {{ edges.length === 1 ? 'relationship' : 'relationships' }}
        </StatusBadge>
        <StatusBadge v-if="activeJobs.length > 0" variant="generating">
          {{ activeJobs.length }} generating
        </StatusBadge>
      </p>

      <div class="mc-overview-layout" :class="{ 'mc-overview-layout-directory': isDirectory }">
        <div class="mc-overview-main">
          <EmptyState
            v-if="entities.length === 0"
            title="An empty world, full of possibility"
            body="Build it from your notes, or ask for the first character."
          >
            <template #actions>
              <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }" class="mc-btn">
                Guided Build
              </RouterLink>
              <RouterLink
                :to="{ name: 'ask', params: { id: campaignId } }"
                class="mc-btn mc-btn-secondary"
              >
                Ask the world…
              </RouterLink>
            </template>
          </EmptyState>

          <template v-else-if="isDirectory">
            <label class="mc-directory-search">
              <span>Find an entity</span>
              <input
                v-model="search"
                type="search"
                placeholder="Search names, kinds, or descriptions"
              />
            </label>
            <p v-if="directoryKinds.length === 0" class="mc-muted">
              No entities match “{{ search }}”.
            </p>
            <section v-for="[kind, list] in directoryKinds" :key="kind">
              <SectionHeader :title="sectionTitle(kind)" :meta="`${list.length}`">
                <template #actions>
                  <RouterLink
                    :to="{ name: 'entity-section', params: { id: campaignId, kind } }"
                    class="mc-link mc-muted"
                  >
                    Open section →
                  </RouterLink>
                </template>
              </SectionHeader>
              <EntityGrid>
                <EntityCard
                  v-for="entity in list"
                  :key="entity.id"
                  :name="entity.name"
                  :meta="metaFor(entity)"
                  :description="describe(entity)"
                  :relation-info="relationInfo(entity)"
                  :portrait-url="world.portraitSrc(campaignId, entity.id)"
                >
                  <template #primary>
                    <RouterLink
                      :to="{ name: 'entity', params: { id: campaignId, entityId: entity.id } }"
                      class="mc-link"
                    >
                      Open →
                    </RouterLink>
                  </template>
                </EntityCard>
              </EntityGrid>
            </section>
          </template>
          <template v-else>
            <section class="mc-overview-browse">
              <SectionHeader title="Explore the world" meta="Browse by kind" />
              <div class="mc-kind-grid">
                <RouterLink
                  v-for="[kind, list] in kinds"
                  :key="kind"
                  :to="{ name: 'entity-section', params: { id: campaignId, kind } }"
                  class="mc-kind-tile"
                >
                  <span class="mc-kind-count">{{ list.length }}</span>
                  <span>{{ sectionTitle(kind) }}</span>
                  <span class="mc-kind-arrow" aria-hidden="true">↗</span>
                </RouterLink>
              </div>
            </section>
            <section class="mc-overview-recent">
              <SectionHeader title="Recently added" :meta="`${recentEntities.length} shown`">
                <template #actions
                  ><RouterLink
                    :to="{ name: 'world', params: { id: campaignId } }"
                    class="mc-link mc-muted"
                    >Browse all →</RouterLink
                  ></template
                >
              </SectionHeader>
              <EntityGrid>
                <EntityCard
                  v-for="entity in recentEntities"
                  :key="entity.id"
                  :name="entity.name"
                  :meta="metaFor(entity)"
                  :description="describe(entity)"
                  :relation-info="relationInfo(entity)"
                  :portrait-url="world.portraitSrc(campaignId, entity.id)"
                >
                  <template #primary
                    ><RouterLink
                      :to="{ name: 'entity', params: { id: campaignId, entityId: entity.id } }"
                      class="mc-link"
                      >Open →</RouterLink
                    ></template
                  >
                </EntityCard>
              </EntityGrid>
            </section>
          </template>
        </div>

        <aside v-if="!isDirectory" class="mc-overview-rail" aria-label="World context">
          <section class="mc-rail-card">
            <p class="mc-rail-kicker">World pulse</p>
            <h2>At a glance</h2>
            <dl class="mc-pulse-list">
              <div>
                <dt>Entities</dt>
                <dd>{{ entities.length }}</dd>
              </div>
              <div>
                <dt>Relationships</dt>
                <dd>{{ edges.length }}</dd>
              </div>
              <div v-for="[kind, list] in kinds" :key="`pulse-${kind}`">
                <dt>{{ kind }}</dt>
                <dd>{{ list.length }}</dd>
              </div>
            </dl>
          </section>

          <section class="mc-rail-card">
            <p class="mc-rail-kicker">Keep shaping</p>
            <h2>Next move</h2>
            <nav class="mc-rail-actions" aria-label="World actions">
              <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }">
                <span>Guided build</span><span aria-hidden="true">→</span>
              </RouterLink>
              <RouterLink :to="{ name: 'ask', params: { id: campaignId } }">
                <span>Ask the world</span><span aria-hidden="true">→</span>
              </RouterLink>
              <RouterLink :to="{ name: 'graph', params: { id: campaignId } }">
                <span>Open relationship graph</span><span aria-hidden="true">→</span>
              </RouterLink>
            </nav>
          </section>

          <section class="mc-rail-card">
            <p class="mc-rail-kicker">Take it with you</p>
            <h2>Export world</h2>
            <p class="mc-export-copy">Download the complete current world as a document.</p>
            <div class="mc-export-actions">
              <a :href="worldExportUrl('markdown')" download class="mc-btn mc-btn-secondary"
                >Markdown</a
              >
              <a :href="worldExportUrl('html')" download class="mc-btn mc-btn-secondary">HTML</a>
            </div>
          </section>
        </aside>
      </div>
    </template>
    <p v-else class="mc-muted">Loading world…</p>
  </div>
</template>

<style scoped>
.mc-overview-counts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--mc-gap-sm);
  margin: 0 0 1.75rem;
}
.mc-world-overview > section {
  margin-top: 2.25rem;
}
.mc-world-overview .mc-eyebrow {
  margin: 0 0 0.5rem;
  color: var(--mc-interactive-bright);
  font-size: var(--mc-meta-size);
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.mc-overview-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(300px, 0.8fr);
  align-items: start;
  gap: 1.5rem;
}
.mc-overview-layout-directory {
  grid-template-columns: minmax(0, 1fr);
}
.mc-overview-main {
  min-width: 0;
}
.mc-overview-browse + .mc-overview-recent {
  margin-top: 2.25rem;
}
.mc-kind-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(155px, 1fr));
  gap: 0.75rem;
}
.mc-kind-tile {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: end;
  gap: 0.15rem;
  min-height: 105px;
  padding: 1rem;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  background: var(--mc-surface-raised);
  color: var(--mc-text-secondary);
  text-decoration: none;
}
.mc-kind-tile:hover,
.mc-kind-tile:focus-visible {
  border-color: var(--mc-interactive-bright);
  background: var(--mc-surface-hover);
}
.mc-kind-count {
  grid-column: 1 / -1;
  color: var(--mc-text-primary);
  font-family: var(--mc-display-font);
  font-size: 2rem;
  line-height: 1;
}
.mc-kind-arrow {
  color: var(--mc-interactive-bright);
}
.mc-directory-search {
  display: grid;
  gap: 0.4rem;
  margin-bottom: 1.75rem;
  color: var(--mc-text-secondary);
}
.mc-directory-search input {
  width: 100%;
  min-width: 0;
  padding: 0.7rem 0.8rem;
  border: 1px solid var(--mc-border-bright);
  border-radius: var(--mc-radius-sm);
  background: var(--mc-input);
  color: var(--mc-text-primary);
}
.mc-world-directory .mc-overview-main > section + section {
  margin-top: 2.5rem;
}
.mc-overview-rail {
  display: grid;
  gap: 1rem;
  position: sticky;
  top: 1.5rem;
}
.mc-rail-card {
  padding: 1.1rem;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  background:
    radial-gradient(circle at 100% 0%, rgba(139, 108, 255, 0.14), transparent 13rem),
    var(--mc-surface);
}
.mc-rail-kicker {
  margin: 0 0 0.4rem;
  color: var(--mc-interactive-bright);
  font-size: var(--mc-meta-size);
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}
.mc-rail-card h2 {
  margin: 0;
  font-family: var(--mc-display-font);
  font-size: 1.35rem;
}
.mc-export-copy {
  margin: 0.55rem 0 0.9rem;
  color: var(--mc-text-secondary);
}
.mc-export-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.mc-pulse-list {
  display: grid;
  gap: 0.65rem;
  margin: 1rem 0 0;
}
.mc-pulse-list div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  padding-bottom: 0.55rem;
  border-bottom: 1px solid var(--mc-border);
}
.mc-pulse-list dt {
  color: var(--mc-text-secondary);
  text-transform: capitalize;
}
.mc-pulse-list dd {
  margin: 0;
  color: var(--mc-text-primary);
  font-weight: 700;
}
.mc-rail-actions {
  display: grid;
  gap: 0.35rem;
  margin-top: 1rem;
}
.mc-rail-actions a {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.65rem 0.7rem;
  border: 1px solid transparent;
  border-radius: var(--mc-radius-sm);
  color: var(--mc-text-secondary);
  text-decoration: none;
}
.mc-rail-actions a:hover {
  border-color: var(--mc-border);
  background: var(--mc-surface-raised);
  color: var(--mc-text-primary);
}
@media (max-width: 900px) {
  .mc-overview-layout {
    grid-template-columns: 1fr;
  }
  .mc-overview-rail {
    position: static;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 560px) {
  .mc-overview-rail {
    grid-template-columns: 1fr;
  }
}
</style>
