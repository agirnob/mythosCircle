<script setup lang="ts">
/**
 * Entity editor (consolidation, 2026-09-27) — the rebuild's record
 * editor: PATCHes the AR24 profile fields, text, world integration, the
 * canonical stat block (StatBlockEditor, blanks dropped), and the dial
 * (AD-36) onto a COMMITTED entity through the store commit path.
 * Presentational: emits `save` with the PATCH; the parent owns the API
 * call + error rendering (409 = the world moved, refresh).
 */
import { ref, watch } from 'vue'

import type { components } from '../../api/schema'
import { statBlockWithRecordIdentity } from '../../lib/statBlockIdentity'
import StatBlockEditor from '../StatBlockEditor.vue'
import {
  FIELD_LABELS,
  IDENTITY_FIELDS,
  LORE_FIELDS,
  WORLD_INTEGRATION_FIELDS,
} from '../profile/profile'
import DialPicker from './DialPicker.vue'

type EntityExport = components['schemas']['EntityExport']

const props = defineProps<{
  entity: EntityExport
  dialLevels: string[]
  /** Registry archetypes ({kind, name, default_dial}) — place/faction only. */
  archetypes?: { kind: string; name: string; default_dial: string }[]
  busy?: boolean
}>()

const emit = defineEmits<{
  save: [patch: Record<string, unknown>]
  cancel: []
}>()

const ROLES = ['NPC', 'BBEG', 'Monster'] as const

const name = ref('')
const text = ref('')
const lore = ref<Record<string, string>>({})
const identity = ref<Record<string, string>>({})
const worldInt = ref<Record<string, string>>({})
const flat = ref<Record<string, string>>({})
const statBlock = ref<Record<string, unknown> | null>(null)
const dial = ref<string | null>(null)
const archetype = ref<string>('')

/** String values for a key set — blanks read as empty, absent as blank. */
function picks(data: Record<string, unknown>, keys: readonly string[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const key of keys) {
    const value = data[key]
    out[key] = typeof value === 'string' ? value : ''
  }
  return out
}

function initialize() {
  const data = (props.entity.data ?? {}) as Record<string, unknown>
  name.value = props.entity.name
  text.value = props.entity.text ?? ''
  lore.value = picks(data, [...LORE_FIELDS])
  identity.value = picks(data, ['role', ...IDENTITY_FIELDS])
  const block = data['world_integration']
  worldInt.value = picks(
    typeof block === 'object' && block !== null && !Array.isArray(block)
      ? (block as Record<string, unknown>)
      : {},
    [...WORLD_INTEGRATION_FIELDS],
  )
  const blockData = data['stat_block']
  statBlock.value =
    typeof blockData === 'object' && blockData !== null
      ? { ...(blockData as Record<string, unknown>) }
      : null
  const level = data['dial']
  dial.value = typeof level === 'string' ? level : null
  const arch = data['archetype']
  archetype.value = typeof arch === 'string' ? arch : ''
  flat.value = picks(data, flatFieldsForKind())
  if (!flat.value.description && !isCharacter()) flat.value.description = text.value
}

// An edit session owns its opening snapshot until save/cancel unmounts it.
// World refetches must not replace prose or partially authored stat rows.
watch([() => props.entity.id, () => props.entity.kind], initialize, { immediate: true })

function buildPatch(): Record<string, unknown> {
  const patch: Record<string, unknown> = { name: name.value, text: text.value }
  if (!isCharacter()) {
    for (const [key, value] of Object.entries(flat.value)) patch[key] = value
    patch.text = flat.value.description ?? text.value
  }
  const merged = { ...lore.value, ...identity.value }
  for (const [key, value] of Object.entries(merged)) {
    patch[key] = value
  }
  const integration: Record<string, string> = {}
  for (const [key, value] of Object.entries(worldInt.value)) {
    integration[key] = value
  }
  patch['world_integration'] = integration
  patch['stat_block'] = statBlockWithRecordIdentity(statBlock.value, identity.value)
  if (dial.value !== null) patch['dial'] = dial.value
  if (archetype.value !== '' && !isCharacter()) patch['archetype'] = archetype.value
  return patch
}

function isCharacter(): boolean {
  return props.entity.kind === 'character'
}

function flatFieldsForKind(): readonly string[] {
  if (props.entity.kind === 'place') return ['description', 'inhabitants', 'whats_hidden']
  if (props.entity.kind === 'faction') return ['description', 'doctrine', 'assets']
  return []
}

/** The archetypes registry offers for this kind (AD-34 payload). */
function archetypesForKind(): string[] {
  return (props.archetypes ?? [])
    .filter((entry) => entry.kind === props.entity.kind)
    .map((entry) => entry.name)
}

/** The flat-kind display label (place/faction). */
function save() {
  if (props.busy) return
  emit('save', buildPatch())
}
</script>

<template>
  <form class="mc-entity-editor" @submit.prevent="save">
    <label v-if="isCharacter()" class="mc-edit-field">
      <span class="mc-edit-label">Name</span>
      <input v-model="name" class="mc-edit-input" aria-label="Name" />
    </label>
    <label class="mc-edit-field">
      <span class="mc-edit-label">{{ isCharacter() ? 'Text' : 'Description' }}</span>
      <textarea
        v-if="isCharacter()"
        v-model="text"
        class="mc-edit-textarea"
        rows="4"
        aria-label="Text"
      ></textarea>
      <textarea
        v-else
        v-model="flat.description"
        class="mc-edit-textarea"
        rows="4"
        aria-label="Description"
      ></textarea>
    </label>

    <template v-if="isCharacter()">
      <div class="mc-edit-grid">
        <label v-for="key in ['role', ...IDENTITY_FIELDS]" :key="key" class="mc-edit-field">
          <span class="mc-edit-label">{{ FIELD_LABELS[key] ?? key }}</span>
          <select
            v-if="key === 'role'"
            v-model="identity.role"
            class="mc-edit-input"
            :aria-label="FIELD_LABELS['role']"
          >
            <option value="">—</option>
            <option v-for="role in ROLES" :key="role" :value="role">{{ role }}</option>
          </select>
          <input
            v-else
            v-model="identity[key]"
            class="mc-edit-input"
            :aria-label="FIELD_LABELS[key] ?? key"
          />
        </label>

        <label v-for="key in LORE_FIELDS" :key="key" class="mc-edit-field">
          <span class="mc-edit-label">{{ FIELD_LABELS[key] ?? key }}</span>
          <textarea
            v-model="lore[key]"
            rows="2"
            class="mc-edit-textarea"
            :aria-label="FIELD_LABELS[key] ?? key"
          ></textarea>
        </label>
      </div>

      <div class="mc-edit-field">
        <span class="mc-edit-label">World integration</span>
        <div class="mc-edit-grid">
          <label v-for="key in WORLD_INTEGRATION_FIELDS" :key="key" class="mc-edit-field">
            <span class="mc-edit-label">{{ FIELD_LABELS[key] ?? key }}</span>
            <textarea
              v-model="worldInt[key]"
              rows="2"
              class="mc-edit-textarea"
              :aria-label="FIELD_LABELS[key] ?? key"
            ></textarea>
          </label>
        </div>
      </div>

      <div class="mc-edit-field">
        <span class="mc-edit-label">Stat block</span>
        <StatBlockEditor v-model="statBlock" hide-identity />
      </div>
    </template>

    <template v-else>
      <div class="mc-edit-grid mc-flat-edit-grid">
        <label
          v-for="key in flatFieldsForKind().filter((field) => field !== 'description')"
          :key="key"
          class="mc-edit-field"
        >
          <span class="mc-edit-label">{{ key.replaceAll('_', ' ') }}</span>
          <textarea
            v-model="flat[key]"
            class="mc-edit-textarea"
            rows="3"
            :aria-label="key.replaceAll('_', ' ')"
          ></textarea>
        </label>
      </div>
      <div v-if="archetypesForKind().length > 0" class="mc-edit-field">
        <span class="mc-edit-label">Archetype</span>
        <select v-model="archetype" class="mc-edit-input" aria-label="Archetype">
          <option value="">—</option>
          <option v-for="archName in archetypesForKind()" :key="archName" :value="archName">
            {{ archName }}
          </option>
        </select>
      </div>
      <p class="mc-muted mc-edit-note">
        These fields stay separate so regeneration can target one part without replacing the rest.
      </p>
    </template>

    <p v-if="dialLevels.length > 0" class="mc-edit-dial">
      <DialPicker :levels="dialLevels" :current="dial" @change="(level) => (dial = level)" />
    </p>

    <p class="mc-edit-actions">
      <button
        type="button"
        class="mc-btn mc-btn-secondary"
        :disabled="busy"
        @click="emit('cancel')"
      >
        Cancel
      </button>
      <button type="submit" class="mc-btn" :disabled="busy">
        {{ busy ? 'Saving…' : 'Save changes' }}
      </button>
    </p>
  </form>
</template>

<style scoped>
.mc-entity-editor {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 860px;
}
.mc-edit-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.mc-edit-label {
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-edit-input,
.mc-edit-textarea {
  background: var(--mc-input);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  color: var(--mc-text-primary);
  padding: 0.5rem 0.6rem;
}
.mc-edit-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--mc-gap-sm);
}
.mc-flat-edit-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.mc-edit-dial {
  display: flex;
  align-items: center;
  gap: var(--mc-gap-sm);
}
.mc-edit-note {
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
}
.mc-edit-actions {
  display: flex;
  gap: var(--mc-gap-sm);
}
</style>
