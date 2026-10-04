<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useAuthStore } from '../../stores/auth'

const props = defineProps<{ campaignId: string }>()
const auth = useAuthStore()
const notes = ref('')
const status = ref('No notes saved.')
const fieldId = useId()
const storageKey = computed(() =>
  auth.account?.id && props.campaignId
    ? `mythoscircle:tonight:${auth.account.id}:${props.campaignId}`
    : null,
)

watch(
  storageKey,
  (key) => {
    // Clear synchronously before attempting to read the new private scope.
    notes.value = ''
    status.value = key ? 'No notes saved.' : 'Sign in to write session notes.'
    if (!key) return
    try {
      notes.value = globalThis.localStorage.getItem(key) ?? ''
      if (notes.value) status.value = 'Saved in this browser.'
    } catch {
      status.value = 'Browser storage unavailable. Notes are not saved.'
    }
  },
  { immediate: true, flush: 'sync' },
)

function updateNotes(event: Event) {
  const key = storageKey.value
  const field = event.currentTarget as HTMLTextAreaElement
  // A scope change can precede Vue's DOM patch; reject input from the old field.
  if (!key || field.dataset.storageKey !== key) return
  notes.value = field.value
  try {
    if (notes.value) globalThis.localStorage.setItem(key, notes.value)
    else globalThis.localStorage.removeItem(key)
    status.value = notes.value ? 'Saved in this browser.' : 'No notes saved.'
  } catch {
    status.value = 'Browser storage unavailable. Notes are not saved.'
  }
}
</script>

<template>
  <section class="mc-tonight-notes">
    <label :for="fieldId" class="mc-notes-label">Session notes</label>
    <p :id="`${fieldId}-help`" class="mc-muted">
      Private preparation saved only in this browser for this account and campaign.
    </p>
    <textarea
      :id="fieldId"
      :value="notes"
      :disabled="!storageKey"
      :data-storage-key="storageKey"
      :aria-describedby="`${fieldId}-help ${fieldId}-status`"
      rows="5"
      class="mc-notes-input"
      @input="updateNotes"
    />
    <p :id="`${fieldId}-status`" class="mc-muted" role="status">{{ status }}</p>
  </section>
</template>

<style scoped>
.mc-tonight-notes {
  margin-bottom: 1.5rem;
}
.mc-notes-label {
  display: block;
  font-weight: 600;
  color: var(--mc-text-primary);
}
.mc-notes-input {
  box-sizing: border-box;
  width: 100%;
  min-height: 8rem;
  resize: vertical;
  padding: 0.75rem;
  color: var(--mc-text-primary);
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  font: inherit;
  line-height: 1.5;
}
.mc-notes-input:focus-visible {
  outline: 2px solid var(--mc-canonical);
  outline-offset: 2px;
}
</style>
