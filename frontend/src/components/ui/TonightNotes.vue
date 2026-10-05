<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { ApiError, apiFetch } from '../../api/client'
import type { components } from '../../api/schema'
import { sessionGeneration, SessionChangedError } from '../../api/session'
import { useAuthStore } from '../../stores/auth'
import { useTonightStore } from '../../stores/tonight'

const props = defineProps<{
  campaignId: string
  entityId: string
  entityName: string
  savedText: string
}>()
const emit = defineEmits<{ cancel: [] }>()
const auth = useAuthStore()
const tonight = useTonightStore()
const fieldId = useId()
const draft = ref('')
const baseline = ref('')
const confirmed = ref('')
const editing = ref(true)
const notesField = ref<globalThis.HTMLTextAreaElement | null>(null)
const editButton = ref<globalThis.HTMLButtonElement | null>(null)
const busy = ref(false)
const status = ref('')
const conflict = ref(false)
const reviewed = ref(false)
const reviewing = ref(false)
let reviewVersion = 0
const scope = computed(() =>
  auth.account?.id ? JSON.stringify([auth.account.id, props.campaignId, props.entityId]) : '',
)
const dirty = computed(() => draft.value !== baseline.value)
let version = 0
onBeforeUnmount(() => {
  version++
})
watch(
  scope,
  (value, previous) => {
    version++
    reviewVersion++
    reviewing.value = false
    const accountChanged = previous && JSON.parse(previous)[0] !== auth.account?.id
    draft.value = value && !accountChanged ? props.savedText : ''
    baseline.value = draft.value
    confirmed.value = draft.value
    editing.value = true
    busy.value = false
    conflict.value = false
    reviewed.value = false
    status.value = ''
  },
  { immediate: true, flush: 'sync' },
)
watch(
  () => props.savedText,
  (value) => {
    confirmed.value = value
    if (!dirty.value && !busy.value) {
      draft.value = value
      baseline.value = value
    }
  },
)
function updateDraft(event: globalThis.Event) {
  const field = event.currentTarget as globalThis.HTMLTextAreaElement
  if (!scope.value || field.dataset.scope !== scope.value || busy.value) return
  draft.value = field.value
  status.value = ''
}
function reloadSaved() {
  reviewVersion++
  reviewing.value = false
  draft.value = confirmed.value
  baseline.value = confirmed.value
  conflict.value = false
  status.value = ''
}
function cancel() {
  reloadSaved()
  editing.value = false
  void nextTick(() => editButton.value?.focus())
  emit('cancel')
}
function edit() {
  draft.value = confirmed.value
  baseline.value = confirmed.value
  status.value = ''
  editing.value = true
  void nextTick(() => notesField.value?.focus())
}
function keepDraft() {
  reviewVersion++
  reviewing.value = false
  baseline.value = confirmed.value
  conflict.value = false
  status.value = 'Current saved notes reviewed. Your draft is ready to save.'
}
async function save() {
  if (!scope.value || busy.value || !dirty.value || conflict.value) return
  if (Array.from(draft.value).length > 20000) {
    status.value = 'Notes exceed 20,000 characters. Your draft is kept; shorten it before saving.'
    return
  }
  reviewVersion++
  const activeVersion = version
  const generation = sessionGeneration()
  const activeScope = scope.value
  const text = draft.value
  const campaign = props.campaignId
  const entity = props.entityId
  const current = () =>
    version === activeVersion && scope.value === activeScope && generation === sessionGeneration()
  busy.value = true
  status.value = ''
  try {
    const result = await tonight.saveNotes(campaign, entity, text, baseline.value)
    if (!current()) return
    const saved = result.refreshed && result.savedText !== undefined ? result.savedText : text
    baseline.value = saved
    confirmed.value = saved
    draft.value = saved
    status.value = result.refreshed
      ? 'Notes saved to the world.'
      : 'Notes saved to the world. Could not refresh the page; reload to see updated history.'
  } catch (error) {
    if (!current() || error instanceof SessionChangedError) return
    if (error instanceof ApiError && error.status === 409) {
      conflict.value = true
      reviewed.value = false
      status.value = 'Saved notes changed. Your draft is kept. Review current notes before saving.'
      await review()
    } else
      status.value =
        error instanceof Error ? `Notes not saved. ${error.message}` : 'Notes not saved. Try again.'
  } finally {
    if (current()) busy.value = false
  }
}
async function review() {
  if (!conflict.value || reviewing.value) return
  const request = ++reviewVersion
  reviewing.value = true
  const activeVersion = version
  const activeScope = scope.value
  const generation = sessionGeneration()
  const campaign = props.campaignId
  const entity = props.entityId
  const current = () =>
    version === activeVersion &&
    scope.value === activeScope &&
    generation === sessionGeneration() &&
    request === reviewVersion &&
    conflict.value
  try {
    const run = await apiFetch<components['schemas']['RunStateResponse']>(
      `/api/campaigns/${encodeURIComponent(campaign)}/run-state`,
    )
    if (!current()) return
    const notes = run.session[entity]?.notes
    confirmed.value = typeof notes === 'string' ? notes : ''
    reviewed.value = true
  } catch {
    if (current())
      status.value =
        'Saved notes changed. Your draft is kept. Could not refresh current notes; retry review.'
  } finally {
    if (current()) reviewing.value = false
  }
}
</script>

<template>
  <section class="mc-tonight-notes">
    <label v-if="editing" :for="fieldId" class="mc-notes-label">Notes for {{ entityName }}</label>
    <p v-else class="mc-notes-label">Notes for {{ entityName }}</p>
    <textarea
      v-if="editing"
      :id="fieldId"
      ref="notesField"
      :value="draft"
      :disabled="!scope || busy"
      :data-scope="scope"
      :aria-describedby="`${fieldId}-status`"
      rows="3"
      class="mc-notes-input"
      @input="updateDraft"
    />
    <p v-if="!editing && confirmed" class="mc-notes-saved">{{ confirmed }}</p>
    <button
      v-if="!editing"
      ref="editButton"
      type="button"
      class="mc-btn mc-btn-secondary"
      :disabled="!scope"
      :aria-label="`${confirmed ? 'Edit' : 'Add'} notes for ${entityName}`"
      @click="edit"
    >
      {{ confirmed ? 'Edit notes' : 'Add notes' }}
    </button>
    <div v-if="editing" class="mc-notes-actions">
      <button
        type="button"
        class="mc-btn mc-btn-secondary"
        :disabled="!scope || busy || !dirty || conflict"
        @click="save"
      >
        {{ busy ? 'Saving…' : 'Save notes' }}
      </button>
      <button type="button" class="mc-btn mc-btn-secondary" :disabled="busy" @click="cancel">
        Cancel
      </button>
    </div>
    <p :id="`${fieldId}-status`" class="mc-muted" role="status">{{ status }}</p>
    <div v-if="conflict">
      <p>Current saved notes</p>
      <pre v-if="reviewed" class="mc-notes-saved">{{ confirmed || 'No saved notes.' }}</pre>
      <button
        v-if="!reviewed"
        type="button"
        class="mc-btn mc-btn-secondary"
        :disabled="reviewing"
        @click="review"
      >
        Retry review
      </button>
      <template v-else>
        <button type="button" class="mc-btn mc-btn-secondary" @click="reloadSaved">
          Reload saved notes
        </button>
        <button type="button" class="mc-btn mc-btn-secondary" @click="keepDraft">
          Keep my draft after review
        </button>
      </template>
    </div>
  </section>
</template>

<style scoped>
.mc-tonight-notes {
  flex-basis: 100%;
  width: 100%;
  margin-top: 0.5rem;
}
.mc-notes-label {
  display: block;
  font-weight: 600;
  color: var(--mc-text-primary);
}
.mc-notes-input {
  box-sizing: border-box;
  width: 100%;
  min-height: 5rem;
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
.mc-notes-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.mc-notes-saved {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
</style>
