<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import type { JournalDraft, JournalEntry, JournalReference, PlaySession } from '../../api/journal'

const props = defineProps<{
  session: PlaySession
  entry?: JournalEntry
  initial?: JournalDraft
  entities: { id: string; name: string; kind: string }[]
  busy: boolean
  error: string
}>()
const emit = defineEmits<{ save: [draft: JournalDraft]; cancel: [] }>()
const headline = ref(props.entry?.headline ?? props.initial?.headline ?? '')
const context = ref(props.entry?.context ?? props.initial?.context ?? '')
// The browser edits UTF-16; the wire contract uses Unicode code points.
const references = ref<JournalReference[]>(
  (props.entry?.references ?? props.initial?.references ?? []).map((item) => {
    const text = item.field === 'headline' ? headline.value : context.value
    return {
      ...item,
      start: item.start == null ? null : Array.from(text).slice(0, item.start).join('').length,
      end: item.end == null ? null : Array.from(text).slice(0, item.end).join('').length,
    }
  }),
)
const headlineField = ref<globalThis.HTMLInputElement | null>(null)
const contextField = ref<globalThis.HTMLTextAreaElement | null>(null)
const activeField = ref<'headline' | 'context' | null>(null)
const range = ref<{ start: number; end: number } | null>(null)
const query = ref('')
const highlighted = ref(0)
const matches = computed(() =>
  props.entities
    .filter((item) => item.name.toLocaleLowerCase().includes(query.value.toLocaleLowerCase()))
    .slice(0, 20),
)
const relatedEntityIds = computed(() => new Set(references.value.map((item) => item.entity_id)))
onMounted(() => {
  ;(props.entry ? contextField.value : headlineField.value)?.focus()
})
function fieldValue(field: 'headline' | 'context') {
  return field === 'headline' ? headline : context
}
function adjustReferences(field: 'headline' | 'context', before: string, after: string) {
  let start = 0
  while (start < before.length && start < after.length && before[start] === after[start]) start++
  let oldEnd = before.length,
    newEnd = after.length
  while (oldEnd > start && newEnd > start && before[oldEnd - 1] === after[newEnd - 1]) {
    oldEnd--
    newEnd--
  }
  const delta = newEnd - oldEnd
  references.value = references.value.flatMap((item) => {
    if (item.field !== field || item.start == null || item.end == null) return [item]
    if (item.end <= start) return [item]
    if (item.start >= oldEnd) return [{ ...item, start: item.start + delta, end: item.end + delta }]
    return [] // Edited through a mention: it becomes plain text.
  })
}
function input(field: 'headline' | 'context', event: globalThis.Event) {
  const target = event.target as globalThis.HTMLInputElement | globalThis.HTMLTextAreaElement
  const state = fieldValue(field)
  adjustReferences(field, state.value, target.value)
  state.value = target.value
  suggest(field, target)
}
function suggest(
  field: 'headline' | 'context',
  target: globalThis.HTMLInputElement | globalThis.HTMLTextAreaElement,
) {
  const before = target.value.slice(0, target.selectionStart ?? 0)
  const match = before.match(/(?:^|[\s(])@([^@\n]*)$/u)
  if (!match) {
    activeField.value = null
    range.value = null
    return
  }
  const mentionStart = before.lastIndexOf('@')
  if (
    references.value.some(
      (item) =>
        item.field === field &&
        item.start === mentionStart &&
        item.end != null &&
        item.end <= before.length,
    )
  ) {
    activeField.value = null
    range.value = null
    return
  }
  activeField.value = field
  range.value = { start: mentionStart, end: before.length }
  query.value = match[1] ?? ''
  highlighted.value = 0
}
function choose(entity: { id: string; name: string }) {
  const field = activeField.value,
    span = range.value
  if (!field || !span) return
  const state = fieldValue(field)
  const token = '@' + entity.name
  const next = state.value.slice(0, span.start) + token + ' ' + state.value.slice(span.end)
  adjustReferences(field, state.value, next)
  state.value = next
  references.value.push({
    entity_id: entity.id,
    label: entity.name,
    token,
    field,
    start: span.start,
    end: span.start + token.length,
  })
  activeField.value = null
  void nextTick(() => {
    const target = field === 'headline' ? headlineField.value : contextField.value
    target?.focus()
    target?.setSelectionRange(span.start + token.length + 1, span.start + token.length + 1)
  })
}
function keydown(event: globalThis.KeyboardEvent) {
  if (!activeField.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    activeField.value = null
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (matches.value.length)
      highlighted.value =
        (highlighted.value + (event.key === 'ArrowDown' ? 1 : matches.value.length - 1)) %
        matches.value.length
  }
  if (event.key === 'Enter' && matches.value[highlighted.value]) {
    event.preventDefault()
    choose(matches.value[highlighted.value]!)
  }
  if (event.key === 'Tab') activeField.value = null
}
function toggleRelated(entity: { id: string; name: string }) {
  if (relatedEntityIds.value.has(entity.id))
    references.value = references.value.filter((item) => item.entity_id !== entity.id)
  else
    references.value.push({
      entity_id: entity.id,
      label: entity.name,
      field: null,
      start: null,
      end: null,
    })
}
const headlineTooLong = computed(() => Array.from(headline.value).length > 180)
function save() {
  if (!headline.value.trim() || headlineTooLong.value || props.busy) return
  emit('save', {
    headline: headline.value,
    context: context.value,
    references: references.value.map((item) => {
      const text = item.field === 'headline' ? headline.value : context.value
      return {
        ...item,
        start: item.start == null ? null : Array.from(text.slice(0, item.start)).length,
        end: item.end == null ? null : Array.from(text.slice(0, item.end)).length,
      }
    }),
  })
}
</script>

<template>
  <form class="mc-journal-composer" @submit.prevent="save" @keydown.esc="!busy && emit('cancel')">
    <h2>{{ entry ? 'Edit event' : 'Record an event' }}</h2>
    <label for="journal-headline">Headline</label>
    <div class="mc-mention-anchor">
      <input
        id="journal-headline"
        ref="headlineField"
        :value="headline"
        :disabled="busy"
        required
        autocomplete="off"
        role="combobox"
        aria-haspopup="listbox"
        aria-autocomplete="list"
        :aria-expanded="activeField === 'headline'"
        :aria-controls="activeField === 'headline' ? 'journal-mentions' : undefined"
        :aria-activedescendant="
          activeField === 'headline' && matches.length
            ? `journal-mention-${highlighted}`
            : undefined
        "
        @input="input('headline', $event)"
        @click="suggest('headline', $event.target as globalThis.HTMLInputElement)"
        @keydown="keydown"
      />
    </div>
    <p v-if="headlineTooLong" role="alert">
      Headline must be at most 180 characters. Your draft is kept.
    </p>
    <label for="journal-context">Context <span class="mc-muted"> · optional</span></label>
    <div class="mc-mention-anchor">
      <textarea
        id="journal-context"
        ref="contextField"
        :value="context"
        :disabled="busy"
        rows="4"
        role="combobox"
        aria-haspopup="listbox"
        aria-autocomplete="list"
        :aria-expanded="activeField === 'context'"
        :aria-controls="activeField === 'context' ? 'journal-mentions' : undefined"
        :aria-activedescendant="
          activeField === 'context' && matches.length ? `journal-mention-${highlighted}` : undefined
        "
        aria-describedby="journal-mention-hint"
        @input="input('context', $event)"
        @click="suggest('context', $event.target as globalThis.HTMLTextAreaElement)"
        @keydown="keydown"
      />
    </div>
    <div
      v-if="activeField"
      id="journal-mentions"
      class="mc-mention-menu"
      role="listbox"
      aria-label="Entity suggestions"
    >
      <button
        v-for="(entity, index) in matches"
        :id="`journal-mention-${index}`"
        :key="entity.id"
        type="button"
        role="option"
        :aria-selected="index === highlighted"
        @mousedown.prevent
        @click="choose(entity)"
      >
        <span>{{ entity.name }}</span
        ><small>{{ entity.kind }} · {{ entity.id.slice(-6) }}</small>
      </button>
      <p v-if="!matches.length">No matching entity. This mention stays plain text.</p>
    </div>
    <p id="journal-mention-hint" class="mc-muted">
      Type @ and choose a suggestion to link an entity and select it under Related entities.
      Use ↑ ↓ and Enter, or click a suggestion.
    </p>
    <details>
      <summary>Related entities · optional</summary>
      <p class="mc-muted">Unchecking an entity removes its links and keeps the text.</p>
      <div class="mc-journal-related">
        <label v-for="entity in entities" :key="entity.id"
          ><input
            type="checkbox"
            :disabled="busy"
            :checked="relatedEntityIds.has(entity.id)"
            @change="toggleRelated(entity)"
          />{{ entity.name }} <small>{{ entity.kind }} · {{ entity.id.slice(-6) }}</small></label
        >
      </div>
    </details>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="mc-composer-bottom">
      <span class="mc-muted">Session {{ session.sequence }} — {{ session.title }}</span>
      <div>
        <button
          type="button"
          class="mc-btn mc-btn-secondary"
          :disabled="busy"
          @click="emit('cancel')"
        >
          Cancel</button
        ><button
          type="submit"
          class="mc-btn mc-btn-primary"
          :disabled="busy || !headline.trim() || headlineTooLong"
        >
          {{ busy ? 'Saving…' : 'Save event' }}
        </button>
      </div>
    </div>
  </form>
</template>

<style scoped>
.mc-journal-composer {
  border: 1px solid var(--mc-canonical);
  border-radius: var(--mc-radius-sm);
  padding: 1.3rem;
  background: var(--mc-surface);
  margin-block: 1.5rem;
  min-width: 0;
}
h2 {
  font-size: 1.1rem;
  margin: 0 0 1rem;
}
label {
  display: block;
  font-size: 0.85rem;
  margin-bottom: 0.4rem;
}
input:not([type='checkbox']),
textarea {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  font: inherit;
  padding: 0.7rem;
  color: var(--mc-text-primary);
  background: var(--mc-app-bg);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  margin-bottom: 0.9rem;
}
textarea {
  resize: vertical;
}
.mc-mention-menu {
  border: 1px solid var(--mc-canonical);
  border-radius: var(--mc-radius-sm);
  max-height: 15rem;
  overflow-y: auto;
  margin-bottom: 0.5rem;
}
.mc-mention-menu button {
  width: 100%;
  background: none;
  border: 0;
  color: var(--mc-text-primary);
  padding: 0.6rem;
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.5rem;
  cursor: pointer;
  text-align: left;
}
.mc-mention-menu button[aria-selected='true'] {
  background: var(--mc-app-bg);
}
small,
.mc-muted {
  color: var(--mc-text-muted);
  font-size: 0.75rem;
}
summary {
  cursor: pointer;
  color: var(--mc-canonical);
  font-size: 0.8rem;
}
.mc-journal-related {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  margin-top: 0.8rem;
}
.mc-journal-related label {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  flex-wrap: wrap;
}
.mc-composer-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--mc-border);
}
.mc-composer-bottom > div {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
</style>
