<script setup lang="ts">
// ONE fully-authored character sheet (path 2 of the hybrid-authorship
// spec — the template-driven direct commit form). Containing view: the
// Add-Character screen; this component is the dogfood surface the DM
// fills end-to-end:
//
// - structured dropdowns + fixed-input slots only (owner ruling F1-F2):
//   role/race/power-slot/class/alignment from the canonical vocabularies,
//   free text ONLY in the record's prose slots (personality, background,
//   …) and description-style fields;
// - the stat block reuses the committed StatBlockEditor (c11b81d) with
//   its identity grid hidden — THIS form owns identity once and mirrors
//   it into BOTH the record text and `stat_block.identity` at build time
//   (the backend's ONE-fact-in-two-slots contract);
// - every AR24 field is drafted non-blank: completeness is the mirror
//   validator's job, so the draft carries every required key ('' when
//   unfilled) and the containing view renders the violation list.
//
// The wire payload is built on demand: `buildSheet()` returns the
// canonical CharacterSheet for the mirror validator and the POST.
import { computed, ref, watch } from 'vue'

import StatBlockEditor from './StatBlockEditor.vue'
import RelationTargetPicker from './RelationTargetPicker.vue'
import { FIELD_LABELS } from './profile/profile'
import type { DeclaredRelation, EntityRef, RelationDraft } from '../api/characters'
import { ALIGNMENTS, ROLES, SRD_CLASSES, SRD_RACES, type CharacterSheet } from '../api/characters'

const props = defineProps<{
  /** The sheet's slot index (1-based display, stable staged key). */
  index: number
  /** Committed campaign entities the Tier-1 relation pickers search. */
  entities: EntityRef[]
  /** The OTHER sheets staged in this submission (Tier-2 targets). */
  staged: Array<{ key: string; name: string }>
}>()
const emit = defineEmits<{ (e: 'change'): void }>()

// --- Identity ---------------------------------------------------------------

const name = ref('')
const role = ref<'NPC' | 'BBEG' | 'Monster'>('NPC')
const RACE_OTHER = '__other__'
const raceChoice = ref<string>(RACE_OTHER)
const raceCustom = ref('')
const levelValue = ref<number | null>(null)
const crValue = ref('')
const CLASS_NONE = '__none__'
const CLASS_OTHER = '__other__'
const classChoice = ref<string>(CLASS_NONE)
const classCustom = ref('')
const alignment = ref('')

const race = computed(() => (raceChoice.value === RACE_OTHER ? raceCustom.value : raceChoice.value).trim())
/** The canonically-authored block class: only an SRD class rides
 * `identity.class`; custom text lands in the record's
 * class_profession only (the gate's identity.class is an SRD vocab). */
const blockClass = computed(() =>
  classChoice.value !== CLASS_NONE && classChoice.value !== CLASS_OTHER ? classChoice.value : '',
)
const classProfession = computed(() =>
  classChoice.value === CLASS_OTHER ? classCustom.value.trim() : classChoice.value === CLASS_NONE ? '' : classChoice.value,
)

// --- The AR24 prose slots ---------------------------------------------------

const RECORD_FIELDS = [
  'personality',
  'secret',
  'rumor',
  'party_hook',
  'appearance',
  'background',
  'goals',
  'relationships',
  'voice_style',
  'catchphrases',
] as const
const recordText = ref<Record<(typeof RECORD_FIELDS)[number], string>>({
  personality: '',
  secret: '',
  rumor: '',
  party_hook: '',
  appearance: '',
  background: '',
  goals: '',
  relationships: '',
  voice_style: '',
  catchphrases: '',
})

const WORLD_FIELDS = ['reputation', 'factions', 'current_location', 'reaction_matrix', 'on_defeat'] as const
const worldIntegration = ref<Record<(typeof WORLD_FIELDS)[number], string>>({
  reputation: '',
  factions: '',
  current_location: '',
  reaction_matrix: '',
  on_defeat: '',
})

const BOSS_FIELDS = ['lair_actions', 'legendary_actions', 'immunities', 'vulnerabilities'] as const
const bossText = ref<Record<(typeof BOSS_FIELDS)[number], string>>({
  lair_actions: '',
  legendary_actions: '',
  immunities: '',
  vulnerabilities: '',
})

// --- Stat block (the committed structured editor; identity is THIS form's) --

const statBlockDraft = ref<Record<string, unknown> | null>(null)

// --- Declared relations (three-tier pickers) --------------------------------

const relationDrafts = ref<RelationDraft[]>([])

function addRelation() {
  relationDrafts.value.push({
    type: 'relationship',
    counter: '',
    tier: 'entity',
    targetId: '',
    targetKey: '',
    targetName: '',
  })
}

function removeRelation(index: number) {
  relationDrafts.value.splice(index, 1)
}

// --- Draft / payload assembly ------------------------------------------------

const sheetKey = computed(() => `staged-${props.index + 1}`)

function levelCrText(): string {
  if (role.value === 'Monster') {
    return crValue.value.trim() !== '' ? `CR ${crValue.value.trim()}` : ''
  }
  return levelValue.value !== null ? `level ${levelValue.value}` : ''
}

function parseIntSafe(value: string): number | null {
  const parsed = Number.parseInt(value.trim(), 10)
  return Number.isFinite(parsed) ? parsed : null
}

/** The canonical payload draft of this sheet — every required key is
 * present ('' where unfilled) so the mirror validator sees exactly what
 * the backend gate would reject. Callers must copy/mutate nothing. */
function buildSheet(): CharacterSheet {
  const record: Record<string, unknown> = {
    name: name.value.trim(),
    role: role.value,
    personality: recordText.value.personality.trim(),
    secret: recordText.value.secret.trim(),
    rumor: recordText.value.rumor.trim(),
    party_hook: recordText.value.party_hook.trim(),
    appearance: recordText.value.appearance.trim(),
    background: recordText.value.background.trim(),
    goals: recordText.value.goals.trim(),
    relationships: recordText.value.relationships.trim(),
    voice_style: recordText.value.voice_style.trim(),
    catchphrases: recordText.value.catchphrases.trim(),
    level_cr: levelCrText(),
    race_type: race.value,
    class_profession: classProfession.value,
    alignment: alignment.value,
  }

  const identity: Record<string, unknown> = { role: role.value, race: race.value }
  if (role.value === 'Monster') {
    if (crValue.value.trim() !== '') identity.cr = crValue.value.trim()
  } else if (levelValue.value !== null) {
    identity.level = levelValue.value
  }
  if (blockClass.value !== '') identity.class = blockClass.value
  if (alignment.value !== '') identity.alignment = alignment.value
  record.stat_block = { ...(statBlockDraft.value ?? {}), identity }

  record.world_integration = {
    reputation: worldIntegration.value.reputation.trim(),
    factions: worldIntegration.value.factions.trim(),
    current_location: worldIntegration.value.current_location.trim(),
    reaction_matrix: worldIntegration.value.reaction_matrix.trim(),
    on_defeat: worldIntegration.value.on_defeat.trim(),
  }

  if (role.value === 'BBEG' || role.value === 'Monster') {
    record.boss = {
      lair_actions: bossText.value.lair_actions.trim(),
      legendary_actions: bossText.value.legendary_actions.trim(),
      immunities: bossText.value.immunities.trim(),
      vulnerabilities: bossText.value.vulnerabilities.trim(),
    }
  }

  const relations = buildRelations()
  // The sheet is assembled from live refs, so its static shape cannot be
  // proven against the canonical CharacterRecord — the mirror validator
  // and the API gate own that proof (a strict cast would fail because the
  // required-key completeness is a RUNTIME property, e.g. stat_block's
  // required subsections only appear once the DM authors them).
  return {
    key: sheetKey.value,
    record,
    ...(relations ? { relations } : {}),
  } as unknown as CharacterSheet
}

/** Declared relations in the wire shape. Tier-3 unresolved names ride as
 * `target_name` so the mirror (and the enqueue gate, should a bypass
 * sneak through) name the path-2 ban with the backend's exact wording —
 * the form's submit button is disabled the moment one appears. */

/** The draft wire shape: as the canonical DeclaredRelation but with the
 * path-2-banned tier-3 ``target_name`` riding for the mirror's ban
 * wording. A target_name can never actually reach the wire — submit is
 * disabled while one exists. */
type RelationDraftWire = {
  type: string
  counter?: number
  target_id?: string
  target_key?: string
  target_name?: string
}

function buildRelations(): DeclaredRelation[] | undefined {
  const out: DeclaredRelation[] = []
  for (const draft of relationDrafts.value) {
    let target: Partial<RelationDraftWire> | null = null
    if (draft.tier === 'entity' && draft.targetId.trim()) {
      target = { target_id: draft.targetId.trim() }
    } else if (draft.tier === 'staged' && draft.targetKey.trim()) {
      target = { target_key: draft.targetKey.trim() }
    } else if (draft.tier === 'name' && draft.targetName.trim()) {
      target = { target_name: draft.targetName.trim() }
    }
    if (!target) continue
    const relation: DeclaredRelation = { type: draft.type, ...target }
    const counter = parseIntSafe(draft.counter)
    if (counter !== null && Number.isInteger(counter)) relation.counter = counter
    out.push(relation)
  }
  return out.length > 0 ? out : undefined
}

function emitChange() {
  emit('change')
}

/** One change signal for the containing view: any mutation of the sheet's
 * draft state (identity, prose, stat block via v-model replacement,
 * relation rows) is visible to this watch, so the parent recomputes the
 * mirror violations exactly once per keystroke/select. */
watch(
  () => JSON.stringify(buildSheet()),
  () => emitChange(),
)

defineExpose({
  buildSheet,
  get sheetName() {
    return name.value.trim()
  },
  get sheetKey() {
    return sheetKey.value
  },
})
</script>

<template>
  <div class="sheet">
    <div class="identity-grid">
      <label class="field">
        Name
        <input v-model="name" type="text" aria-label="Character name" />
      </label>
      <label class="field">
        Role
        <select v-model="role" aria-label="Role">
          <option v-for="option in ROLES" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
      <label class="field">
        Race / type
        <select v-model="raceChoice" aria-label="Race / type">
          <option v-for="option in SRD_RACES" :key="option" :value="option">{{ option }}</option>
          <option :value="RACE_OTHER">(other…)</option>
        </select>
      </label>
      <input
        v-if="raceChoice === RACE_OTHER"
        v-model="raceCustom"
        type="text"
        placeholder="Race / type (e.g. Dragon)"
        aria-label="Custom race"
      />
      <label v-if="role !== 'Monster'" class="field">
        Level (1–20)
        <input v-model.number="levelValue" type="number" min="1" max="20" aria-label="Level" />
      </label>
      <label v-else class="field">
        CR (0–30 or a fraction like 1/2)
        <input v-model="crValue" type="text" placeholder="e.g. 12 or 1/2" aria-label="CR" />
      </label>
      <label class="field">
        Class / profession
        <select v-model="classChoice" aria-label="Class / profession">
          <option :value="CLASS_NONE">(none)</option>
          <option v-for="option in SRD_CLASSES" :key="option" :value="option">{{ option }}</option>
          <option :value="CLASS_OTHER">(other…)</option>
        </select>
      </label>
      <input
        v-if="classChoice === CLASS_OTHER"
        v-model="classCustom"
        type="text"
        placeholder="Class / profession (e.g. Guildmaster)"
        aria-label="Custom class / profession"
      />
      <label class="field">
        Alignment
        <select v-model="alignment" aria-label="Alignment">
          <option value="">—</option>
          <option v-for="option in ALIGNMENTS" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
    </div>

    <div class="prose-grid">
      <label v-for="field in RECORD_FIELDS" :key="field" class="field">
        {{ FIELD_LABELS[field] }}
        <textarea v-model="recordText[field]" rows="2" :aria-label="FIELD_LABELS[field]"></textarea>
      </label>
    </div>

    <h4>World integration</h4>
    <div class="prose-grid">
      <label v-for="field in WORLD_FIELDS" :key="field" class="field">
        {{ FIELD_LABELS[field] }}
        <textarea v-model="worldIntegration[field]" rows="2" :aria-label="`world_integration.${field}`"></textarea>
      </label>
    </div>

    <h4 v-if="role === 'BBEG' || role === 'Monster'">Boss section</h4>
    <div v-if="role === 'BBEG' || role === 'Monster'" class="prose-grid">
      <label v-for="field in BOSS_FIELDS" :key="field" class="field">
        {{ FIELD_LABELS[field] }}
        <textarea v-model="bossText[field]" rows="2" :aria-label="`boss.${field}`"></textarea>
      </label>
    </div>

    <h4>Stat block</h4>
    <StatBlockEditor v-model="statBlockDraft" :hide-identity="true" />

    <div class="relations">
      <h4>Relations</h4>
      <p class="muted small">
        Aim each relation at a committed entity (search below), another character staged in this
        submission, or type a name to resolve it against the committed world.
      </p>
      <div v-for="(draft, relationIndex) in relationDrafts" :key="relationIndex" class="relation-row">
        <RelationTargetPicker
          v-model="relationDrafts[relationIndex]"
          :group-name="`sheet-${props.index}-relation-${relationIndex}`"
          :entities="props.entities"
          :staged="props.staged"
        />
        <button type="button" class="link" :aria-label="`Remove relation ${relationIndex + 1}`" @click="removeRelation(relationIndex)">
          Remove
        </button>
      </div>
      <button type="button" class="link" @click="addRelation">Add relation</button>
    </div>
  </div>
</template>

<style scoped>
.sheet {
  display: grid;
  gap: 0.75rem;
}
.identity-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.5rem;
}
.prose-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 0.5rem;
}
.field {
  display: grid;
  gap: 0.25rem;
}
input,
select,
textarea {
  padding: 0.4rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
}
.relations {
  margin-top: 0.25rem;
}
.relation-row {
  border: 1px solid #2c3038;
  border-radius: 6px;
  padding: 0.5rem;
  display: grid;
  gap: 0.35rem;
}
h4 {
  margin: 0.25rem 0;
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