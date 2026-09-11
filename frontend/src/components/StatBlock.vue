<script setup lang="ts">
import { computed } from 'vue'

/**
 * Minimal 5e stat-block renderer (spec-2-7) for `data['stat_block']`, plus
 * the optional mechanics aspects the pipeline learned to store and the
 * structured attack damage (spec: structured attack damage and the missing
 * stat aspects, 2026-09-11).
 *
 * The pipeline (2.4) guarantees the committed shape — identity, six
 * ability scores, AC/HP, optional skills/actions/traits/spells — but the
 * view tolerates absence: anything that is not an object renders nothing,
 * and missing sections/fields render no line rather than an error.
 *
 * Every aspect added on 2026-09-11 is OPTIONAL and ADDITIVE: a block
 * written before them renders exactly as it did (no empty rows, no
 * placeholders), and a present one is preferred over the derived/prose
 * value it supersedes: an explicit `initiative` replaces the DEX-derived
 * one, and `actions[].damage[]` replaces the action's prose description.
 */

interface StatIdentity {
  role?: unknown
  race?: unknown
  level?: unknown
  cr?: unknown
  class?: unknown
  alignment?: unknown
}

interface StatAttributes {
  str?: unknown
  dex?: unknown
  con?: unknown
  int?: unknown
  wis?: unknown
  cha?: unknown
}

interface NamedEntry {
  name?: unknown
  bonus?: unknown
  description?: unknown
  /** Optional structured attack data (spec 2026-09-11): the to-hit bonus
   * and the damage parts the auditor and the export read. */
  to_hit?: unknown
  damage?: unknown
}

const props = defineProps<{ block: unknown }>()

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)

const identity = computed<StatIdentity | null>(() => {
  const raw = isObject(props.block) ? props.block['identity'] : null
  return isObject(raw) ? (raw as StatIdentity) : null
})

const attributes = computed<StatAttributes | null>(() => {
  const raw = isObject(props.block) ? props.block['attributes'] : null
  return isObject(raw) ? (raw as StatAttributes) : null
})

const combat = computed<Record<string, unknown> | null>(() => {
  const raw = isObject(props.block) ? props.block['combat'] : null
  return isObject(raw) ? raw : null
})

const entries = (section: string): NamedEntry[] => {
  const raw = isObject(props.block) ? props.block[section] : null
  return Array.isArray(raw) ? raw.filter(isObject) : []
}

const skills = computed(() => entries('skills'))
const actions = computed(() => entries('actions'))
const traits = computed(() => entries('traits'))

const spells = computed<string[]>(() => {
  const raw = isObject(props.block) ? props.block['spells'] : null
  return Array.isArray(raw) ? raw.filter((spell): spell is string => typeof spell === 'string') : []
})

/** "Monster" blocks carry cr instead of level. */
const powerLabel = computed(() => {
  if (!identity.value) return null
  const { cr, level } = identity.value
  if (typeof cr === 'number' || typeof cr === 'string') return `CR ${String(cr)}`
  if (typeof level === 'number' || typeof level === 'string') return `Level ${String(level)}`
  return null
})

const identityLine = computed(() => {
  const id = identity.value
  if (!id) return null
  const parts = [id.role, id.race, id.class, powerLabel.value, id.alignment]
    .filter((part) => typeof part === 'string' && part.trim())
    .map((part) => String(part))
  return parts.length > 0 ? parts.join(' · ') : null
})

const ABILITY_KEYS = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const
const ABILITY_LABELS: Record<(typeof ABILITY_KEYS)[number], string> = {
  str: 'STR',
  dex: 'DEX',
  con: 'CON',
  int: 'INT',
  wis: 'WIS',
  cha: 'CHA',
}

const abilityScores = computed(() => {
  const attrs = attributes.value
  if (!attrs) return []
  return ABILITY_KEYS.map((key) => {
    const value = attrs[key]
    const modifier = isFiniteNumber(value) ? Math.floor((value - 10) / 2) : null
    const modifierText = modifier === null ? '' : ` (${modifier >= 0 ? '+' : ''}${modifier})`
    return {
      key,
      label: ABILITY_LABELS[key],
      score:
        isFiniteNumber(value) || (typeof value === 'string' && value.trim()) ? String(value) : '—',
      modifierText,
    }
  })
})

/** Initiative rides DEX — the block is "ready to roll initiative". */
const derivedInitiative = computed(() => {
  const dex = attributes.value?.dex
  if (!isFiniteNumber(dex)) return null
  const modifier = Math.floor((dex - 10) / 2)
  return modifier >= 0 ? `+${modifier}` : String(modifier)
})

/** An explicit `initiative` (optional, spec 2026-09-11) — the number the
 * model stated, which is what the DM rolls. */
const initiative = computed<number | null>(() => {
  const raw = isObject(props.block) ? props.block['initiative'] : null
  return isFiniteNumber(raw) ? raw : null
})

/** `combat.hit_dice` — a dice expression printed as written ("24d10 + 192"). */
const hitDice = computed<string | null>(() => {
  const raw = combat.value?.['hit_dice']
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null
})

const combatLine = computed(() => {
  const combatValue = combat.value
  if (!combatValue) return null
  const parts = [
    isFiniteNumber(combatValue['ac']) ? `AC ${combatValue['ac']}` : null,
    isFiniteNumber(combatValue['hp']) ? `HP ${combatValue['hp']}` : null,
    hitDice.value !== null ? `Hit dice ${hitDice.value}` : null,
    // A stated initiative wins over the DEX-derived one, which then drops
    // out of this line and renders as its own (below): one number, never
    // two, and a block with neither is byte-identical to the old panel.
    initiative.value === null && derivedInitiative.value !== null
      ? `Initiative ${derivedInitiative.value}`
      : null,
  ].filter((part): part is string => part !== null)
  return parts.length > 0 ? parts.join(' · ') : null
})

/** `power` -> "Over-powered — DPR 189 vs band 63–68", stamped
 * deterministically by the pipeline (owner verdict 2026-09-12: an
 * over-powered block commits declared). Only the over-powered verdict
 * renders — committed blocks are otherwise on-target, so a line for
 * those would be noise on every card. */
const powerFlag = computed<string | null>(() => {
  const raw = isObject(props.block) ? props.block['power'] : null
  if (!isObject(raw) || raw['verdict'] !== 'over-powered') return null
  const dpr = isFiniteNumber(raw['dpr']) ? String(raw['dpr']) : '?'
  const band = Array.isArray(raw['band'])
    ? raw['band'].filter(isFiniteNumber)
    : []
  const bandText = band.length === 2 ? ` vs band ${band[0]}–${band[1]}` : ''
  return `Over-powered — DPR ${dpr}${bandText}`
})

/** The panel's signed-number convention ("+5" / "-2", 5e notation). */
const withSign = (value: number): string => (value >= 0 ? `+${value}` : String(value))

/** `lay_on_hands` -> `Lay on hands` (the sheet's ``_label`` convention). */
const humanize = (key: string): string => {
  const spaced = key.replace(/_/g, ' ').trim()
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : key
}

/** `saves` -> "Saves STR +5, DEX +1, …" in the six abilities' canonical
 * order (unknown keys render nothing — the validator admits only those). */
function savesText(raw: unknown): string | null {
  if (!isObject(raw)) return null
  const rows: string[] = []
  for (const key of ABILITY_KEYS) {
    const value = raw[key]
    if (isFiniteNumber(value)) rows.push(`${ABILITY_LABELS[key]} ${withSign(value)}`)
  }
  return rows.length > 0 ? `Saves ${rows.join(', ')}` : null
}

/** `spellcasting` -> "Spellcasting DC 18 · Attack +10 · Slots 4/3/2"; each
 * part renders only when stored. */
function spellcastingText(raw: unknown): string | null {
  if (!isObject(raw)) return null
  const parts: string[] = []
  const dc = raw['dc']
  if (isFiniteNumber(dc)) parts.push(`DC ${dc}`)
  const attack = raw['attack_bonus']
  if (isFiniteNumber(attack)) parts.push(`Attack ${withSign(attack)}`)
  const slots = raw['slots']
  if (Array.isArray(slots)) {
    const values = slots.filter(isFiniteNumber)
    if (values.length > 0) parts.push(`Slots ${values.join('/')}`)
  }
  return parts.length > 0 ? `Spellcasting ${parts.join(' · ')}` : null
}

/** `features` -> "Features Divine Smite, Lay on Hands" (names only — the
 * block's own list, never folded into `traits`). */
function featuresText(raw: unknown): string | null {
  if (!Array.isArray(raw)) return null
  const names = raw
    .filter((name): name is string => typeof name === 'string' && name.trim() !== '')
    .map((name) => name.trim())
  return names.length > 0 ? `Features ${names.join(', ')}` : null
}

/** `resources` -> "Resources Lay on hands 85, Ki points 5" in committed
 * order. */
function resourcesText(raw: unknown): string | null {
  if (!isObject(raw)) return null
  const rows: string[] = []
  for (const [name, value] of Object.entries(raw)) {
    if (isFiniteNumber(value)) rows.push(`${humanize(name)} ${value}`)
  }
  return rows.length > 0 ? `Resources ${rows.join(', ')}` : null
}

/**
 * The optional aspects as their own labelled mono lines, in the contract's
 * order: saves, initiative, passive perception, proficiency bonus,
 * spellcasting, features, resources (hit dice rides the combat line). A
 * block carrying none of them yields no lines at all.
 */
const aspectLines = computed<string[]>(() => {
  const block = isObject(props.block) ? props.block : null
  if (!block) return []
  const lines: string[] = []
  const add = (line: string | null) => {
    if (line !== null) lines.push(line)
  }
  add(savesText(block['saves']))
  if (initiative.value !== null) add(`Initiative ${withSign(initiative.value)}`)
  const passive = block['passive_perception']
  if (isFiniteNumber(passive)) add(`Passive perception ${passive}`)
  const proficiency = block['proficiency_bonus']
  if (isFiniteNumber(proficiency)) add(`Proficiency bonus ${withSign(proficiency)}`)
  add(spellcastingText(block['spellcasting']))
  add(featuresText(block['features']))
  add(resourcesText(block['resources']))
  return lines
})

/** The dice expression and nominal average a damage part's count/sides
 * pair implies, or null when the pair is unusable. */
function pairDamage(part: Record<string, unknown>): { dice: string; average: number } | null {
  const count = part['count']
  const sides = part['sides']
  if (!isFiniteNumber(count) || !isFiniteNumber(sides) || count < 1 || sides < 2) return null
  return { dice: `${count}d${sides}`, average: (count * (sides + 1)) / 2 }
}

/** One damage part as the DM reads it — "19 (2d6+12) slashing" — or null
 * when it names no readable dice expression. */
function damagePartText(part: unknown): string | null {
  if (!isObject(part)) return null
  const pair = pairDamage(part)
  const stored = typeof part['dice'] === 'string' ? part['dice'].trim() : ''
  const dice = stored || pair?.dice || ''
  if (!dice) return null
  const bonus = isFiniteNumber(part['bonus']) ? part['bonus'] : 0
  const average = isFiniteNumber(part['average'])
    ? part['average']
    : pair
      ? pair.average + bonus
      : null
  const numbers = `${average === null ? '' : `${average} `}(${dice}${bonus === 0 ? '' : withSign(bonus)})`
  const kind =
    typeof part['type'] === 'string' && part['type'].trim() ? ` ${part['type'].trim()}` : ''
  return `${numbers}${kind}`
}

/** The action's parts as one clause ("19 (2d6+12) slashing + 16.5 (3d10)
 * radiant"), or null when it carries no readable part. */
function damageText(action: NamedEntry): string | null {
  const raw = action.damage
  if (!Array.isArray(raw)) return null
  const parts = raw.map(damagePartText).filter((part): part is string => part !== null)
  return parts.length > 0 ? parts.join(' + ') : null
}

/** Everything the action line adds after its name: the structured to-hit
 * and damage when the action carries parts, else today's description-only
 * line. Never both — the parts say what the prose said, and repeating them
 * would print the numbers twice. */
function actionSuffix(action: NamedEntry): string {
  const damage = damageText(action)
  if (damage !== null) {
    const toHit = isFiniteNumber(action.to_hit) ? ` ${withSign(action.to_hit)}` : ''
    return `${toHit} — ${damage}`
  }
  return action.description ? ` — ${String(action.description)}` : ''
}

const skillLine = (skill: NamedEntry) =>
  `${typeof skill.name === 'string' && skill.name.trim() ? skill.name : '?'}${typeof skill.bonus === 'number' ? (skill.bonus >= 0 ? ' +' : ' ') + String(skill.bonus) : ''}`
</script>

<template>
  <section
    v-if="
      identity ||
      attributes ||
      combatLine ||
      powerFlag ||
      aspectLines.length > 0 ||
      skills.length > 0 ||
      actions.length > 0 ||
      traits.length > 0 ||
      spells.length > 0
    "
    class="stat-block"
  >
    <h4>Stat block</h4>
    <p v-if="identityLine" class="identity">{{ identityLine }}</p>
    <div v-if="abilityScores.length > 0" class="abilities">
      <span v-for="ability in abilityScores" :key="ability.key" class="ability">
        <span class="muted">{{ ability.label }}</span>
        <strong>{{ ability.score }}</strong
        >{{ ability.modifierText }}
      </span>
    </div>
    <p v-if="combatLine" class="mono">{{ combatLine }}</p>
    <p v-if="powerFlag" class="mono">{{ powerFlag }}</p>
    <!-- The optional aspects (spec 2026-09-11), one labelled line each.
         Nothing renders for a block that carries none of them. -->
    <p v-for="line in aspectLines" :key="line" class="mono">{{ line }}</p>
    <p v-if="skills.length > 0" class="mono">{{ skills.map(skillLine).join(', ') }}</p>
    <dl v-if="actions.length > 0">
      <dt>Actions</dt>
      <dd v-for="(action, index) in actions" :key="`${index}-${String(action.name)}`">
        <strong>{{ action.name ?? '?' }}</strong
        ><template v-if="actionSuffix(action)">{{ actionSuffix(action) }}</template>
      </dd>
    </dl>
    <dl v-if="traits.length > 0">
      <dt>Traits</dt>
      <dd v-for="(trait, index) in traits" :key="`${index}-${String(trait.name)}`">
        <strong>{{ trait.name ?? '?' }}</strong
        ><template v-if="trait.description"> — {{ trait.description }}</template>
      </dd>
    </dl>
    <dl v-if="spells.length > 0">
      <dt>Spells</dt>
      <dd class="mono">{{ spells.join(', ') }}</dd>
    </dl>
  </section>
</template>

<style scoped>
.stat-block {
  border-left: 3px solid #2c3038;
  padding-left: 0.75rem;
  margin-top: 0.5rem;
}
.stat-block h4 {
  margin: 0.25rem 0;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #9aa0a6;
}
.identity {
  margin: 0.25rem 0;
}
.ability {
  display: inline-block;
  margin-right: 1rem;
}
.mono,
.ability strong {
  font-family: ui-monospace, monospace;
}
dl {
  margin: 0.25rem 0;
}
dt {
  color: #9aa0a6;
  font-size: 0.85rem;
}
dd {
  margin: 0.1rem 0 0.1rem 0;
}
</style>
