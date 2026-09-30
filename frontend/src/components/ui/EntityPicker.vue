<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

export interface EntityPickerOption {
  name: string
  kind: string
  source?: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: EntityPickerOption[]
    placeholder?: string
    ariaLabel?: string
  }>(),
  { placeholder: 'Choose or type a name', ariaLabel: undefined },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const input = ref<{ select: () => void } | null>(null)
const open = ref(false)
const query = ref(props.modelValue)

watch(
  () => props.modelValue,
  (value) => {
    if (value !== query.value) query.value = value
  },
)

const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return props.options
    .filter((option) => !needle || option.name.toLowerCase().includes(needle))
    .slice(0, 30)
})

const grouped = computed(() => {
  const groups = new Map<string, EntityPickerOption[]>()
  for (const option of filtered.value) {
    const group = groups.get(option.kind) ?? []
    group.push(option)
    groups.set(option.kind, group)
  }
  return [...groups.entries()]
})

function update(value: string) {
  query.value = value
  emit('update:modelValue', value)
}

function choose(option: EntityPickerOption) {
  update(option.name)
  open.value = false
}

function focusInput() {
  open.value = true
  void nextTick(() => input.value?.select())
}

function blurLater() {
  globalThis.setTimeout(() => {
    open.value = false
  }, 120)
}
</script>

<template>
  <div class="mc-picker" @keydown.esc="open = false">
    <input
      ref="input"
      :value="query"
      class="mc-seed-input"
      role="combobox"
      :aria-expanded="open"
      aria-autocomplete="list"
      :placeholder="placeholder"
      :aria-label="ariaLabel"
      @input="update(($event.target as unknown as { value: string }).value)"
      @focus="open = true"
      @blur="blurLater"
      @keydown.down.prevent="focusInput"
      @keydown.enter.prevent="open = false"
    />
    <div v-if="open" class="mc-picker-menu" role="listbox">
      <template v-for="([kind, entries], index) in grouped" :key="kind">
        <p class="mc-picker-group" :class="{ 'mc-picker-group-first': index === 0 }">
          {{ kind }}
        </p>
        <button
          v-for="option in entries"
          :key="`${kind}:${option.name}`"
          type="button"
          class="mc-picker-option"
          role="option"
          @mousedown.prevent="choose(option)"
        >
          <span>{{ option.name }}</span>
          <small>{{ option.source ?? kind }}</small>
        </button>
      </template>
      <p v-if="!grouped.length" class="mc-picker-empty">No matching entities.</p>
      <p v-if="query.trim()" class="mc-picker-create">
        Press Enter to use “{{ query.trim() }}” as a new name.
      </p>
    </div>
  </div>
</template>

<style scoped>
.mc-picker {
  position: relative;
  min-width: 0;
}

.mc-picker > input {
  width: 100%;
}

.mc-picker-menu {
  position: absolute;
  z-index: 30;
  top: calc(100% + 6px);
  right: 0;
  left: 0;
  max-height: 280px;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--mc-border-strong);
  border-radius: 10px;
  background: #151b28;
  box-shadow: 0 18px 40px rgb(0 0 0 / 35%);
}

.mc-picker-group {
  margin: 8px 8px 4px;
  color: var(--mc-text-muted);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.mc-picker-group-first {
  margin-top: 2px;
}

.mc-picker-option {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 10px;
  border: 0;
  border-radius: 7px;
  color: var(--mc-text);
  background: transparent;
  text-align: left;
}

.mc-picker-option:hover,
.mc-picker-option:focus-visible {
  color: white;
  background: rgb(150 122 255 / 16%);
  outline: 0;
}

.mc-picker-option small {
  color: var(--mc-accent-soft);
  font-size: 0.68rem;
}

.mc-picker-empty,
.mc-picker-create {
  margin: 8px;
  color: var(--mc-text-muted);
  font-size: 0.78rem;
}

.mc-picker-create {
  border-top: 1px solid var(--mc-border);
  padding-top: 8px;
}
</style>
