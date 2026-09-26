<script setup lang="ts">
/**
 * World overview (rebuild §13) — campaign identity, counts, creation
 * entry points, browsable entity sections.
 *
 * Reads the same world-store snapshot + media manifest as WorldView; no new
 * backend behavior. The full record list, editors, and export stay in the
 * existing world view (linked from here) until the later rebuild stages.
 */
import { computed, onMounted, onUnmounted } from 'vue'
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
      onReconnect: () => world.requestRefetch(campaignId),
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

const activeJobs = computed(
  () => jobs.forCampaign(campaignId).filter((j) => j.state === 'queued' || j.state === 'running'),
)

function describe(entity: EntityExport): string | undefined {
  const data = entity.data as Record<string, unknown>
  return (
    asString(data['personality']) ?? asString(data['description']) ?? entity.text ?? undefined
  )
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

/** First few cards per kind — the overview browses, the world view lists all. */
function preview(list: EntityExport[]): EntityExport[] {
  return list.slice(0, 6)
}
</script>

<template>
  <div>
    <PageHeader
      :title="campaigns.current?.title ?? 'Loading world…'"
      :description="campaigns.current?.description || campaigns.current?.theme"
    >
      <template #actions>
        <RouterLink :to="{ name: 'forge', params: { id: campaignId } }" class="mc-btn">
          Ask the world…
        </RouterLink>
        <RouterLink
          :to="{ name: 'add-character', params: { id: campaignId } }"
          class="mc-btn mc-btn-secondary"
        >
          + Create
        </RouterLink>
      </template>
    </PageHeader>

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
            :to="{ name: 'forge', params: { id: campaignId } }"
            class="mc-btn mc-btn-secondary"
          >
            Ask the world…
          </RouterLink>
        </template>
      </EmptyState>

      <template v-else>
        <section v-for="[kind, list] in kinds" :key="kind">
          <SectionHeader :title="kind" :meta="`${list.length}`">
            <template #actions>
              <RouterLink
                :to="{ name: 'world', params: { id: campaignId } }"
                class="mc-link mc-muted"
              >
                View all →
              </RouterLink>
            </template>
          </SectionHeader>
          <EntityGrid>
            <EntityCard
              v-for="entity in preview(list)"
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
    </template>
    <p v-else class="mc-muted">Loading world…</p>
  </div>
</template>

<style scoped>
.mc-overview-counts {
  display: flex;
  gap: var(--mc-gap-sm);
  margin: 0 0 0.5rem;
}
</style>
