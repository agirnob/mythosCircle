<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import type { components } from '../api/schema'
import EntityCard from '../components/ui/EntityCard.vue'
import EntityGrid from '../components/ui/EntityGrid.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import ErrorState from '../components/ui/ErrorState.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import { asString } from '../components/profile/profile'
import { useCampaignsStore } from '../stores/campaigns'
import { useWorldStore } from '../stores/world'

type EntityExport = components['schemas']['EntityExport']
type EntityKind = 'place' | 'faction' | 'character'

const route = useRoute()
const campaignId = route.params.id as string
const requestedKind = route.params.kind as string
const validKinds: EntityKind[] = ['place', 'faction', 'character']
const kind = computed<EntityKind | null>(() =>
  validKinds.includes(requestedKind as EntityKind) ? (requestedKind as EntityKind) : null,
)

const world = useWorldStore()
const campaigns = useCampaignsStore()

onMounted(() => {
  void world.load(campaignId)
  if (!campaigns.current || campaigns.current.id !== campaignId) {
    void campaigns.fetchOne(campaignId)
  }
  void world.fetchMedia(campaignId)
})

const entry = computed(() => world.entry(campaignId))
const entities = computed<EntityExport[]>(() =>
  kind.value
    ? (entry.value.world?.entities.filter((entity) => entity.kind === kind.value) ?? [])
    : [],
)
const label = computed(() => {
  if (kind.value === 'place') return 'Places'
  if (kind.value === 'faction') return 'Factions'
  if (kind.value === 'character') return 'Characters'
  return 'Entities'
})

function describe(entity: EntityExport): string | undefined {
  const data = entity.data as Record<string, unknown>
  return asString(data['description']) ?? asString(data['personality']) ?? entity.text ?? undefined
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
  const count = entry.value.world?.edges.filter((edge) => edge.src === entity.id || edge.dst === entity.id).length ?? 0
  const location = asString((entity.data as Record<string, unknown>)['current_location'])
  const relation = `${count} relationship${count === 1 ? '' : 's'}`
  return location ? `${relation} · ${location}` : relation
}
</script>

<template>
  <div class="mc-entity-section">
    <div class="mc-section-back">
      <RouterLink :to="{ name: 'overview', params: { id: campaignId } }" class="mc-link">
        ← Campaign atlas
      </RouterLink>
    </div>

    <PageHeader
      :title="label"
      :description="campaigns.current?.title ?? 'World entities'"
    >
      <template #actions>
        <StatusBadge variant="canon">
          {{ entities.length }} {{ entities.length === 1 ? 'record' : 'records' }}
        </StatusBadge>
      </template>
    </PageHeader>

    <ErrorState v-if="entry.error" title="Could not load this section." :message="entry.error" />
    <EmptyState
      v-else-if="entry.notFound"
      title="World not found"
      body="It may have been deleted, or you may not have access to it."
    />
    <EmptyState
      v-else-if="kind === null"
      title="Section not found"
      body="Choose places, factions, or characters from the campaign atlas."
    />
    <EmptyState
      v-else-if="entry.world && entities.length === 0"
      :title="`No ${label.toLowerCase()} yet`"
      body="Build the world or add a record to start this section."
    >
      <template #actions>
        <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }" class="mc-btn">
          Guided Build
        </RouterLink>
      </template>
    </EmptyState>
    <EntityGrid v-else-if="entry.world">
      <EntityCard
        v-for="entity in entities"
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
    <p v-else class="mc-muted">Loading section…</p>
  </div>
</template>

<style scoped>
.mc-section-back {
  margin-bottom: var(--mc-gap-md);
}
</style>
