<script setup lang="ts">
// The three-tier relation-target picker (spec: hybrid authorship — the
// owner's round-4 ruling). ONE widget, all three tiers; the DIRECT form
// (this sprint) enforces the path-2 guardrail:
//
// - Tier 1: a searchable dropdown of committed campaign entities
//   (kind-filtered by the edge type) → `{target_id}` ULID binding.
// - Tier 2: a sibling sheet staged in the SAME submission → the
//   `{target_key}` intra-payload atomic pair.
// - Tier 3: a freeform name. On the direct form a `target_name` is a
//   schema violation (no mandate access), so the tier is a helper for
//   NAMING a target: typing a name that normalized-matches exactly one
//   committed entity surfaces the autocomplete prompt "Did you mean
//   [Name] (#ULID)?" and "Use this entity" converts the row to a
//   Tier-1 binding; a name that matches nothing stays a blocked
//   declaration (the mirror validator names the ban) — the wire never
//   carries `target_name` from this form.
import { computed, ref, watch } from 'vue'

import { EDGE_TYPES, edgeKindOk, normalizeEntityName, type EntityRef, type RelationDraft } from '../api/characters'

const props = defineProps<{
  modelValue: RelationDraft
  /** Unique radio-group name (one per row, or all rows in a sheet
   * collide). The parent passes `relation-<index>`. */
  groupName: string
  /** Committed campaign entities (id + name + kind) the Tier-1 picker
   * searches, kind-filtered by the chosen edge type. */
  entities: EntityRef[]
  /** Other sheets staged in this submission ({key, name}) — Tier 2. */
  staged: Array<{ key: string; name: string }>
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: RelationDraft): void
}>()

const draft = ref<RelationDraft>({ ...props.modelValue })
watch(
  () => props.modelValue,
  (value) => {
    draft.value = { ...value }
  },
  { immediate: true },
)

function update(patch: Partial<RelationDraft>) {
  const next = { ...draft.value, ...patch }
  draft.value = next
  emit('update:modelValue', next)
}

function shortId(id: string): string {
  return id.length > 14 ? `…${id.slice(-14)}` : id
}

// --- Tier 1: committed-entity search --------------------------------------

const searchQuery = ref('')

/** Entities the chosen edge type may run TO from this character. */
const legalEntities = computed(() =>
  props.entities.filter((entity) => edgeKindOk(draft.value.type, 'character', entity.kind)),
)

const matchingEntities = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (query === '') return legalEntities.value
  return legalEntities.value.filter((entity) => entity.name.toLowerCase().includes(query))
})

/** '' = nothing picked; otherwise the picked entity's id. */
const chosenId = ref('')
function pickTarget(id: string) {
  searchQuery.value = ''
  chosenId.value = id
  update({ tier: 'entity', targetId: id })
}

const pickedName = computed(() => {
  if (chosenId.value === '') return null
  const entity = props.entities.find((candidate) => candidate.id === chosenId.value)
  return entity ? `${entity.name} (#${shortId(entity.id)})` : null
})

function clearTarget() {
  chosenId.value = ''
  update({ tier: 'entity', targetId: '' })
}

// --- Tier 3: freeform name + the exact-match autocomplete prompt --------

const typedName = computed(() => draft.value.targetName.trim())

const exactMatches = computed(() => {
  const name = typedName.value
  if (name === '') return []
  const normalized = normalizeEntityName(name)
  if (normalized === '') return []
  return props.entities.filter((entity) => normalizeEntityName(entity.name) === normalized)
})

function useExactMatch(entity: EntityRef) {
  chosenId.value = entity.id
  update({ tier: 'entity', targetId: entity.id, targetName: '' })
}

// --- Tier 2: sibling sheets -----------------------------------------------

const stagedOptions = computed(() =>
  props.staged.filter(() => edgeKindOk(draft.value.type, 'character', 'character')),
)

defineExpose({})
</script>

<template>
  <div class="relation-target-picker">
    <div class="row-top">
      <select
        :value="draft.type"
        :aria-label="`Relation type`"
        @change="update({ type: ($event.target as HTMLSelectElement).value })"
      >
        <option v-for="edgeType in EDGE_TYPES" :key="edgeType" :value="edgeType">
          {{ edgeType }}
        </option>
      </select>
      <input
        :value="draft.counter"
        @input="update({ counter: ($event.target as HTMLInputElement).value })"
        class="counter-input"
        type="number"
        :aria-label="`Relation counter`"
        placeholder="counter (optional)"
      />
    </div>

    <div class="tier-tabs">
      <label>
        <input
          type="radio"
          :name="groupName"
          value="entity"
          :checked="draft.tier === 'entity'"
          @change="update({ tier: 'entity' })"
        />
        Committed entity
      </label>
      <label>
        <input
          type="radio"
          :name="groupName"
          value="staged"
          :checked="draft.tier === 'staged'"
          @change="update({ tier: 'staged' })"
        />
        Staged in this batch
      </label>
      <label>
        <input
          type="radio"
          :name="groupName"
          value="name"
          :checked="draft.tier === 'name'"
          @change="update({ tier: 'name' })"
        />
        Named target
      </label>
    </div>

    <div v-if="draft.tier === 'entity'" class="tier-pane">
      <p v-if="pickedName" class="muted small picked">
        {{ pickedName }}
        <button type="button" class="link" @click="clearTarget">change</button>
      </p>
      <template v-else>
        <label class="field">
          Search committed entities
          <input v-model="searchQuery" type="text" aria-label="Search committed entities" />
        </label>
        <select v-if="matchingEntities.length > 0" aria-label="Target entity" @change="pickTarget(($event.target as HTMLSelectElement).value)">
          <option value="" disabled>Pick an entity…</option>
          <option v-for="entity in matchingEntities" :key="entity.id" :value="entity.id">
            {{ entity.name }} ({{ entity.kind }} · #{{ shortId(entity.id) }})
          </option>
        </select>
        <p v-else-if="searchQuery.trim()" class="muted small">
          No {{ draft.type }} target among the committed entities matches
          “{{ searchQuery.trim() }}”.
        </p>
        <p v-else class="muted small">No entity fits this relation type yet.</p>
      </template>
    </div>

    <div v-if="draft.tier === 'staged'" class="tier-pane">
      <select v-if="stagedOptions.length > 0" aria-label="Staged target sheet" @change="update({ tier: 'staged', targetKey: ($event.target as HTMLSelectElement).value })">
        <option value="" disabled>Pick a staged character…</option>
        <option v-for="sheet in stagedOptions" :key="sheet.key" :value="sheet.key">
          {{ sheet.key }} — {{ sheet.name || '(unnamed)' }}
        </option>
      </select>
      <p v-else class="muted small">
        No other staged character fits {{ draft.type }} — add another sheet to this submission.
      </p>
    </div>

    <div v-if="draft.tier === 'name'" class="tier-pane">
      <label class="field">
        Named target
        <input
          :value="draft.targetName"
          @input="update({ targetName: ($event.target as HTMLInputElement).value })"
          type="text"
          placeholder="e.g. The Shadow Queen"
          aria-label="Named target"
        />
      </label>
      <p v-if="exactMatches.length === 1 && typedName" class="match-prompt">
        Did you mean
        <strong>{{ exactMatches[0].name }} (#{{ exactMatches[0].id }})</strong>?
        <button type="button" class="link" @click="useExactMatch(exactMatches[0])">
          Use this entity
        </button>
      </p>
      <p v-else-if="exactMatches.length > 1" class="muted small">
        “{{ typedName }}” matches {{ exactMatches.length }} committed entities — pick one by ULID
        under Committed entity.
      </p>
      <p v-else-if="typedName" class="error small">
        No committed entity matches “{{ typedName }}” — a fully-authored character has no mandate
        access: pick a committed entity above or stage it as another sheet in this batch.
      </p>
      <p v-else class="muted small">
        Pick an entity, or type a name to resolve it against the committed world.
      </p>
    </div>
  </div>
</template>

<style scoped>
.relation-target-picker {
  display: grid;
  gap: 0.35rem;
}
.row-top {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
.row-top select {
  padding: 0.3rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
}
.tier-tabs {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.85rem;
}
.tier-tabs label {
  display: flex;
  gap: 0.25rem;
  align-items: center;
}
.tier-pane {
  display: grid;
  gap: 0.25rem;
}
.field {
  display: grid;
  gap: 0.25rem;
}
.counter-input {
  width: 6rem;
  padding: 0.2rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
}
.picked {
  color: #9aa0a6;
}
.match-prompt {
  font-size: 0.85rem;
}
.error.small {
  font-size: 0.8rem;
  color: #ff8c8c;
}
.link {
  background: none;
  border: none;
  color: #8ab4ff;
  cursor: pointer;
  padding: 0;
  font: inherit;
}
.link:hover {
  text-decoration: underline;
}
</style>