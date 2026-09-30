<script setup lang="ts">
/**
 * Spell cards (2026-09-27 round 3) — spells as small editable cards
 * instead of the "one per line" textarea. v-model accepts string[]
 * (the AR25 spell names). Local row state with the parent-echo guard
 * (StatBlockEditor's fingerprint pattern): external updates re-sync,
 * our own emissions never re-trigger mid-edit; blanks drop on blur.
 */
import { ref, watch } from 'vue'

const props = defineProps<{
  modelValue: string[] | null
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[] | null): void
}>()

const items = ref<string[]>([])
let lastEmitted = ''

function syncFromParent() {
  const next = (props.modelValue ?? []).map((spell) => spell)
  if (JSON.stringify(next) !== lastEmitted) {
    items.value = next
  }
}

watch(() => props.modelValue, syncFromParent, { immediate: true })

function emitRows() {
  lastEmitted = JSON.stringify(items.value)
  emit('update:modelValue', items.value.length > 0 ? [...items.value] : null)
}

/** Drop the empties and visible-shrink — run on blur/remove only, so an
 * in-progress blank row never vanishes under the user. */
function emitCleaned() {
  const cleaned = items.value.map((spell) => spell.trim()).filter(Boolean)
  lastEmitted = JSON.stringify(cleaned)
  emit('update:modelValue', cleaned.length > 0 ? cleaned : null)
}

function addSpell() {
  items.value = [...items.value, '']
  emitRows()
}

function removeAt(index: number) {
  items.value.splice(index, 1)
  emitCleaned()
}

function onBlur(index: number) {
  const trimmed = (items.value[index] ?? '').trim()
  if (trimmed === '') {
    items.value.splice(index, 1)
    emitCleaned()
    return
  }
  items.value[index] = trimmed
  emitCleaned()
}
</script>

<template>
  <div class="mc-spell-cards">
    <div v-if="items.length > 0" class="mc-spell-rows">
      <label v-for="(spell, index) in items" :key="index" class="mc-spell-card">
        <input
          :value="spell"
          :disabled="disabled"
          class="mc-spell-input"
          :aria-label="`spell ${index + 1}`"
          @input="
            (event) => {
              items[index] = (event.target as HTMLInputElement).value
            }
          "
          @blur="onBlur(index)"
        />
        <button
          type="button"
          class="mc-link mc-spell-remove"
          :disabled="disabled"
          @click="removeAt(index)"
        >
          ✕
        </button>
      </label>
    </div>
    <p v-if="items.length === 0" class="mc-muted mc-spell-empty">No spells yet.</p>
    <button type="button" class="mc-btn mc-btn-secondary" :disabled="disabled" @click="addSpell">
      + Add spell
    </button>
  </div>
</template>

<style scoped>
.mc-spell-cards {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.mc-spell-rows {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.mc-spell-card {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  padding: 0.2rem 0.5rem;
}
.mc-spell-input {
  background: var(--mc-input);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  color: var(--mc-text-primary);
  padding: 0.25rem 0.45rem;
  min-width: 9rem;
}
.mc-spell-remove {
  background: none;
  border: none;
  color: var(--mc-text-muted);
  cursor: pointer;
  padding: 0 0.15rem;
  font: inherit;
}
.mc-spell-empty {
  margin: 0;
}
</style>
