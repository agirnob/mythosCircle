<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { WorkspaceNode } from '../../stores/workspace'
const props = defineProps<{
  node: WorkspaceNode
  nodes: WorkspaceNode[]
  targets: { id: string; kind: string; name: string }[]
  selectedId: string | null
  focusId: string | null
  disabled: boolean
}>()
const emit = defineEmits<{
  change: [patch?: Partial<WorkspaceNode>, id?: string]
  select: [id: string]
  add: [kind: WorkspaceNode['kind'], parent: string]
  remove: [id: string]
}>()
const input = ref<globalThis.HTMLInputElement | null>(null)
const children = computed(() => props.nodes.filter((n) => n.parent_id === props.node.id))
const connected = computed(() =>
  props.nodes.some((n) => n.parent_id === props.node.id || n.faction_id === props.node.id),
)
const label = (id: string | null | undefined) =>
  props.targets.find((n) => n.id === id)?.name || 'Unnamed planned entry'
function focusName() {
  if (props.focusId === props.node.id) void nextTick(() => input.value?.focus())
}
onMounted(focusName)
watch(() => props.focusId, focusName)
</script>
<template>
  <article
    class="plan-branch"
    :class="{ selected: selectedId === node.id }"
    :aria-label="`Planned ${node.kind}`"
  >
    <div class="plan-row">
      <span class="planned-badge">Planned {{ node.kind }}</span>
      <input
        ref="input"
        :value="node.name"
        :disabled="disabled"
        :aria-label="`Planned ${node.kind} name`"
        placeholder="Name · or let the model choose"
        @input="
          emit('change', { name: ($event.target as globalThis.HTMLInputElement).value }, node.id)
        "
      />
      <button
        class="row-btn"
        :disabled="disabled || connected"
        :title="
          connected
            ? 'Reassign connected entries before removing this parent'
            : 'Remove planned entry'
        "
        @click="emit('remove', node.id)"
      >
        Remove
      </button>
    </div>
    <p class="placement-line">
      {{ node.parent_id ? `Under ${label(node.parent_id)}` : 'World level'
      }}<template v-if="node.faction_id"> · Member of {{ label(node.faction_id) }}</template>
    </p>
    <details>
      <summary>Move, connect &amp; guidance</summary>
      <div class="plan-edit-grid">
        <label
          >Placement<select
            :value="node.parent_id ?? ''"
            :disabled="disabled"
            @change="
              emit(
                'change',
                { parent_id: ($event.target as globalThis.HTMLSelectElement).value || null },
                node.id,
              )
            "
          >
            <option :value="null">World level</option>
            <option
              v-for="target in targets.filter((t) => t.kind === 'place' && t.id !== node.id)"
              :key="target.id"
              :value="target.id"
            >
              {{ target.name || 'Unnamed planned place' }}
            </option>
          </select></label
        >
        <label v-if="node.kind !== 'place'"
          >Faction membership<select
            :value="node.faction_id ?? ''"
            :disabled="disabled"
            @change="
              emit(
                'change',
                { faction_id: ($event.target as globalThis.HTMLSelectElement).value || null },
                node.id,
              )
            "
          >
            <option :value="null">No membership</option>
            <option
              v-for="target in targets.filter((t) => t.kind === 'faction' && t.id !== node.id)"
              :key="target.id"
              :value="target.id"
            >
              {{ target.name || 'Unnamed planned faction' }}
            </option>
          </select></label
        >
        <label
          >Guidance<textarea
            :value="node.guidance"
            :disabled="disabled"
            maxlength="2000"
            rows="3"
            @input="
              emit(
                'change',
                { guidance: ($event.target as globalThis.HTMLTextAreaElement).value },
                node.id,
              )
            "
          />
        </label>
      </div>
    </details>
    <div v-if="node.kind === 'place'" class="plan-child-tools">
      <button
        v-for="kind in ['place', 'faction', 'character'] as const"
        :key="kind"
        class="row-btn"
        :disabled="disabled"
        @click="emit('add', kind, node.id)"
      >
        + {{ kind }}
      </button>
    </div>
    <div v-if="children.length" class="plan-nested">
      <PlannedEntry
        v-for="child in children"
        :key="child.id"
        :node="child"
        :nodes="nodes"
        :targets="targets"
        :selected-id="selectedId"
        :focus-id="focusId"
        :disabled="disabled"
        @change="(patch, id) => emit('change', patch, id)"
        @select="emit('select', $event)"
        @add="(kind, parent) => emit('add', kind, parent)"
        @remove="emit('remove', $event)"
      />
    </div>
  </article>
</template>
