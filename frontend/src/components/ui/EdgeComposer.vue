<script setup lang="ts">
/**
 * Edge composer (v3; AD-30/31/32) — the matrix-driven relation creator:
 * relation TYPE only from the live kinds registry's cells for this
 * source kind (AD-31: `edge_kind_ok` availability, one source), targets
 * filtered to the type's destination kinds, counter bounded by the cell's
 * semantics, and a REQUIRED saved reason (AD-32) — blank blocks submit
 * client-side; the backend's 422 stays armed as the backstop.
 *
 * Presentational: emits `create`; the parent owns the API call (the
 * world store's addEdge, which lands back as a coalesced refetch).
 */
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import EmptyState from './EmptyState.vue'

type KindsResponse = components['schemas']['KindsResponse']
type EdgeTypeRule = components['schemas']['EdgeTypeRule']
interface TargetCandidate {
  id: string
  name: string
  kind: string
}

const props = defineProps<{
  kinds: KindsResponse | null
  /** The fixed source entity — its kind drives the available types. */
  srcEntity: { id: string; kind: string; name: string }
  /** Every other entity in the campaign — filtered by the type's dst set. */
  candidates: TargetCandidate[]
  busy?: boolean
}>()

const emit = defineEmits<{
  create: [{ src: string; dst: string; type: string; counter: number; reason: string }]
}>()

/** One matrix cell, or undefined for types the registry does not know
 * (a payload is never authoritative past its own mount). */
function kindRule(edgeType: string): EdgeTypeRule | undefined {
  return props.kinds?.kind_rules[edgeType]
}

const type = ref('')
const dst = ref('')
const counterInput = ref('')
const reason = ref('')

/** The available outgoing types for the source kind (AD-31 single source:
 * the registry renders the same matrix the commit path validates; a
 * ``src: null`` cell means ANY kind — the legacy catch-all types). */
const types = computed<string[]>(() => {
  const edgeTypes = props.kinds?.edge_types ?? []
  return edgeTypes.filter((edgeType) => {
    const src = kindRule(edgeType)?.src
    return src === undefined || src === null || src.includes(props.srcEntity.kind)
  })
})

const rule = computed<EdgeTypeRule | undefined>(() => kindRule(type.value))

const dstKinds = computed<string[] | null>(() => rule.value?.dst ?? null)

const eligibleTargets = computed<TargetCandidate[]>(() => {
  const allowed = dstKinds.value
  if (allowed === null) return props.candidates
  return props.candidates.filter((candidate) => allowed.includes(candidate.kind))
})

const counterBounds = computed<number[] | null>(() => rule.value?.counter_bounds ?? null)

const counterMin = computed(() => counterBounds.value?.[0] ?? 1)
const counterMax = computed(() => counterBounds.value?.[1] ?? 99)
/** Neutral cells (part_of) are locked to 1 — the input hides. */
const counterFixed = computed(
  () => counterBounds.value !== null && counterBounds.value.length === 1,
)

const counterValue = computed<number | null>(() => {
  if (counterFixed.value) return counterMin.value
  if (counterInput.value === '') return null
  const parsed = Number(counterInput.value)
  return Number.isNaN(parsed) ? null : parsed
})

const reasonTrimmed = computed(() => reason.value.trim())

const canCreate = computed(
  () =>
    type.value !== '' &&
    dst.value !== '' &&
    counterValue.value !== null &&
    reasonTrimmed.value !== '' &&
    !props.busy,
)

function onTypeChange() {
  dst.value = ''
  // Seed the counter at the cell's minimum (the legacy editor's default);
  // a blank counter never blocks the form once a type is chosen.
  counterInput.value = String(counterMin.value)
}

function submit() {
  const counter = counterValue.value
  if (!canCreate.value || counter === null) return
  const typeId = type.value
  const dstId = dst.value
  const reasonText = reasonTrimmed.value
  if (!typeId || !dstId || !reasonText) return
  type.value = ''
  dst.value = ''
  counterInput.value = ''
  reason.value = ''
  emit('create', {
    src: props.srcEntity.id,
    dst: dstId,
    type: typeId,
    counter,
    reason: reasonText,
  })
}
</script>

<template>
  <form class="mc-edge-composer" @submit.prevent="submit">
    <p v-if="!kinds" class="mc-muted">Loading the relation vocabulary…</p>
    <EmptyState
      v-else-if="types.length === 0"
      title="No outgoing relations"
      body="This kind has no outbound relation types in the vocabulary."
    ></EmptyState>

    <template v-else>
      <label class="mc-edge-field">
        <span class="mc-edge-label">Relation</span>
        <select v-model="type" class="mc-edge-select" @change="onTypeChange">
          <option value="" disabled>Choose…</option>
          <option v-for="edgeType in types" :key="edgeType" :value="edgeType">
            {{ edgeType
            }}<template v-if="kindRule(edgeType)?.counter_semantic">
              · {{ kindRule(edgeType)!.counter_semantic }}</template
            >
          </option>
        </select>
      </label>

      <label class="mc-edge-field">
        <span class="mc-edge-label">Target</span>
        <select v-model="dst" class="mc-edge-select" :disabled="!type">
          <option value="" disabled>
            {{ eligibleTargets.length > 0 ? 'Choose…' : 'No eligible targets' }}
          </option>
          <option v-for="target in eligibleTargets" :key="target.id" :value="target.id">
            {{ target.name }} ({{ target.kind }})
          </option>
        </select>
      </label>

      <label v-if="!counterFixed" class="mc-edge-field">
        <span class="mc-edge-label">Counter</span>
        <input
          v-model="counterInput"
          type="number"
          class="mc-edge-input"
          :min="counterMin"
          :max="counterMax"
          :disabled="!type"
        />
      </label>

      <label class="mc-edge-field">
        <span class="mc-edge-label">Reason</span>
        <textarea
          v-model="reason"
          class="mc-edge-textarea"
          rows="2"
          :disabled="!type"
          placeholder="Why does this edge exist? (required)"
        ></textarea>
      </label>

      <p v-if="type && reasonTrimmed === ''" class="mc-edge-hint">
        A relation needs its reason — blank submits are blocked.
      </p>

      <button type="submit" class="mc-btn" :disabled="!canCreate">
        {{ busy ? 'Adding…' : 'Add relation' }}
      </button>
    </template>
  </form>
</template>

<style scoped>
.mc-edge-composer {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.mc-edge-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.mc-edge-label {
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-edge-select,
.mc-edge-input,
.mc-edge-textarea {
  background: var(--mc-input);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  color: var(--mc-text-primary);
  padding: 0.45rem 0.6rem;
}
.mc-edge-hint {
  margin: 0;
  font-size: 0.8rem;
  color: var(--mc-text-muted);
}
.mc-btn[disabled] {
  opacity: 0.55;
  cursor: default;
}
</style>
