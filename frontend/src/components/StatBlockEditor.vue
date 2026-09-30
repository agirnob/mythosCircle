<script setup lang="ts">
// Structured stat-block editor (owner feedback 2026-09-17: committed
// characters were edited as raw JSON — a form instead). The editor
// round-trips the canonical block: subsections render as inputs, action
// damage parts as dice boxes ([count]d[sides]+[mod] + damage type), and
// the emitted value carries only filled fields — blanks are dropped,
// never written as empty strings. `power` is a server stamp: read-only.
import { computed } from 'vue'
import { ref, watch } from 'vue'

import SpellCards from './ui/SpellCards.vue'

const props = defineProps<{
  modelValue: Record<string, unknown> | null
  /** True when the parent owns the identity block (the fully-authored
   * Add-Character form authors role/race/level/CR/class/alignment once,
   * at sheet level, and injects them at build time) — the identity grid
   * does not render here; existing identity fields round-trip unchanged.
   * WorldView's profile editor leaves it
   * unset and keeps the built-in identity rows. */
  hideIdentity?: boolean
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: Record<string, unknown> | null): void }>()

const ROLES = ['NPC', 'BBEG', 'Monster'] as const
const ATTRIBUTES = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const

// --- Local editable state ----------------------------------------------------

const identityRole = ref('')
const identityRace = ref('')
const powerSlot = ref<'level' | 'cr'>('level')
const levelValue = ref<number | null>(null)
const crValue = ref('')
const identityClass = ref('')
const identityAlignment = ref('')

const attributes = ref<Record<string, number | null>>({
  str: null,
  dex: null,
  con: null,
  int: null,
  wis: null,
  cha: null,
})

const ac = ref<number | null>(null)
const hp = ref<number | null>(null)
const hitDice = ref('')

const powerStamp = ref<{ dpr?: number; band?: number[]; verdict?: string } | null>(null)

interface SkillRow {
  source?: Record<string, unknown>
  name: string
  bonus: string
  description: string
}
const skills = ref<SkillRow[]>([])

interface DamageRow {
  source?: Record<string, unknown>
  initial?: { count: string; sides: string; mod: string; type: string }
  count: string
  sides: string
  mod: string
  type: string
}
interface ActionRow {
  source?: Record<string, unknown>
  name: string
  toHit: string
  description: string
  damages: DamageRow[]
}
const actions = ref<ActionRow[]>([])

interface TraitRow {
  source?: Record<string, unknown>
  name: string
  description: string
}
const traits = ref<TraitRow[]>([])

const spellRows = ref<string[]>([])

// --- Load: canonical block -> rows --------------------------------------------

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function damageRows(parts: unknown): DamageRow[] {
  if (!Array.isArray(parts) || parts.length === 0) return []
  return parts
    .filter((part): part is Record<string, unknown> => typeof part === 'object' && part !== null)
    .map((part) => {
      const dice = typeof part['dice'] === 'string' ? part['dice'] : ''
      const match = /^(\d+)d(\d+)([+-]\d+)?$/.exec(dice.replace(/\s+/g, ''))
      // The canonical part carries the modifier as a SEPARATE `bonus`
      // field (dice is the bare "5d6"); older/odd rows may bake it into
      // the dice string instead — show whichever exists, prefer the dice
      // suffix, never invent a 0 that hides a real bonus.
      const fromDice = match?.[3]?.replace('+', '') ?? ''
      const bonus =
        fromDice !== ''
          ? fromDice
          : typeof part['bonus'] === 'number' && part['bonus'] !== 0
          ? String(part['bonus'])
          : ''
      const fields = {
        count: match?.[1] ?? (num(part['count']) !== null ? String(part['count']) : ''),
        sides: match?.[2] ?? (num(part['sides']) !== null ? String(part['sides']) : ''),
        mod: bonus,
        type: typeof part['type'] === 'string' ? part['type'] : '',
      }
      return { source: part, initial: { ...fields }, ...fields }
    })
}

/** Recover structured damage when a generated stat block only included the
 * published damage expression in its action description. This keeps the
 * editor useful for model output such as `Hit: 10 (1d10 + 5) force damage`
 * while leaving wrapper actions like Multiattack without a false damage row. */
function damageRowsFromDescription(description: string): DamageRow[] {
  const rows: DamageRow[] = []
  const pattern = /\((\d+)d(\d+)(?:\s*([+-])\s*(\d+))?\)\s+([A-Za-z]+)\s+damage/gi
  for (const match of description.matchAll(pattern)) {
    const modifier = match[4] ? `${match[3] === '-' ? '-' : ''}${match[4]}` : ''
    rows.push({
      count: match[1],
      sides: match[2],
      mod: modifier,
      type: match[5].toLowerCase(),
    })
  }
  return rows
}

function load(block: Record<string, unknown> | null) {
  const identity = (block?.['identity'] ?? {}) as Record<string, unknown>
  identityRole.value = typeof identity['role'] === 'string' ? identity['role'] : ''
  identityRace.value = typeof identity['race'] === 'string' ? identity['race'] : ''
  identityClass.value = typeof identity['class'] === 'string' ? identity['class'] : ''
  identityAlignment.value =
    typeof identity['alignment'] === 'string' ? identity['alignment'] : ''
  if (identity['cr'] !== undefined && identity['cr'] !== null) {
    powerSlot.value = 'cr'
    crValue.value = String(identity['cr'])
    levelValue.value = null
  } else {
    powerSlot.value = 'level'
    levelValue.value = num(identity['level'])
    crValue.value = ''
  }

  const attrs = (block?.['attributes'] ?? {}) as Record<string, unknown>
  for (const attr of ATTRIBUTES) attributes.value[attr] = num(attrs[attr])

  const combat = (block?.['combat'] ?? {}) as Record<string, unknown>
  ac.value = num(combat['ac'])
  hp.value = num(combat['hp'])
  hitDice.value = typeof combat['hit_dice'] === 'string' ? combat['hit_dice'] : ''

  powerStamp.value =
    block && typeof block['power'] === 'object' && block['power'] !== null
      ? (block['power'] as { dpr?: number; band?: number[]; verdict?: string })
      : null

  const rawSkills = block?.['skills']
  skills.value = Array.isArray(rawSkills)
    ? rawSkills.map((entry) => {
        const skill = (entry ?? {}) as Record<string, unknown>
        return {
          source: skill,
          name: typeof skill['name'] === 'string' ? skill['name'] : '',
          bonus: skill['bonus'] !== undefined ? String(skill['bonus']) : '',
          description:
            typeof skill['description'] === 'string' ? skill['description'] : '',
        }
      })
    : []

  const rawActions = block?.['actions']
  actions.value = Array.isArray(rawActions)
    ? rawActions.map((entry) => {
        const action = (entry ?? {}) as Record<string, unknown>
        const actionDescription =
          typeof action['description'] === 'string' ? action['description'] : ''
        const structuredDamage = damageRows(action['damage'])
        return {
          source: action,
          name: typeof action['name'] === 'string' ? action['name'] : '',
          toHit: action['to_hit'] !== undefined ? String(action['to_hit']) : '',
          description: actionDescription,
          damages:
            structuredDamage.length > 0
              ? structuredDamage
              : damageRowsFromDescription(actionDescription),
        }
      })
    : []

  const rawTraits = block?.['traits']
  traits.value = Array.isArray(rawTraits)
    ? rawTraits.map((entry) => {
        const trait = (entry ?? {}) as Record<string, unknown>
        return {
          source: trait,
          name: typeof trait['name'] === 'string' ? trait['name'] : '',
          description:
            typeof trait['description'] === 'string' ? trait['description'] : '',
        }
      })
    : []

  const rawSpells = block?.['spells']
  spellRows.value = Array.isArray(rawSpells)
    ? rawSpells.filter((spell): spell is string => typeof spell === 'string')
    : []
}

/** The JSON of the last value WE emitted. The parent writes it back
 * through v-model; reloading local state from our own echo would rebuild
 * the rows mid-edit and DROP any incomplete damage row (emitBlock skips
 * rows without both dice numbers) — the reported bug: type the dice,
 * click the sides input, the row vanishes. External changes (edit-open
 * seed, an external refetch) still reload. */
let lastEmitted: string | null = null

watch(
  () => props.modelValue,
  (block) => {
    if (JSON.stringify(block ?? {}) === lastEmitted) return
    load(block)
  },
  { immediate: true, deep: false },
)

// --- Emit: rows -> canonical block (blanks dropped) -----------------------------

function addSkill() {
  skills.value.push({ name: '', bonus: '', description: '' })
}
function addAction() {
  actions.value.push({ name: '', toHit: '', description: '', damages: [] })
}
function addDamage(action: ActionRow) {
  action.damages.push({ count: '', sides: '', mod: '', type: '' })
}
function addTrait() {
  traits.value.push({ name: '', description: '' })
}

const stampLine = computed(() => {
  const stamp = powerStamp.value
  if (!stamp || stamp.dpr === undefined) return null
  const band = Array.isArray(stamp.band) ? `${stamp.band[0]}-${stamp.band[1]}` : '—'
  return `${stamp.verdict ?? 'unknown'} — DPR ${stamp.dpr} vs band ${band}`
})

/** Copy fields the form does not own; clearing an owned field still removes it. */
function unedited(value: unknown, keys: readonly string[]): Record<string, unknown> {
  const result =
    typeof value === 'object' && value !== null && !Array.isArray(value)
      ? { ...(value as Record<string, unknown>) }
      : {}
  for (const key of keys) delete result[key]
  return result
}

function emitBlock(): Record<string, unknown> | null {
  const original = props.modelValue
  const block = unedited(original, [
    'identity', 'attributes', 'combat', 'skills', 'actions', 'traits', 'spells',
  ])

  const identity = unedited(
    original?.identity,
    props.hideIdentity ? [] : ['role', 'race', 'level', 'cr', 'class', 'alignment'],
  )
  if (!props.hideIdentity) {
    if (identityRole.value.trim()) identity.role = identityRole.value.trim()
    if (identityRace.value.trim()) identity.race = identityRace.value.trim()
    if (powerSlot.value === 'level' && levelValue.value !== null) identity.level = levelValue.value
    if (powerSlot.value === 'cr' && crValue.value.trim()) identity.cr = crValue.value.trim()
    if (identityClass.value.trim()) identity.class = identityClass.value.trim()
    if (identityAlignment.value.trim()) identity.alignment = identityAlignment.value.trim()
  }
  if (Object.keys(identity).length > 0) block.identity = identity

  const attrs = unedited(original?.attributes, ATTRIBUTES)
  for (const attr of ATTRIBUTES) {
    const value = attributes.value[attr]
    if (value !== null) attrs[attr] = value
  }
  if (Object.keys(attrs).length > 0) block.attributes = attrs

  const combat = unedited(original?.combat, ['ac', 'hp', 'hit_dice'])
  if (ac.value !== null) combat.ac = ac.value
  if (hp.value !== null) combat.hp = hp.value
  if (hitDice.value.trim()) combat.hit_dice = hitDice.value.trim()
  if (Object.keys(combat).length > 0) block.combat = combat

  const skillList = skills.value
    .filter((s) => s.name.trim())
    .map((s) => {
      const skill: Record<string, unknown> = {
        ...unedited(s.source, ['name', 'bonus', 'description']),
        name: s.name.trim(),
      }
      const bonus = Number.parseInt(s.bonus, 10)
      if (s.bonus.trim() && Number.isFinite(bonus)) skill.bonus = bonus
      if (s.description.trim()) skill.description = s.description.trim()
      return skill
    })
  if (skillList.length > 0) block.skills = skillList

  const actionList = actions.value
    .filter((a) => a.name.trim() || a.description.trim())
    .map((a) => {
      const action = unedited(a.source, ['name', 'to_hit', 'description', 'damage'])
      if (a.name.trim()) action.name = a.name.trim()
      const toHit = Number.parseInt(a.toHit, 10)
      if (a.toHit.trim() && Number.isFinite(toHit)) action.to_hit = toHit
      if (a.description.trim()) action.description = a.description.trim()
      const parts: Record<string, unknown>[] = []
      for (const row of a.damages) {
        // Unedited parts round-trip exactly, including fractional averages,
        // legacy dice suffixes and omitted canonical fields.
        if (
          row.source && row.initial &&
          row.count === row.initial.count && row.sides === row.initial.sides &&
          row.mod === row.initial.mod && row.type === row.initial.type
        ) {
          parts.push({ ...row.source })
          continue
        }
        const count = Number.parseInt(row.count, 10)
        const sides = Number.parseInt(row.sides, 10)
        if (!Number.isFinite(count) || !Number.isFinite(sides)) continue
        const mod = row.mod.trim()
        const bonus = mod === '' || !Number.isFinite(Number.parseInt(mod, 10)) ? 0 : Number.parseInt(mod, 10)
        // Canonical part: dice is the BARE formula ("5d6") and the modifier
        // rides the separate `bonus` field — baking "+7" into dice AND
        // setting bonus made the export render "5d6+7+7" and the auditor
        // read the bonus twice.
        const dice = `${count}d${sides}`
        const average = Math.round(((count * (sides + 1)) / 2 + bonus) * 100) / 100
        parts.push({
          ...unedited(row.source, ['dice', 'count', 'sides', 'bonus', 'average', 'type']),
          dice,
          count,
          sides,
          bonus,
          average,
          type: row.type.trim() || 'damage',
        })
      }
      if (parts.length > 0) action.damage = parts
      return action
    })
  if (actionList.length > 0) block.actions = actionList

  const traitList = traits.value
    .filter((t) => t.name.trim() || t.description.trim())
    .map((t) => ({
      ...unedited(t.source, ['name', 'description']),
      name: t.name.trim(),
      description: t.description.trim(),
    }))
  if (traitList.length > 0) block.traits = traitList

  const spells = spellRows.value.map((spell: string) => spell.trim()).filter(Boolean)
  if (spells.length > 0) block.spells = spells

  return Object.keys(block).length > 0 ? block : null
}

function emitUpdate() {
  const value = emitBlock()
  lastEmitted = JSON.stringify(value ?? {})
  emit('update:modelValue', value)
}
</script>

<template>
  <div class="sbe" @change="emitUpdate" @blur="emitUpdate">
    <p v-if="stampLine" class="muted small mono">{{ stampLine }}</p>

    <div v-if="!props.hideIdentity" class="grid">
      <label>
        Role
        <select v-model="identityRole">
          <option value="">—</option>
          <option v-for="role in ROLES" :key="role" :value="role">{{ role }}</option>
        </select>
      </label>
      <label>
        Race / type
        <input v-model="identityRace" type="text" />
      </label>
      <label>
        Power slot
        <select v-model="powerSlot">
          <option value="level">Level</option>
          <option value="cr">CR</option>
        </select>
      </label>
      <label v-if="powerSlot === 'level'">
        Level (1–20)
        <input v-model.number="levelValue" type="number" min="1" max="20" />
      </label>
      <label v-else>
        CR (integer or '1/2')
        <input v-model="crValue" type="text" />
      </label>
      <label>
        Class
        <input v-model="identityClass" type="text" />
      </label>
      <label>
        Alignment
        <input v-model="identityAlignment" type="text" />
      </label>
    </div>

    <h4>Attributes</h4>
    <div class="grid">
      <label v-for="attr in ATTRIBUTES" :key="attr">
        {{ attr.toUpperCase() }}
        <input v-model.number="attributes[attr]" type="number" min="1" max="30" :aria-label="`attributes.${attr}`" />
      </label>
    </div>

    <h4>Combat</h4>
    <div class="grid">
      <label>AC <input v-model.number="ac" type="number" min="0" aria-label="combat.ac" /></label>
      <label>HP <input v-model.number="hp" type="number" min="1" aria-label="combat.hp" /></label>
      <label>Hit dice <input v-model="hitDice" type="text" aria-label="combat.hit_dice" /></label>
    </div>

    <h4>Skills <button type="button" class="link" @click="addSkill">+ add</button></h4>
    <div v-for="(skill, i) in skills" :key="`sk${i}`" class="row four">
      <input v-model="skill.name" type="text" placeholder="Arcana" />
      <input v-model="skill.bonus" type="text" placeholder="bonus" />
      <input v-model="skill.description" type="text" placeholder="notes" />
      <button type="button" class="link" @click="skills.splice(i, 1); emitUpdate()">✕</button>
    </div>

    <h4>Actions <button type="button" class="link" @click="addAction">+ add</button></h4>
    <div v-for="(action, i) in actions" :key="`ac${i}`" class="action-block">
      <div class="row four">
        <input v-model="action.name" type="text" placeholder="Longsword" />
        <input v-model="action.toHit" type="text" placeholder="to hit (+14)" />
        <span></span>
        <button type="button" class="link" @click="actions.splice(i, 1); emitUpdate()">✕</button>
      </div>
      <textarea
        v-model="action.description"
        rows="2"
        placeholder="Melee Weapon Attack: +14 to hit. Hit: 11.5 (1d6+8) force damage."
      ></textarea>
      <div v-for="(damage, di) in action.damages" :key="`ac${i}d${di}`" class="dice-row">
        <input v-model="damage.count" type="text" placeholder="dice" />
        <span>d</span>
        <input v-model="damage.sides" type="text" placeholder="sides" />
        <span>+</span>
        <input v-model="damage.mod" type="text" placeholder="mod" />
        <input v-model="damage.type" type="text" placeholder="type" />
        <button
          type="button"
          class="link"
          @click="action.damages.splice(di, 1); emitUpdate()"
        >
          ✕
        </button>
      </div>
      <button type="button" class="link" @click="addDamage(action)">+ damage row</button>
    </div>

    <h4>Traits <button type="button" class="link" @click="addTrait">+ add</button></h4>
    <div v-for="(trait, i) in traits" :key="`tr${i}`" class="trait-block">
      <input v-model="trait.name" type="text" placeholder="Unsettling Presence" />
      <textarea
        v-model="trait.description"
        rows="2"
        placeholder="Creatures starting their turn within 30 feet…"
      ></textarea>
      <button type="button" class="link" @click="traits.splice(i, 1); emitUpdate()">✕</button>
    </div>

    <h4>Spells</h4>
    <SpellCards v-model="spellRows" />
  </div>
</template>

<style scoped>
.sbe {
  display: grid;
  gap: 0.5rem;
  min-width: 0;
  max-width: 100%;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(130px, 100%), 1fr));
  gap: 0.5rem;
}
label {
  display: grid;
  gap: 0.2rem;
  min-width: 0;
}
input,
select,
textarea {
  box-sizing: border-box;
  min-width: 0;
  max-width: 100%;
  padding: 0.3rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
}
.row.four {
  display: grid;
  grid-template-columns: 1.5fr 0.7fr 1.5fr auto;
  gap: 0.4rem;
  align-items: center;
  min-width: 0;
}
.dice-row {
  display: grid;
  grid-template-columns: 3.5rem auto 3.5rem auto 3.5rem 1fr auto;
  gap: 0.3rem;
  align-items: center;
  min-width: 0;
}
.row.four > *,
.dice-row > * {
  min-width: 0;
}
.action-block,
.trait-block {
  display: grid;
  gap: 0.3rem;
  border: 1px dashed #2c3038;
  border-radius: 6px;
  padding: 0.4rem;
  margin-bottom: 0.4rem;
}
.mono {
  font-family: ui-monospace, monospace;
}
.muted.small {
  color: #9aa0a6;
  font-size: 0.8rem;
}
.link {
  background: none;
  border: none;
  color: #8ab4ff;
  cursor: pointer;
  padding: 0;
  font: inherit;
}
h4 {
  margin: 0.4rem 0 0;
  display: flex;
  gap: 0.75rem;
  align-items: center;
}

@media (max-width: 760px) {
  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .row.four {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .row.four .link {
    justify-self: start;
  }
}

@media (max-width: 480px) {
  .grid,
  .row.four {
    grid-template-columns: minmax(0, 1fr);
  }
  .dice-row {
    display: flex;
    flex-wrap: wrap;
  }
  .dice-row input {
    flex: 1 1 3rem;
  }
  .dice-row input:last-of-type {
    flex-basis: 7rem;
  }
}
</style>
