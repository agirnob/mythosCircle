<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useWorkspaceStore, workspaceId } from '../../stores/workspace'
import type { WorkspaceNode } from '../../stores/workspace'
import { useWorldStore } from '../../stores/world'
import { useJobsStore } from '../../stores/jobs'
import { useAuthStore } from '../../stores/auth'
import PlannedEntry from './PlannedEntry.vue'
import WorkspaceGraph from './WorkspaceGraph.vue'
const props = defineProps<{ campaignId: string }>()
const emit = defineEmits<{ initial: [] }>()
const workspace = useWorkspaceStore(),
  world = useWorldStore(),
  jobs = useJobsStore()
workspace.configureOwner(props.campaignId, useAuthStore().account?.id ?? null)
const entry = computed(() => workspace.entry(props.campaignId))
const existing = computed(() => world.entry(props.campaignId).world?.entities ?? [])
const targets = computed(() => [...existing.value, ...entry.value.nodes])
const selectedId = ref<string | null>(null),
  focusId = ref<string | null>(null),
  graphOpen = ref(false)
const selected = computed(() => targets.value.find((n) => n.id === selectedId.value))
const scope = computed(() => {
  if (selected.value?.kind === 'place') return selected.value.id
  const planned = entry.value.nodes.find((n) => n.id === selectedId.value)
  if (planned) return planned.parent_id ?? null
  const placement = world
    .entry(props.campaignId)
    .world?.edges.find(
      (e) => e.src === selectedId.value && ['located_in', 'bases_at'].includes(e.type),
    )
  return placement?.dst ?? null
})
const branch = computed(() =>
  entry.value.nodes.filter((n) => n.parent_id === scope.value && n.id !== selectedId.value),
)
const plannedSelection = computed(() => entry.value.nodes.find((n) => n.id === selectedId.value))
const liveChildren = computed(() => {
  const edges = world.entry(props.campaignId).world?.edges ?? []
  const ids = new Set(
    edges
      .filter(
        (e) => e.dst === selectedId.value && ['located_in', 'part_of', 'bases_at'].includes(e.type),
      )
      .map((e) => e.src),
  )
  return existing.value.filter((e) => ids.has(e.id))
})
const activeJob = computed(() =>
  jobs
    .forCampaign(props.campaignId)
    .find(
      (j) =>
        ['expand_world', 'plan_expansion'].includes(j.kind) &&
        ['queued', 'running'].includes(j.state),
    ),
)
const lastJob = computed(() =>
  jobs
    .forCampaign(props.campaignId)
    .find((j) => ['expand_world', 'plan_expansion'].includes(j.kind)),
)
const guidance = ref(''),
  bulkCounts = ref({ place: 0, faction: 0, character: 0 }),
  bulkNames = ref({ place: '', faction: '', character: '' })
const removed = ref<{ node: WorkspaceNode; index: number } | null>(null)
watch(
  () => entry.value.snapshot?.receipts,
  (receipts) => {
    if (!receipts) return
    const mapping = new Map(receipts.map((r) => [r.node_id as string, r.entity_id as string]))
    if (selectedId.value && mapping.has(selectedId.value)) {
      selectedId.value = mapping.get(selectedId.value)!
      focusId.value = null
    }
    if (!removed.value) return
    if (mapping.has(removed.value.node.id)) {
      removed.value = null
      return
    }
    const node = removed.value.node
    node.parent_id = mapping.get(node.parent_id ?? '') ?? node.parent_id
    node.faction_id = mapping.get(node.faction_id ?? '') ?? node.faction_id
  },
  { deep: true },
)
const terminalSeen = new Set<string>()
let timer: ReturnType<typeof globalThis.setInterval> | undefined
let polling = false
let mounted = true
async function sync() {
  if (polling) return
  polling = true
  try {
    await jobs.syncList(props.campaignId)
    if (!mounted) return
    const history = jobs.forCampaign(props.campaignId)
    const terminal = history.filter((job) => !['queued', 'running'].includes(job.state))
    // One authoritative refresh reconciles every historical expansion receipt.
    const expansions = terminal.filter(
      (job) => job.kind === 'expand_world' && !terminalSeen.has(job.id),
    )
    if (expansions[0] && (await workspace.reconcile(props.campaignId, expansions[0]))) {
      for (const job of expansions) terminalSeen.add(job.id)
    }
    const latestPlan = history.find((job) => job.kind === 'plan_expansion')
    if (
      mounted &&
      latestPlan &&
      !['queued', 'running'].includes(latestPlan.state) &&
      !terminalSeen.has(latestPlan.id)
    ) {
      if (await workspace.reconcile(props.campaignId, latestPlan)) terminalSeen.add(latestPlan.id)
    }
  } catch (error) {
    if (mounted)
      entry.value.error =
        error instanceof Error ? error.message : 'Could not refresh workspace status.'
  } finally {
    polling = false
  }
}
function protectUnsaved(event: globalThis.BeforeUnloadEvent) {
  if (!entry.value.dirty && !entry.value.saving) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(async () => {
  globalThis.addEventListener('beforeunload', protectUnsaved)
  await Promise.all([workspace.load(props.campaignId), world.load(props.campaignId)])
  if (!mounted) return
  selectedId.value = existing.value.find((n) => n.kind === 'place')?.id ?? null
  await sync()
  if (!mounted) return
  timer = globalThis.setInterval(() => {
    void sync()
  }, 3000)
})
onUnmounted(() => {
  mounted = false
  globalThis.removeEventListener('beforeunload', protectUnsaved)
  if (timer) globalThis.clearInterval(timer)
})
// Debounce server persistence, serialize saves, and preserve local changes on 409.
let saveTimer: ReturnType<typeof globalThis.setTimeout> | undefined
onUnmounted(() => {
  if (saveTimer) globalThis.clearTimeout(saveTimer)
  if (entry.value.dirty && !entry.value.conflict) void workspace.save(props.campaignId)
})
function changed(patch?: Partial<WorkspaceNode>, id?: string) {
  if (patch && id) {
    const node = entry.value.nodes.find((n) => n.id === id)
    if (node && patch.parent_id) {
      let parent: string | null | undefined = patch.parent_id
      const seen = new Set<string>([id])
      while (parent) {
        if (seen.has(parent)) {
          entry.value.error = 'A place cannot be moved inside itself or its descendants.'
          return
        }
        seen.add(parent)
        parent = entry.value.nodes.find((n) => n.id === parent)?.parent_id
      }
    }
    if (node) Object.assign(node, patch)
  }
  workspace.edit(props.campaignId)
  if (saveTimer) globalThis.clearTimeout(saveTimer)
  saveTimer = globalThis.setTimeout(() => {
    void workspace.save(props.campaignId)
  }, 500)
}
function add(kind: WorkspaceNode['kind'], parent: string | null = scope.value) {
  if (entry.value.nodes.filter((n) => n.kind === kind).length >= 100) {
    entry.value.error = 'Keep the plan to 100 entries per kind or fewer.'
    return
  }
  const node: WorkspaceNode = {
    id: workspaceId(),
    kind,
    name: '',
    parent_id: parent,
    faction_id: null,
    guidance: '',
  }
  entry.value.nodes.push(node)
  focusId.value = node.id
  changed()
}
function remove(id: string) {
  if (entry.value.nodes.some((n) => n.parent_id === id || n.faction_id === id)) {
    entry.value.error = 'Reassign connected entries before removing this parent.'
    return
  }
  const index = entry.value.nodes.findIndex((n) => n.id === id),
    node = entry.value.nodes[index]
  if (!node) return
  removed.value = { node: JSON.parse(JSON.stringify(node)) as WorkspaceNode, index }
  entry.value.nodes.splice(index, 1)
  if (selectedId.value === id) selectedId.value = node.parent_id ?? null
  changed()
}
function undo() {
  if (!removed.value) return
  const mapping = new Map(
    (entry.value.snapshot?.receipts ?? []).map((r) => [r.node_id as string, r.entity_id as string]),
  )
  if (mapping.has(removed.value.node.id)) {
    removed.value = null
    return
  }
  removed.value.node.parent_id =
    mapping.get(removed.value.node.parent_id ?? '') ?? removed.value.node.parent_id
  removed.value.node.faction_id =
    mapping.get(removed.value.node.faction_id ?? '') ?? removed.value.node.faction_id
  if (entry.value.nodes.filter((n) => n.kind === removed.value!.node.kind).length >= 100) {
    entry.value.error = 'Make room in this kind before restoring the removed entry.'
    return
  }
  entry.value.nodes.splice(
    Math.min(removed.value.index, entry.value.nodes.length),
    0,
    removed.value.node,
  )
  focusId.value = removed.value.node.id
  removed.value = null
  changed()
}
function bulkAdd() {
  const kinds = ['place', 'faction', 'character'] as const
  const additions = Object.fromEntries(
    kinds.map((kind) => [
      kind,
      Math.min(100, Math.max(0, Math.trunc(bulkCounts.value[kind]) || 0)) +
        bulkNames.value[kind].split(/\r?\n/).filter((n) => n.trim()).length,
    ]),
  )
  if (
    kinds.some(
      (kind) => entry.value.nodes.filter((n) => n.kind === kind).length + additions[kind]! > 100,
    )
  ) {
    entry.value.error = 'Keep the plan to 100 entries per kind or fewer.'
    return
  }
  const total = kinds.reduce(
    (sum, kind) =>
      sum +
      Math.min(100, Math.max(0, Math.trunc(bulkCounts.value[kind]) || 0)) +
      bulkNames.value[kind].split(/\r?\n/).filter((n) => n.trim()).length,
    0,
  )
  if (entry.value.nodes.length + total > 300) {
    entry.value.error = 'Keep the plan to 100 entries per kind or fewer.'
    return
  }
  for (const kind of kinds) {
    const names = bulkNames.value[kind]
      .split(/\r?\n/)
      .map((n) => n.trim())
      .filter(Boolean)
    for (let i = 0; i < Math.min(100, Math.max(0, Math.trunc(bulkCounts.value[kind]) || 0)); i++)
      names.push('')
    for (const name of names) {
      add(kind)
      entry.value.nodes.at(-1)!.name = name
    }
    bulkCounts.value[kind] = 0
    bulkNames.value[kind] = ''
  }
  changed()
}
async function submit(action: 'plan' | 'generate') {
  await workspace.submit(props.campaignId, action, guidance.value, scope.value)
}
async function cancel() {
  if (activeJob.value) {
    await jobs.cancel(activeJob.value.id)
    await sync()
  }
}
const summary = computed(() =>
  ['place', 'faction', 'character']
    .map(
      (kind) =>
        `${entry.value.nodes.filter((n) => n.kind === kind).length} ${kind === 'character' ? 'characters' : `${kind}s`}`,
    )
    .join(' · '),
)
const orderedOutline = computed(() => {
  const rows: { id: string; kind: string; name: string; planned: boolean; depth: number }[] = []
  const visit = (parent: string | null, depth: number, visited: Set<string>) => {
    for (const n of entry.value.nodes.filter((n) => n.parent_id === parent && !visited.has(n.id))) {
      rows.push({ ...n, planned: true, depth })
      visited.add(n.id)
      visit(n.id, depth + 1, visited)
    }
  }
  const visited = new Set<string>()
  for (const n of existing.value) {
    rows.push({ ...n, planned: false, depth: 0 })
    visit(n.id, 1, visited)
  }
  visit(null, 0, visited)
  for (const n of entry.value.nodes.filter((n) => !visited.has(n.id)))
    rows.push({ ...n, planned: true, depth: 0 })
  return rows
})
</script>
<template>
  <section class="world-workspace" aria-label="World workspace">
    <p v-if="entry.loading && !entry.snapshot" role="status">Loading your workspace…</p>
    <div v-if="entry.error" role="alert" class="workspace-error">
      <p>{{ entry.error }}</p>
      <p v-if="entry.conflict">
        Your local plan is still here. Copy any unsaved guidance before reloading the server draft.
      </p>
      <button class="row-btn" @click="workspace.load(campaignId, true)">
        Reload saved workspace
      </button>
    </div>
    <div v-if="!existing.length && !entry.nodes.length && !entry.loading" class="world-empty">
      <h2>Start with a foundation</h2>
      <p>Once content exists, explore it here and plan the next additions.</p>
      <button class="mc-btn" @click="emit('initial')">Go to Guided Build</button>
    </div>
    <template v-else>
      <div class="world-toolbar">
        <div>
          <h2>Your world, as it stands</h2>
          <p class="world-summary">
            {{ existing.length }} existing entries · {{ entry.nodes.length }} planned additions ·
            {{ entry.saving ? 'Saving…' : entry.dirty ? 'Unsaved edits' : 'Saved' }}
          </p>
        </div>
        <div class="plan-submit">
          <button v-if="removed" class="row-btn" :disabled="!!activeJob" @click="undo">
            Undo removal</button
          ><button
            class="mc-btn"
            :disabled="!!activeJob || entry.saving || entry.conflict || !entry.nodes.length"
            @click="submit('generate')"
          >
            Generate planned additions</button
          ><button
            class="mc-btn mc-btn-secondary"
            :aria-expanded="graphOpen"
            @click="graphOpen = !graphOpen"
          >
            {{ graphOpen ? 'Hide graph' : 'Show graph' }}
          </button>
        </div>
      </div>
      <section class="plan-summary-bar" aria-label="Generation scope">
        <p>{{ summary }}</p>
        <p class="hint">
          Only these unfinished additions will be generated. Placement and membership shown below
          are included.
        </p>
        <details v-if="entry.nodes.length">
          <summary>Review every addition</summary>
          <ul>
            <li v-for="node in entry.nodes" :key="node.id">
              {{ node.name || 'Model chooses name' }} · {{ node.kind }} ·
              {{ targets.find((n) => n.id === node.parent_id)?.name || 'World level'
              }}<template v-if="node.faction_id">
                · Member of
                {{
                  targets.find((n) => n.id === node.faction_id)?.name || 'Unnamed faction'
                }}</template
              >
            </li>
          </ul>
        </details>
      </section>
      <p v-if="activeJob" role="status">
        {{ activeJob.kind === 'plan_expansion' ? 'Suggesting structure' : 'Generating additions' }}
        · {{ activeJob.state }} · {{ Math.round(activeJob.progress * 100) }}%
        <button class="row-btn" @click="cancel">Cancel</button>
      </p>
      <p v-else-if="lastJob?.error" role="alert">
        {{
          lastJob.kind === 'plan_expansion' ? 'Structure suggestion failed.' : 'Generation failed.'
        }}
        {{ lastJob.error }} Unfinished entries remain in your plan.
      </p>
      <p v-else-if="lastJob?.state === 'cancelled'" role="status">
        {{
          lastJob.kind === 'plan_expansion'
            ? 'Structure suggestion cancelled.'
            : 'Generation cancelled.'
        }}
        Unfinished entries remain in your plan.
      </p>
      <p
        v-else-if="lastJob?.state === 'succeeded' && lastJob.kind === 'expand_world'"
        role="status"
      >
        Confirmed additions are now in your world.
      </p>
      <div class="world-layout">
        <aside class="world-outline" aria-label="World outline">
          <details open>
            <summary><strong>Explore the world</strong></summary>
            <button
              class="outline-btn"
              :aria-current="selectedId === null"
              @click="selectedId = null"
            >
              World level</button
            ><button
              v-for="node in orderedOutline"
              :key="node.id"
              class="outline-btn"
              :class="{ 'is-planned': node.planned }"
              :style="{ paddingLeft: `${0.4 + Math.min(node.depth, 8) * 0.65}rem` }"
              :aria-current="selectedId === node.id"
              @click="selectedId = node.id"
            >
              <span class="kind-dot">{{ node.planned ? '◇' : '●' }}</span
              >{{ node.name || `Unnamed ${node.kind}`
              }}<small>{{ node.planned ? 'Planned' : 'Existing' }} {{ node.kind }}</small>
            </button>
          </details>
        </aside>
        <article class="world-focus" aria-label="Selected world entry">
          <template v-if="selected"
            ><span :class="plannedSelection ? 'planned-badge' : 'existing-badge'"
              >{{ plannedSelection ? 'Planned' : 'Existing' }} {{ selected.kind }}</span
            >
            <h2>{{ selected.name || `Unnamed ${selected.kind}` }}</h2>
            <p v-if="!plannedSelection && 'text' in selected">{{ selected.text }}</p></template
          >
          <h2 v-else>World level</h2>
          <div v-if="!selected || selected.kind === 'place'" class="planning-toolbar">
            <button
              v-for="kind in ['place', 'faction', 'character'] as const"
              :key="kind"
              class="row-btn"
              :disabled="!!activeJob || entry.nodes.filter((n) => n.kind === kind).length >= 100"
              @click="add(kind)"
            >
              + {{ kind }}
            </button>
          </div>
          <div v-if="liveChildren.length" class="entity-grid">
            <button
              v-for="node in liveChildren"
              :key="node.id"
              class="entity-tile"
              @click="selectedId = node.id"
            >
              <span class="existing-badge">Existing {{ node.kind }}</span
              ><strong>{{ node.name }}</strong>
            </button>
          </div>
          <h3 class="section-title">Planned additions</h3>
          <PlannedEntry
            v-if="plannedSelection && plannedSelection.kind !== 'place'"
            :node="plannedSelection"
            :nodes="entry.nodes"
            :targets="targets"
            :selected-id="selectedId"
            :focus-id="focusId"
            :disabled="!!activeJob"
            @change="changed"
            @select="selectedId = $event"
            @add="add"
            @remove="remove"
          />
          <PlannedEntry
            v-for="node in plannedSelection?.kind === 'place' ? [] : branch"
            :key="node.id"
            :node="node"
            :nodes="entry.nodes"
            :targets="targets"
            :selected-id="selectedId"
            :focus-id="focusId"
            :disabled="!!activeJob"
            @change="changed"
            @select="selectedId = $event"
            @add="add"
            @remove="remove"
          />
          <p v-if="!branch.length && !plannedSelection" class="hint">
            Choose a place and add the next part of your world.
          </p>
          <PlannedEntry
            v-if="plannedSelection?.kind === 'place'"
            :node="plannedSelection"
            :nodes="entry.nodes"
            :targets="targets"
            :selected-id="selectedId"
            :focus-id="focusId"
            :disabled="!!activeJob"
            @change="changed"
            @select="selectedId = $event"
            @add="add"
            @remove="remove"
          />
        </article>
        <aside class="expand-panel" aria-label="Optional planning tools">
          <p class="mc-eyebrow">Optional planning tools</p>
          <h2>Working under</h2>
          <p class="scope-name">{{ targets.find((n) => n.id === scope)?.name || 'World level' }}</p>
          <details class="plan-tools">
            <summary><strong>Ask AI for ideas</strong></summary>
            <label class="field"
              >What belongs here?<textarea v-model="guidance" rows="3" maxlength="2000" /></label
            ><button
              class="mc-btn"
              :disabled="!!activeJob || entry.conflict"
              @click="submit('plan')"
            >
              Suggest structure
            </button>
            <p class="hint">
              Suggestions are reviewed before they join your draft. They create no world content.
            </p>
          </details>
          <details class="plan-tools">
            <summary><strong>Add several at once</strong></summary>
            <template v-for="kind in ['place', 'faction', 'character'] as const" :key="kind"
              ><label class="allocation"
                >Additional {{ kind }}s<input
                  v-model.number="bulkCounts[kind]"
                  type="number"
                  min="0"
                  max="100"
                  :disabled="!!activeJob" /></label
              ><label class="field"
                >Named {{ kind }}s<textarea
                  v-model="bulkNames[kind]"
                  rows="2"
                  :disabled="!!activeJob"
                  placeholder="One per line · additional to count"
                /></label></template
            ><button class="mc-btn mc-btn-secondary" :disabled="!!activeJob" @click="bulkAdd">
              Add to plan manually
            </button>
          </details>
          <div v-if="entry.proposal" class="proposal-review">
            <h3>Suggested structure</h3>
            <ul>
              <li v-for="node in entry.proposal.draft.nodes" :key="node.id">
                {{ node.kind }} · {{ node.name || 'Model chooses name' }}
              </li>
            </ul>
            <button class="mc-btn" @click="workspace.applyProposal(campaignId)">
              Apply to draft</button
            ><button class="row-btn" @click="workspace.dismissProposal(campaignId)">Discard</button>
          </div>
          <p class="plan-help">
            Use + Place, + Faction and + Character on a location to add one entry exactly where it
            belongs.
          </p>
        </aside>
      </div>
      <WorkspaceGraph
        v-if="graphOpen"
        :existing="existing"
        :edges="world.entry(campaignId).world?.edges ?? []"
        :nodes="entry.nodes"
        :selected-id="selectedId"
        @select="selectedId = $event"
      />
    </template>
  </section>
</template>
<style src="./workspace.css"></style>
