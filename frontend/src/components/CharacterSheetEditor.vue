<script setup lang="ts">
// The shared character-sheet editor (owner decision, 2026-09-17): ONE
// authoring surface for the character forge AND the build-in's authored
// key figures. Everything the DM fills commits verbatim; blanks are the
// generator's job. The relation block renders only when a campaign is
// bound (a draft in the Generic library stores characters alone).
import { computed, onMounted, ref, watch } from 'vue'

import { apiFetch } from '../api/client'
import type { AuthoredFigureSeed, AuthoredRelationSeed } from '../stores/jobs'

const props = defineProps<{ campaignId: string }>()

// --- Vocabulary constants (the canonical sets, mirrored in
// ../api/characterSchema.ts and store/direct.py) ---------------------------

type RecordTextField = 'personality' | 'secret' | 'rumor' | 'party_hook' | 'appearance' |
  'background' | 'goals' | 'relationships' | 'voice_style' | 'catchphrases'

const RECORD_FIELDS: Array<{ key: RecordTextField; label: string }> = [
  { key: 'personality', label: 'Personality' },
  { key: 'secret', label: 'Secret' },
  { key: 'rumor', label: 'Rumor' },
  { key: 'party_hook', label: 'Party hook' },
  { key: 'appearance', label: 'Appearance' },
  { key: 'background', label: 'Background' },
  { key: 'goals', label: 'Goals' },
  { key: 'relationships', label: 'Relationships' },
  { key: 'voice_style', label: 'Voice style' },
  { key: 'catchphrases', label: 'Catchphrases' },
]

const ROLES = ['NPC', 'BBEG', 'Monster'] as const

type WorldIntegrationField = 'reputation' | 'factions' | 'current_location' | 'reaction_matrix' | 'on_defeat'

const WORLD_INTEGRATION_FIELDS: Array<{ key: WorldIntegrationField; label: string }> = [
  { key: 'reputation', label: 'Reputation' },
  { key: 'factions', label: 'Factions' },
  { key: 'current_location', label: 'Current location' },
  { key: 'reaction_matrix', label: 'Reaction matrix' },
  { key: 'on_defeat', label: 'On defeat' },
]

const SRD_RACES = [
  'Dragonborn',
  'Dwarf',
  'Elf',
  'Gnome',
  'Half-Elf',
  'Half-Orc',
  'Halfling',
  'Human',
  'Tiefling',
] as const

const SRD_CLASSES = [
  'Barbarian',
  'Bard',
  'Cleric',
  'Druid',
  'Fighter',
  'Monk',
  'Paladin',
  'Ranger',
  'Rogue',
  'Sorcerer',
  'Warlock',
  'Wizard',
] as const

const ALIGNMENTS = [
  'LG',
  'NG',
  'CG',
  'LN',
  'N',
  'CN',
  'LE',
  'NE',
  'CE',
  'unaligned',
] as const

const ATTRIBUTES = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const

const EDGE_TYPES = [
  'relationship',
  'debt',
  'grudge',
  'loyalty',
  'member_of',
  'located_in',
  'rival_of',
  'kin_of',
  'ally_of',
  'enemy_of',
  'bases_at',
  'controls',
  'employs',
  'worships',
  'hails_from',
  'protects',
] as const

// --- Form state -------------------------------------------------------------

const name = ref('')
const role = ref<'NPC' | 'BBEG' | 'Monster'>('NPC')
const recordText = ref<Record<RecordTextField, string>>({
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
const worldIntegration = ref<Record<WorldIntegrationField, string>>({
  reputation: '',
  factions: '',
  current_location: '',
  reaction_matrix: '',
  on_defeat: '',
})

// --- Authored stat-block subsections (each optional independently) ----------

const identityRace = ref('')
const powerSlot = ref<'level' | 'cr'>('level')
const levelValue = ref<number | null>(null)
const crValue = ref('')
const identityClass = ref('')
const identityAlignment = ref('')

const authorAttributes = ref(false)
const attributes = ref<Record<(typeof ATTRIBUTES)[number], number | null>>({
  str: null,
  dex: null,
  con: null,
  int: null,
  wis: null,
  cha: null,
})

const authorCombat = ref(false)
const ac = ref<number | null>(null)
const hp = ref<number | null>(null)
const hitDiceCount = ref<number | null>(null)
const hitDiceSides = ref<number | null>(null)
const hitDiceMod = ref('')

const authorSkills = ref(false)
const skillDrafts = ref<Array<{ name: string; bonus: string; description: string }>>([])

const authorActions = ref(false)
interface ActionDraft {
  name: string
  description: string
  damageCount: number | null
  damageSides: number | null
  damageMod: string
}
const actionDrafts = ref<ActionDraft[]>([])

const authorTraits = ref(false)
const traitDrafts = ref<Array<{ name: string; description: string }>>([])

const authorSpells = ref(false)
const spellText = ref('')

const authorBoss = ref(false)
const bossFields = ref({ lair_actions: '', legendary_actions: '', immunities: '', vulnerabilities: '' })

function addSkill() {
  skillDrafts.value.push({ name: '', bonus: '', description: '' })
}
function addAction() {
  actionDrafts.value.push({ name: '', description: '', damageCount: null, damageSides: null, damageMod: '' })
}
function addTrait() {
  traitDrafts.value.push({ name: '', description: '' })
}

/** The dice boxes compose the canonical formula: [count]d[sides]+[mod].
 * Both numbers required; the modifier optional (signed integer). */
function composeDice(count: number | null, sides: number | null, mod: string): string | null {
  if (count === null && sides === null && mod.trim() === '') return null
  if (count === null || sides === null) return null
  const m = mod.trim()
  if (m !== '' && !/^[+-]?\d+$/.test(m)) return null
  const modStr = m === '' ? '' : m.startsWith('-') ? m : `+${m}`
  return `${count}d${sides}${modStr}`
}

// --- Declared relations (bound worlds only) ----------------------------------

interface RelationDraft {
  type: string
  mode: 'existing' | 'new'
  target_id: string
  target_name: string
  counter: string
}

const relationDrafts = ref<RelationDraft[]>([])

interface EntityRef {
  id: string
  name: string
  kind: string
}
interface ExportShape {
  entities: EntityRef[]
}

const worldEntities = ref<EntityRef[]>([])

async function loadWorldEntities() {
  if (!props.campaignId) return
  try {
    const response = await apiFetch<ExportShape>(
      `/api/campaigns/${encodeURIComponent(props.campaignId)}/export`,
    )
    worldEntities.value = response.entities.map((entity: EntityRef) => ({
      id: entity.id,
      name: entity.name,
      kind: entity.kind,
    }))
  } catch {
    // The relation picker degrades to free-entry targets when the world
    // cannot be read; the build re-checks target existence either way.
  }
}

watch(
  () => props.campaignId,
  () => {
    void loadWorldEntities()
  },
)
onMounted(() => {
  void loadWorldEntities()
})

function addRelation() {
  relationDrafts.value.push({ type: 'located_in', mode: 'existing', target_id: '', target_name: '', counter: '' })
}

// --- Seed composition ---------------------------------------------------------

const hasContent = computed(() => name.value.trim().length > 0)

function blankFree(values: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(values)) {
    if (value.trim()) out[key] = value.trim()
  }
  return out
}

/** The seed entry: filled fields only — blanks are the generator's job. */
function buildFigure(): AuthoredFigureSeed {
  const entry: AuthoredFigureSeed = { name: name.value.trim(), role: role.value }

  const record: Record<string, unknown> = blankFree({ ...recordText.value })
  const integration = blankFree(worldIntegration.value)
  if (Object.keys(integration).length > 0) record.world_integration = integration

  const block: Record<string, unknown> = {}
  {
    const identity: Record<string, unknown> = blankFree({
      class: identityClass.value,
      alignment: identityAlignment.value,
    })
    if (identityRace.value) identity.race = identityRace.value
    if (powerSlot.value === 'level' && levelValue.value !== null) identity.level = levelValue.value
    if (powerSlot.value === 'cr' && crValue.value.trim()) identity.cr = crValue.value.trim()
    if (Object.keys(identity).length > 0) {
      block.identity = identity
      // ONE fact in two slots: the canonical identity the gates check AND
      // the record's display text — both derived from these dropdowns.
      record.race_type = identityRace.value
      record.class_profession = identityClass.value
      record.alignment = identityAlignment.value
      const levelCrText =
        powerSlot.value === 'level' && levelValue.value !== null
          ? `level ${levelValue.value}`
          : identity.cr
          ? `CR ${identity.cr}`
          : ''
      if (levelCrText) record.level_cr = levelCrText
      for (const key of ['race_type', 'class_profession', 'alignment'] as const) {
        const value = record[key]
        if (typeof value === 'string' && !value.trim()) delete record[key]
      }
    }
  }
  if (authorAttributes.value) {
    const attrs: Record<string, number> = {}
    for (const attr of ATTRIBUTES) {
      const value = attributes.value[attr]
      if (value !== null) attrs[attr] = value
    }
    if (Object.keys(attrs).length > 0) block.attributes = attrs
  }
  if (authorCombat.value) {
    const combat: Record<string, unknown> = {}
    if (ac.value !== null) combat.ac = ac.value
    if (hp.value !== null) combat.hp = hp.value
    const hitDice = composeDice(hitDiceCount.value, hitDiceSides.value, hitDiceMod.value)
    if (hitDice) combat.hit_dice = hitDice
    if (Object.keys(combat).length > 0) block.combat = combat
  }
  if (authorSkills.value) {
    const skills = skillDrafts.value
      .filter((s) => s.name.trim())
      .map((s) => {
        const skill: Record<string, unknown> = { name: s.name.trim() }
        if (s.bonus.trim()) skill.bonus = Number.parseInt(s.bonus, 10)
        if (s.description.trim()) skill.description = s.description.trim()
        return skill
      })
    if (skills.length > 0) block.skills = skills
  }
  if (authorActions.value) {
    const actions = actionDrafts.value
      .filter((a) => a.name.trim() && a.description.trim())
      .map((a) => {
        const action: Record<string, unknown> = {
          name: a.name.trim(),
          description: a.description.trim(),
        }
        const damage = composeDice(a.damageCount, a.damageSides, a.damageMod)
        if (damage) action.damage = damage
        return action
      })
    if (actions.length > 0) block.actions = actions
  }
  if (authorTraits.value) {
    const traits = traitDrafts.value
      .filter((t) => t.name.trim() && t.description.trim())
      .map((t) => ({ name: t.name.trim(), description: t.description.trim() }))
    if (traits.length > 0) block.traits = traits
  }
  if (authorSpells.value) {
    const spells = spellText.value
      .split(/\r?\n/)
      .map((line: string) => line.trim())
      .filter(Boolean)
    if (spells.length > 0) block.spells = spells
  }
  if (Object.keys(block).length > 0) record.stat_block = block

  if (authorBoss.value && role.value !== 'NPC') {
    const boss = blankFree(bossFields.value)
    if (Object.keys(boss).length > 0) record.boss = boss
  }

  if (Object.keys(record).length > 0) entry.record = record

  if (props.campaignId) {
    const relations: AuthoredRelationSeed[] = []
    for (const draft of relationDrafts.value) {
      const target = draft.mode === 'existing' ? draft.target_id.trim() : draft.target_name.trim()
      if (!target) continue
      const relation: AuthoredRelationSeed =
        draft.mode === 'existing'
          ? { type: draft.type, target_id: target }
          : { type: draft.type, target_name: target }
      const counter = Number.parseInt(draft.counter, 10)
      if (draft.counter.trim() && Number.isFinite(counter)) relation.counter = counter
      relations.push(relation)
    }
    if (relations.length > 0) entry.relations = relations
  }

  return entry
}

defineExpose({ buildFigure, hasContent })
</script>

<template>
  <div class="editor">
    <h2>Sheet</h2>
    <div class="grid">
      <label>
        Name
        <input v-model="name" type="text" required />
      </label>
      <label>
        Role
        <select v-model="role">
          <option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option>
        </select>
      </label>
    </div>
    <div class="grid">
      <label v-for="field in RECORD_FIELDS" :key="field.key">
        {{ field.label }}
        <textarea
          v-model="recordText[field.key]"
          rows="2"
          placeholder="blank = generated"
        ></textarea>
      </label>
    </div>

    <h3>World integration</h3>
    <div class="grid">
      <label v-for="field in WORLD_INTEGRATION_FIELDS" :key="field.key">
        {{ field.label }}
        <textarea
          v-model="worldIntegration[field.key]"
          rows="2"
          placeholder="blank = generated"
        ></textarea>
      </label>
    </div>

    <h2>
      <button type="button" class="link" @click="authorBoss = !authorBoss">
        {{ authorBoss ? '☑' : '☐' }} Boss section
      </button>
      <span v-if="role === 'NPC'" class="muted small"> ({{ role }} — the build skips this) </span>
    </h2>
    <div v-if="authorBoss && role !== 'NPC'" class="grid">
      <label v-for="(value, key) in bossFields" :key="key">
        {{ key.replaceAll('_', ' ') }}
        <textarea v-model="bossFields[key as keyof typeof bossFields]" rows="2"></textarea>
      </label>
    </div>

    <h2>Stat block — author any part, or leave it all to the build</h2>
    <div class="subsections">
      <section class="subsection">
        <h3>Identity</h3>
        <div class="grid">
          <label>
            Race (SRD)
            <select v-model="identityRace">
              <option value="">— generated —</option>
              <option v-for="race in SRD_RACES" :key="race" :value="race">{{ race }}</option>
            </select>
          </label>
          <label>
            Power slot
            <select v-model="powerSlot">
              <option value="level">Level (NPC/BBEG)</option>
              <option value="cr">CR (Monster)</option>
            </select>
          </label>
          <label v-if="powerSlot === 'level'">
            Level (1–20)
            <input v-model.number="levelValue" type="number" min="1" max="20" />
          </label>
          <label v-if="powerSlot === 'cr'">
            CR (integer or '1/2')
            <input v-model="crValue" type="text" />
          </label>
          <label>
            Class (SRD)
            <select v-model="identityClass">
              <option value="">— generated —</option>
              <option v-for="cls in SRD_CLASSES" :key="cls" :value="cls">{{ cls }}</option>
            </select>
          </label>
          <label>
            Alignment
            <select v-model="identityAlignment">
              <option value="">— generated —</option>
              <option
                v-for="alignmentOption in ALIGNMENTS"
                :key="alignmentOption"
                :value="alignmentOption"
              >
                {{ alignmentOption }}
              </option>
            </select>
          </label>
        </div>
      </section>

      <section class="subsection">
        <h3>
          <button type="button" class="link" @click="authorAttributes = !authorAttributes">
            {{ authorAttributes ? '☑' : '☐' }} Attributes
          </button>
        </h3>
        <div v-if="authorAttributes" class="grid">
          <label v-for="attr in ATTRIBUTES" :key="attr">
            {{ attr.toUpperCase() }}
            <input v-model.number="attributes[attr]" type="number" min="1" max="30" />
          </label>
        </div>
      </section>

      <section class="subsection">
        <h3>
          <button type="button" class="link" @click="authorCombat = !authorCombat">
            {{ authorCombat ? '☑' : '☐' }} Combat
          </button>
        </h3>
        <div v-if="authorCombat" class="grid">
          <label>
            AC
            <input v-model.number="ac" type="number" min="0" />
          </label>
          <label>
            HP
            <input v-model.number="hp" type="number" min="1" />
          </label>
        </div>
        <div v-if="authorCombat" class="dice-row">
          <span class="muted small">Hit dice</span>
          <input v-model.number="hitDiceCount" type="number" min="1" placeholder="dice" />
          <span>d</span>
          <input v-model.number="hitDiceSides" type="number" min="1" placeholder="sides" />
          <span>+</span>
          <input v-model="hitDiceMod" type="text" placeholder="mod" />
        </div>
      </section>

      <section class="subsection">
        <h3>
          <button type="button" class="link" @click="authorSkills = !authorSkills">
            {{ authorSkills ? '☑' : '☐' }} Skills
          </button>
          <button v-if="authorSkills" type="button" class="link" @click="addSkill">+ add</button>
        </h3>
        <div v-for="(skill, i) in skillDrafts" :key="`s${i}`" class="row three">
          <input v-model="skill.name" type="text" placeholder="Religion" />
          <input v-model="skill.bonus" type="text" placeholder="bonus (optional)" />
          <input v-model="skill.description" type="text" placeholder="notes (optional)" />
          <button type="button" class="link" @click="skillDrafts.splice(i, 1)">✕</button>
        </div>
      </section>

      <section class="subsection">
        <h3>
          <button type="button" class="link" @click="authorActions = !authorActions">
            {{ authorActions ? '☑' : '☐' }} Actions
          </button>
          <button v-if="authorActions" type="button" class="link" @click="addAction">+ add</button>
        </h3>
        <div v-for="(action, i) in actionDrafts" :key="`a${i}`" class="action-block">
          <div class="row three">
            <input v-model="action.name" type="text" placeholder="Longsword" />
            <input
              v-model="action.description"
              type="text"
              placeholder="Melee Weapon Attack: +5 to hit…"
            />
            <button type="button" class="link" @click="actionDrafts.splice(i, 1)">✕</button>
          </div>
          <div class="dice-row">
            <span class="muted small">Damage</span>
            <input v-model.number="action.damageCount" type="number" min="1" placeholder="dice" />
            <span>d</span>
            <input v-model.number="action.damageSides" type="number" min="1" placeholder="sides" />
            <span>+</span>
            <input v-model="action.damageMod" type="text" placeholder="mod" />
          </div>
        </div>
      </section>

      <section class="subsection">
        <h3>
          <button type="button" class="link" @click="authorTraits = !authorTraits">
            {{ authorTraits ? '☑' : '☐' }} Traits
          </button>
          <button v-if="authorTraits" type="button" class="link" @click="addTrait">+ add</button>
        </h3>
        <div v-for="(trait, i) in traitDrafts" :key="`t${i}`" class="row three">
          <input v-model="trait.name" type="text" placeholder="Magic Resistance" />
          <input v-model="trait.description" type="text" placeholder="Advantage on saves…" />
          <span></span>
          <button type="button" class="link" @click="traitDrafts.splice(i, 1)">✕</button>
        </div>
      </section>

      <section class="subsection">
        <h3>
          <button type="button" class="link" @click="authorSpells = !authorSpells">
            {{ authorSpells ? '☑' : '☐' }} Spells
          </button>
        </h3>
        <textarea
          v-if="authorSpells"
          v-model="spellText"
          rows="3"
          placeholder="one per line — must be on the class's spell list"
        ></textarea>
      </section>
    </div>

    <template v-if="campaignId">
      <h2>Relations</h2>
      <p class="muted small">
        Wire the sheet to an entity already in the world, or name a NEW one — the build creates
        it (the relation's type decides what: located in → a place, member of → faction…).
      </p>
      <div v-for="(relation, i) in relationDrafts" :key="`r${i}`" class="relation-block">
        <div class="row four">
          <select v-model="relation.type">
            <option v-for="edgeType in EDGE_TYPES" :key="edgeType" :value="edgeType">
              {{ edgeType.replaceAll('_', ' ') }}
            </option>
          </select>
          <select v-model="relation.mode">
            <option value="existing">existing</option>
            <option value="new">create new</option>
          </select>
          <select v-if="relation.mode === 'existing'" v-model="relation.target_id">
            <option value="" disabled>pick a committed entity…</option>
            <option v-for="entity in worldEntities" :key="entity.id" :value="entity.id">
              {{ entity.name }} ({{ entity.kind }})
            </option>
          </select>
          <input
            v-else
            v-model="relation.target_name"
            type="text"
            placeholder="e.g. Vaelmoor — created if missing"
          />
          <button type="button" class="link" @click="relationDrafts.splice(i, 1)">✕</button>
        </div>
      </div>
      <button type="button" class="link" @click="addRelation">+ relation</button>
    </template>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}
label {
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
.subsections {
  display: grid;
  gap: 0.75rem;
}
.subsection {
  border: 1px dashed #2c3038;
  border-radius: 6px;
  padding: 0.6rem;
}
.subsection h3 {
  margin: 0 0 0.5rem;
  display: flex;
  gap: 1rem;
  align-items: center;
}
.row.three {
  display: grid;
  grid-template-columns: 2fr 1fr 2fr auto;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
  align-items: center;
}
.row.four {
  display: grid;
  grid-template-columns: 1fr auto 2fr auto;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
  align-items: center;
}
.relation-block {
  margin-bottom: 0.4rem;
}
.action-block {
  margin-bottom: 0.6rem;
  display: grid;
  gap: 0.3rem;
}
.dice-row {
  display: grid;
  grid-template-columns: auto 5rem auto 5rem auto 5rem;
  gap: 0.4rem;
  align-items: center;
  margin-top: 0.5rem;
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
}
.error {
  color: #ff8c8c;
}
.muted {
  color: #9aa0a6;
}
.muted.small {
  font-size: 0.85rem;
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
