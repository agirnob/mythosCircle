<script setup lang="ts">
// The shared character-sheet editor (owner decision, 2026-09-17): ONE
// authoring surface for the character forge AND the build-in's authored
// key figures. Everything the DM fills commits verbatim; blanks are the
// generator's job. The relation block renders only when a campaign is
// bound (a draft in the Generic library stores characters alone).
import { computed, onUnmounted, ref, watch } from 'vue'

import { apiFetch } from '../api/client'
import type { AuthoredFigureSeed, AuthoredRelationSeed } from '../stores/jobs'

const props = withDefaults(defineProps<{ campaignId: string; forgeWorkspace?: boolean }>(), {
  forgeWorkspace: false,
})
const mode = ref<'quick' | 'detailed'>(props.forgeWorkspace ? 'quick' : 'detailed')
const section = ref<'identity' | 'character' | 'mechanics' | 'connections' | 'review'>('identity')
const concept = ref('')
const optionalOpen = ref<Record<string, boolean>>({})

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
const powerSlot = computed<'level' | 'cr'>(() => role.value === 'Monster' ? 'cr' : 'level')
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
const spellDrafts = ref<Array<{ name: string; description: string }>>([])

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
function addSpell() { spellDrafts.value.push({ name: '', description: '' }) }

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

// Keep relations attached to their authored world rather than retargeting
// existing IDs or names when the destination changes.
const relationsByCampaign = ref<Record<string, RelationDraft[]>>({})
const relationDrafts = computed(() => relationsByCampaign.value[props.campaignId] ?? [])

interface EntityRef {
  id: string
  name: string
  kind: string
}
interface ExportShape {
  entities: EntityRef[]
}

const worldEntities = ref<EntityRef[]>([])

let entityGeneration = 0
watch(
  () => props.campaignId,
  async (campaignId) => {
    const generation = ++entityGeneration
    worldEntities.value = []
    if (!campaignId) return
    try {
      const response = await apiFetch<ExportShape>(
        `/api/campaigns/${encodeURIComponent(campaignId)}/export`,
      )
      if (generation !== entityGeneration || props.campaignId !== campaignId) return
      worldEntities.value = response.entities.map(({ id, name, kind }) => ({ id, name, kind }))
    } catch {
      // Never retain another world's options after a failed load.
      // Authored relation drafts remain available when returning here.
    }
  },
  { immediate: true, flush: 'sync' },
)
onUnmounted(() => { ++entityGeneration })

function addRelation() {
  const drafts = relationsByCampaign.value[props.campaignId] ??= []
  drafts.push({ type: 'located_in', mode: 'existing', target_id: '', target_name: '', counter: '' })
}

// --- Seed composition ---------------------------------------------------------

const hasContent = computed(() => Boolean(
  name.value.trim() || concept.value.trim() || recordText.value.background.trim(),
))

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

  const record: Record<string, unknown> = blankFree({
    ...recordText.value,
    background: '',
  })
  if (recordText.value.background.trim()) record.background = recordText.value.background
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
  if (Object.values(attributes.value).some((value) => value !== null)) {
    const attrs: Record<string, number> = {}
    for (const attr of ATTRIBUTES) {
      const value = attributes.value[attr]
      if (value !== null) attrs[attr] = value
    }
    if (Object.keys(attrs).length > 0) block.attributes = attrs
  }
  if (ac.value !== null || hp.value !== null || hitDiceCount.value !== null || hitDiceSides.value !== null || hitDiceMod.value.trim()) {
    const combat: Record<string, unknown> = {}
    if (ac.value !== null) combat.ac = ac.value
    if (hp.value !== null) combat.hp = hp.value
    const hitDice = composeDice(hitDiceCount.value, hitDiceSides.value, hitDiceMod.value)
    if (hitDice) combat.hit_dice = hitDice
    if (Object.keys(combat).length > 0) block.combat = combat
  }
  if (skillDrafts.value.length > 0) {
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
  if (actionDrafts.value.length > 0) {
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
  if (traitDrafts.value.length > 0) {
    const traits = traitDrafts.value
      .filter((t) => t.name.trim() && t.description.trim())
      .map((t) => ({ name: t.name.trim(), description: t.description.trim() }))
    if (traits.length > 0) block.traits = traits
  }
  if (spellDrafts.value.length > 0) {
    const spells = spellDrafts.value
      .filter((spell) => spell.name.trim())
      .map((spell) => ({
        name: spell.name.trim(),
        ...(spell.description.trim() ? { description: spell.description.trim() } : {}),
      }))
    if (spells.length > 0) block.spells = spells
  }
  if (Object.keys(block).length > 0) record.stat_block = block

  if (role.value !== 'NPC' && Object.values(bossFields.value).some((value) => value.trim())) {
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

const inputSummary = computed(() => {
  const authored: string[] = []
  const generated: string[] = []
  const supplied = (label: string, value: string) => {
    if (value.trim()) authored.push(`${label}: ${value.trim()}`)
  }

  supplied('Name', name.value)
  supplied('Species', identityRace.value)
  supplied('Class', identityClass.value)
  supplied('Alignment', identityAlignment.value)
  const power = powerSlot.value === 'cr' ? crValue.value : levelValue.value === null ? '' : String(levelValue.value)
  supplied(powerSlot.value === 'cr' ? 'Challenge rating' : 'Level', power)
  if (!name.value.trim()) generated.push('Name')
  if (!identityRace.value) generated.push('Species')
  if (!identityClass.value) generated.push('Class')
  if (!identityAlignment.value) generated.push('Alignment')
  if (!power) generated.push(powerSlot.value === 'cr' ? 'Challenge rating' : 'Level')

  for (const field of RECORD_FIELDS) {
    supplied(field.label, recordText.value[field.key])
    if (!recordText.value[field.key].trim()) generated.push(field.label)
  }
  for (const field of WORLD_INTEGRATION_FIELDS) {
    supplied(field.label, worldIntegration.value[field.key])
    if (!worldIntegration.value[field.key].trim()) generated.push(field.label)
  }
  for (const relation of relationDrafts.value) {
    const target = relation.mode === 'existing' ? worldEntities.value.find((entity) => entity.id === relation.target_id)?.name : relation.target_name
    if (target?.trim()) authored.push(`Relation: ${relation.type.replaceAll('_', ' ')} → ${target.trim()}`)
  }
  if (relationDrafts.value.length === 0) generated.push('Relations')

  const authoredAttributes = ATTRIBUTES.filter((attr) => attributes.value[attr] !== null)
  if (authoredAttributes.length) authored.push(`Attributes: ${authoredAttributes.map((attr) => `${attr.toUpperCase()} ${attributes.value[attr]}`).join(', ')}`)
  else generated.push('Attributes')
  const combatParts = [
    ac.value === null ? '' : `AC ${ac.value}`,
    hp.value === null ? '' : `HP ${hp.value}`,
    composeDice(hitDiceCount.value, hitDiceSides.value, hitDiceMod.value) ?? '',
  ].filter(Boolean)
  if (combatParts.length) authored.push(`Combat: ${combatParts.join(', ')}`)
  else generated.push('Combat')

  const rows = (label: string, values: Array<string>) => {
    const present = values.filter((value) => value.trim())
    if (present.length) authored.push(...present.map((value) => `${label}: ${value.trim()}`))
    else generated.push(labelPlural(label))
  }
  rows('Skill', skillDrafts.value.map((row) => [row.name, row.bonus, row.description].filter((part) => part.trim()).join(' — ')))
  rows('Action', actionDrafts.value.map((row) => [row.name, row.description, composeDice(row.damageCount, row.damageSides, row.damageMod)].filter(Boolean).join(' — ')))
  rows('Trait', traitDrafts.value.map((row) => [row.name, row.description].filter((part) => part.trim()).join(' — ')))
  rows('Spell', spellDrafts.value.map((row) => [row.name, row.description].filter((part) => part.trim()).join(' — ')))

  if (role.value !== 'NPC') {
    const bossValues = Object.entries(bossFields.value).filter(([, value]) => value.trim())
    if (bossValues.length) authored.push(...bossValues.map(([key, value]) => `${key.replaceAll('_', ' ')}: ${value.trim()}`))
    else generated.push('Boss details')
  }
  return { authored, generated }
})

function labelPlural(label: string): string {
  return label === 'Spell' ? 'Spells' : `${label}s`
}

defineExpose({ buildFigure, hasContent, mode, concept })
</script>

<template>
  <div class="workspace">
    <div class="editor">
      <nav v-if="forgeWorkspace" class="mode-tabs" aria-label="Forge mode">
        <button type="button" role="tab" :aria-selected="mode === 'quick'" @click="mode = 'quick'">Quick generate</button>
        <button type="button" role="tab" :aria-selected="mode === 'detailed'" @click="mode = 'detailed'">Detailed builder</button>
      </nav>

      <template v-if="mode === 'quick'">
        <h2>Make a character from an idea</h2>
        <label class="wide-field">Character concept
          <textarea v-model="concept" maxlength="2000" rows="6" placeholder="A retired dragon hunter who secretly funds the creatures she once pursued…"></textarea>
          <small>Describe the character you want. This guides generation and is not saved as authored Background.</small>
        </label>
        <div class="grid aligned">
          <label>Name (optional)<input v-model="name" type="text" maxlength="200" placeholder="Generated if left blank" /></label>
          <label>Power
            <select v-model="role"><option value="NPC">NPC</option><option value="BBEG">BBEG</option><option value="Monster">Monster</option></select>
            <small>{{ role === 'NPC' ? 'A person who can support or complicate an adventure.' : role === 'BBEG' ? 'A major antagonist with a larger plan.' : 'A creature whose nature and abilities shape an encounter.' }}</small>
          </label>
          <label>Species (optional)
            <select v-model="identityRace"><option value="">Let the model choose</option><option v-for="race in SRD_RACES" :key="race" :value="race">{{ race }}</option></select>
            <small>Guides heritage, appearance and perspective.</small>
          </label>
          <label>Class (optional)
            <select v-model="identityClass"><option value="">Let the model choose</option><option v-for="cls in SRD_CLASSES" :key="cls" :value="cls">{{ cls }}</option></select>
            <small>Guides training, skills and spellcasting.</small>
          </label>
        </div>
        <label v-if="role === 'Monster'" class="compact-field">Challenge rating (optional)<input v-model="crValue" type="text" placeholder="Let the model choose" /></label>
        <label v-else class="compact-field">Level (optional)<input v-model.number="levelValue" type="number" min="1" max="20" placeholder="Let the model choose" /></label>
      </template>

      <template v-else>
        <nav class="section-tabs" aria-label="Detailed builder sections" role="tablist">
          <button v-for="item in [{key:'identity',label:'Identity'},{key:'character',label:'Character'},{key:'mechanics',label:'Mechanics'},{key:'connections',label:'Connections'},{key:'review',label:'Review'}]" :key="item.key" type="button" role="tab" :aria-selected="section === item.key" @click="section = item.key as typeof section">{{ item.label }}</button>
        </nav>
        <section v-show="section === 'identity'" class="section-content">
          <h2>Identity</h2><div class="grid aligned">
            <label>Name<input v-model="name" type="text" maxlength="200" placeholder="May be named from your Background" /></label>
            <label>Role<select v-model="role"><option v-for="r in ROLES" :key="r" :value="r">{{ r }}</option></select></label>
            <label>Species<select v-model="identityRace"><option value="">Let the model choose</option><option v-for="race in SRD_RACES" :key="race" :value="race">{{ race }}</option></select><small>Guides heritage and appearance.</small></label>
            <label>Class<select v-model="identityClass"><option value="">Let the model choose</option><option v-for="cls in SRD_CLASSES" :key="cls" :value="cls">{{ cls }}</option></select><small>Guides training and spellcasting.</small></label>
            <label v-if="role === 'Monster'">Challenge rating<input v-model="crValue" type="text" placeholder="Will generate" /></label>
            <label v-else>Level<input v-model.number="levelValue" type="number" min="1" max="20" placeholder="Will generate" /></label>
            <label>Alignment<select v-model="identityAlignment"><option value="">Will generate</option><option v-for="value in ALIGNMENTS" :key="value" :value="value">{{ value }}</option></select></label>
          </div>
        </section>

        <section v-show="section === 'character'" class="section-content">
          <h2>Character</h2><label class="wide-field">Background
            <textarea v-model="recordText.background" rows="12" placeholder="Write the story as you want it saved. A Background can also be used to name this character."></textarea>
            <small>Saved as written. Leave blank to have the build create one.</small>
          </label>
          <h3>Optional details</h3><div class="optional-grid">
            <div v-for="field in RECORD_FIELDS.filter((item) => item.key !== 'background')" :key="field.key" class="optional-field">
              <button type="button" class="expand-button" :aria-expanded="!!optionalOpen[field.key]" @click="optionalOpen[field.key] = !optionalOpen[field.key]">{{ field.label }} <span>{{ recordText[field.key].trim() ? 'Authored' : optionalOpen[field.key] ? 'Open' : 'Will generate' }}</span></button>
              <textarea v-show="optionalOpen[field.key]" v-model="recordText[field.key]" rows="3" :aria-label="field.label" placeholder="Leave blank to generate"></textarea>
            </div>
          </div>
        </section>

        <section v-show="section === 'mechanics'" class="section-content">
          <h2>Mechanics</h2><div class="mechanic-card">
            <button type="button" class="expand-button" :aria-expanded="authorAttributes" @click="authorAttributes = !authorAttributes">Attributes <span>{{ authorAttributes ? 'Authored' : 'Will generate' }}</span></button>
            <div v-if="authorAttributes" class="attribute-grid"><label v-for="attr in ATTRIBUTES" :key="attr">{{ attr.toUpperCase() }}<input v-model.number="attributes[attr]" type="number" min="1" max="30" /></label></div>
          </div>
          <div class="mechanic-card"><button type="button" class="expand-button" :aria-expanded="authorCombat" @click="authorCombat = !authorCombat">Combat <span>{{ authorCombat ? 'Authored' : 'Will generate' }}</span></button>
            <div v-if="authorCombat" class="grid"><label>AC<input v-model.number="ac" type="number" min="1" /></label><label>HP<input v-model.number="hp" type="number" min="1" /></label></div>
            <div v-if="authorCombat" class="dice-row"><span>Hit Dice</span><input v-model.number="hitDiceCount" type="number" min="1" placeholder="Dice" /><span>d</span><input v-model.number="hitDiceSides" type="number" min="1" placeholder="Sides" /><input v-model="hitDiceMod" type="text" placeholder="Modifier" /></div>
          </div>
          <div class="mechanic-card"><button type="button" class="expand-button" :aria-expanded="authorSkills" @click="authorSkills = !authorSkills">Skills <span>{{ skillDrafts.length ? `${skillDrafts.length} rows` : authorSkills ? 'Ready to author' : 'Will generate' }}</span></button><button v-if="authorSkills" type="button" class="link" @click="addSkill">Add skill</button>
            <div v-for="(skill, i) in authorSkills ? skillDrafts : []" :key="`s${i}`" class="row three"><input v-model="skill.name" aria-label="Skill name" placeholder="Religion" /><input v-model="skill.bonus" aria-label="Skill bonus" placeholder="Bonus" /><input v-model="skill.description" aria-label="Skill notes" placeholder="Notes" /><button type="button" :aria-label="`Remove skill ${i + 1}`" @click="skillDrafts.splice(i, 1)">Remove</button></div>
          </div>
          <div class="mechanic-card"><button type="button" class="expand-button" :aria-expanded="authorActions" @click="authorActions = !authorActions">Actions <span>{{ actionDrafts.length ? `${actionDrafts.length} rows` : authorActions ? 'Ready to author' : 'Will generate' }}</span></button><button v-if="authorActions" type="button" class="link" @click="addAction">Add action</button>
            <div v-for="(action, i) in authorActions ? actionDrafts : []" :key="`a${i}`" class="action-block"><div class="row"><input v-model="action.name" aria-label="Action name" placeholder="Longsword" /><input v-model="action.description" aria-label="Action description" placeholder="Describe the action" /><button type="button" :aria-label="`Remove action ${i + 1}`" @click="actionDrafts.splice(i, 1)">Remove</button></div><div class="dice-row"><input v-model.number="action.damageCount" aria-label="Damage dice" type="number" min="1" placeholder="Dice" /><span>d</span><input v-model.number="action.damageSides" aria-label="Damage sides" type="number" min="1" placeholder="Sides" /><input v-model="action.damageMod" aria-label="Damage modifier" placeholder="Modifier" /></div></div>
          </div>
          <div class="mechanic-card"><button type="button" class="expand-button" :aria-expanded="authorTraits" @click="authorTraits = !authorTraits">Traits <span>{{ traitDrafts.length ? `${traitDrafts.length} rows` : authorTraits ? 'Ready to author' : 'Will generate' }}</span></button><button v-if="authorTraits" type="button" class="link" @click="addTrait">Add trait</button><div v-for="(trait, i) in authorTraits ? traitDrafts : []" :key="`t${i}`" class="row"><input v-model="trait.name" aria-label="Trait name" placeholder="Magic Resistance" /><input v-model="trait.description" aria-label="Trait description" placeholder="Describe the trait" /><button type="button" :aria-label="`Remove trait ${i + 1}`" @click="traitDrafts.splice(i, 1)">Remove</button></div></div>
          <div class="mechanic-card"><button type="button" class="expand-button" :aria-expanded="authorSpells" @click="authorSpells = !authorSpells">Spells <span>{{ spellDrafts.length ? `${spellDrafts.length} rows` : authorSpells ? 'Ready to author' : 'Will generate' }}</span></button><button v-if="authorSpells" type="button" class="link" @click="addSpell">Add spell</button><div v-for="(spell, i) in authorSpells ? spellDrafts : []" :key="`spell${i}`" class="spell-row"><input v-model="spell.name" aria-label="Spell name" placeholder="Spell name" /><input v-model="spell.description" aria-label="Spell description" placeholder="Description (optional)" /><button type="button" :aria-label="`Remove spell ${i + 1}`" @click="spellDrafts.splice(i, 1)">Remove spell</button></div></div>
          <div v-if="role !== 'NPC'" class="mechanic-card"><button type="button" class="expand-button" :aria-expanded="authorBoss" @click="authorBoss = !authorBoss">Boss details <span>{{ authorBoss ? 'Authored' : 'Will generate' }}</span></button><div v-if="authorBoss" class="grid"><label v-for="(value, key) in bossFields" :key="key">{{ key.replaceAll('_', ' ') }}<textarea v-model="bossFields[key as keyof typeof bossFields]" rows="2"></textarea></label></div></div>
        </section>

        <section v-show="section === 'connections'" class="section-content">
          <h2>Connections</h2><h3>World integration</h3><div class="grid"><label v-for="field in WORLD_INTEGRATION_FIELDS" :key="field.key">{{ field.label }}<textarea v-model="worldIntegration[field.key]" rows="2" placeholder="Leave blank to generate"></textarea></label></div>
          <template v-if="campaignId"><h3>Relations</h3><p class="muted small">Relations stay with this world when you change destination.</p>
            <div v-for="(relation, i) in relationDrafts" :key="`r${i}`" class="relation-block"><div class="row four"><label>Type<select v-model="relation.type"><option v-for="edgeType in EDGE_TYPES" :key="edgeType" :value="edgeType">{{ edgeType.replaceAll('_', ' ') }}</option></select></label><label>Target<select v-model="relation.mode"><option value="existing">Existing</option><option value="new">Create new</option></select></label><select v-if="relation.mode === 'existing'" v-model="relation.target_id" aria-label="Related world entity"><option value="" disabled>Choose entity</option><option v-if="relation.target_id && !worldEntities.some((entity) => entity.id === relation.target_id)" :value="relation.target_id" disabled>Selected target unavailable ({{ relation.target_id }}) — reload or choose another target</option><option v-for="entity in worldEntities" :key="entity.id" :value="entity.id">{{ entity.name }} ({{ entity.kind }})</option></select><input v-else v-model="relation.target_name" aria-label="New relation target" placeholder="New entity name" /><button type="button" @click="relationDrafts.splice(i, 1)">Remove</button></div></div><button type="button" class="link" @click="addRelation">Add relation</button>
          </template>
        </section>
        <section v-show="section === 'review'" class="section-content"><h2>Review</h2><p><strong>{{ name.trim() || (concept.trim() || recordText.background.trim() ? 'Name will be generated' : 'Name needed') }}</strong> · {{ role }}</p><p>{{ concept.trim() ? 'Concept supplied' : 'No concept supplied' }}</p><div class="review-summary"><h3>Authored</h3><p v-for="item in inputSummary.authored" :key="`authored-${item}`">{{ item }}</p><p v-if="inputSummary.authored.length === 0">Nothing authored yet.</p><h3>Will generate</h3><p>{{ inputSummary.generated.join(' · ') || 'No blank fields' }}</p></div><p class="muted small">Only fields you supplied are sent as authored input. Generated fields remain the model’s work.</p></section>
      </template>
    </div>
    <aside class="summary" aria-label="Character input summary"><h2>Character summary</h2><p class="summary-name">{{ name.trim() || 'Unnamed character' }}</p><p>{{ role }} · {{ identityRace || 'Species will generate' }} · {{ identityClass || 'Class will generate' }}</p><div><strong>You supplied</strong><span>{{ [concept.trim() ? 'Concept' : '', ...inputSummary.authored].filter(Boolean).join(' · ') || 'No authored fields yet' }}</span></div><div><strong>Will generate</strong><span>{{ inputSummary.generated.join(' · ') || 'No blank fields' }}</span></div></aside>
  </div>
</template>

<style scoped>
.workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 24px;
  color: var(--mc-text-primary);
}
.editor, .summary, .mechanic-card, .optional-field {
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  background: var(--mc-surface);
}
.editor { padding: 24px; min-width: 0; }
.summary { padding: 20px; align-self: start; position: sticky; top: 16px; }
.summary h2 { margin-top: 0; font-size: 1.1rem; }
.summary div { border-top: 1px solid var(--mc-border); padding: 12px 0; display: grid; gap: 4px; }
.summary span, small { color: var(--mc-text-secondary); }
.summary-name { font: 1.4rem Georgia, serif; }
.review-summary { border-top: 1px solid var(--mc-border); margin-top: 16px; padding-top: 8px; }
.review-summary p { margin: 6px 0; overflow-wrap: anywhere; }
.mode-tabs, .section-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 24px; }
.mode-tabs button, .section-tabs button, .expand-button {
  min-height: 42px; border: 1px solid var(--mc-border-bright); border-radius: 7px;
  background: var(--mc-input); color: var(--mc-text-secondary); padding: 8px 14px; cursor: pointer;
}
.mode-tabs button[aria-selected="true"], .section-tabs button[aria-selected="true"] {
  color: var(--mc-text-primary); border-color: var(--mc-interactive); background: rgba(139,108,255,.12);
}
.section-content h2 { font-size: 1.25rem; }
.wide-field { display: grid; gap: 8px; margin-bottom: 20px; }
.wide-field textarea { min-height: 150px; }
.compact-field { max-width: 260px; margin-top: 16px; }
.aligned { align-items: start; }
.aligned label { min-height: 108px; align-content: start; }
.aligned small { line-height: 1.4; }
.optional-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }
.optional-field { padding: 10px; }
.optional-field textarea { margin-top: 8px; }
.expand-button { width: 100%; display:flex; align-items:center; justify-content:space-between; text-align:left; }
.expand-button span { color: var(--mc-text-muted); font-size: .85rem; }
.mechanic-card { padding: 12px; margin: 10px 0; }
.attribute-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; margin-top:12px; }
.attribute-grid label { text-align:center; }
.spell-row { display:grid; grid-template-columns:1fr 2fr auto; gap:8px; margin-top:10px; }
.row { display:grid; grid-template-columns:1fr 2fr auto; gap:8px; margin:10px 0; }
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
  width: 100%; min-width: 0; min-height: 42px; padding: 0.6rem 0.75rem;
  border-radius: var(--mc-radius-sm); border: 1px solid var(--mc-border-bright);
  background: var(--mc-input); color: var(--mc-text-primary); font: inherit;
}
textarea { resize: vertical; min-height: 72px; }
.row.four { grid-template-columns: 1fr 1fr 2fr auto; }
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
  color: var(--mc-danger);
}
.muted {
  color: var(--mc-text-secondary);
}
.muted.small {
  font-size: 0.85rem;
}
.link {
  background: none;
  border: 1px solid var(--mc-border);
  border-radius: 7px;
  color: var(--mc-interactive-bright);
  cursor: pointer;
  padding: 8px 12px;
  font: inherit;
}
.link:hover {
  text-decoration: underline;
}
input:focus-visible, select:focus-visible, textarea:focus-visible, button:focus-visible { outline:2px solid var(--mc-interactive-bright); outline-offset:2px; }
@media (max-width: 900px) { .workspace { grid-template-columns:1fr; } .summary { position:static; } }
@media (max-width: 520px) {
  .editor { padding:14px; }
  .section-tabs { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); }
  .section-tabs button { padding:6px 4px; font-size:.85rem; }
  .optional-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .attribute-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .spell-row { grid-template-columns:1fr; }
  .row, .row.four { grid-template-columns:1fr; }
}
</style>
