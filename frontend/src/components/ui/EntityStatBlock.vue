<script setup lang="ts">
/**
 * Entity stat block (rebuild §15) — the same tolerant `block` parsing as
 * StatBlock.vue (absent sections render nothing) in the new visual
 * language: combat strip, ability grid, collapsible maneuver lists.
 *
 * Deliberately a separate component: the old StatBlock.vue stays byte-
 * identical for the existing world/accept surfaces and their tests.
 */
import { computed } from 'vue'

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
    const modifierText = modifier === null ? '' : `(${modifier >= 0 ? '+' : ''}${modifier})`
    return {
      key,
      label: ABILITY_LABELS[key],
      score:
        isFiniteNumber(value) || (typeof value === 'string' && value.trim()) ? String(value) : '—',
      modifierText,
    }
  })
})

const withSign = (value: number): string => (value >= 0 ? `+${value}` : String(value))

const initiative = computed<number | null>(() => {
  const raw = isObject(props.block) ? props.block['initiative'] : null
  return isFiniteNumber(raw) ? raw : null
})

const derivedInitiative = computed(() => {
  const dex = attributes.value?.dex
  if (!isFiniteNumber(dex)) return null
  const modifier = Math.floor((dex - 10) / 2)
  return withSign(modifier)
})

const initiativeText = computed(() => {
  if (initiative.value !== null) return withSign(initiative.value)
  return derivedInitiative.value
})

const acText = computed(() => {
  const ac = combat.value?.['ac']
  return isFiniteNumber(ac) ? String(ac) : null
})

const hpText = computed(() => {
  const hp = combat.value?.['hp']
  return isFiniteNumber(hp) ? String(hp) : null
})

const hitDice = computed(() => {
  const raw = combat.value?.['hit_dice']
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null
})

const powerFlag = computed<string | null>(() => {
  const raw = isObject(props.block) ? props.block['power'] : null
  if (!isObject(raw)) return null
  const verdict = raw['verdict']
  if (verdict !== 'over-powered' && verdict !== 'under-powered') return null
  const dpr = isFiniteNumber(raw['dpr']) ? String(raw['dpr']) : '?'
  const band = Array.isArray(raw['band']) ? raw['band'].filter(isFiniteNumber) : []
  const bandText = band.length === 2 ? ` vs band ${band[0]}–${band[1]}` : ''
  const label = verdict === 'over-powered' ? 'Over-powered' : 'Under-powered'
  return `${label} — DPR ${dpr}${bandText}`
})

const humanize = (key: string): string => {
  const spaced = key.replace(/_/g, ' ').trim()
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : key
}

function savesText(raw: unknown): string | null {
  if (!isObject(raw)) return null
  const rows: string[] = []
  for (const key of ABILITY_KEYS) {
    const value = raw[key]
    if (isFiniteNumber(value)) rows.push(`${ABILITY_LABELS[key]} ${withSign(value)}`)
  }
  return rows.length > 0 ? `Saves ${rows.join(', ')}` : null
}

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

function featuresText(raw: unknown): string | null {
  if (!Array.isArray(raw)) return null
  const names = raw
    .filter((name): name is string => typeof name === 'string' && name.trim() !== '')
    .map((name) => name.trim())
  return names.length > 0 ? `Features ${names.join(', ')}` : null
}

function resourcesText(raw: unknown): string | null {
  if (!isObject(raw)) return null
  const rows: string[] = []
  for (const [name, value] of Object.entries(raw)) {
    if (isFiniteNumber(value)) rows.push(`${humanize(name)} ${value}`)
  }
  return rows.length > 0 ? `Resources ${rows.join(', ')}` : null
}

const aspectLines = computed<string[]>(() => {
  const block = isObject(props.block) ? props.block : null
  if (!block) return []
  const lines: string[] = []
  const add = (line: string | null) => {
    if (line !== null) lines.push(line)
  }
  add(savesText(block['saves']))
  const passive = block['passive_perception']
  if (isFiniteNumber(passive)) add(`Passive perception ${passive}`)
  const proficiency = block['proficiency_bonus']
  if (isFiniteNumber(proficiency)) add(`Proficiency bonus ${withSign(proficiency)}`)
  add(spellcastingText(block['spellcasting']))
  add(featuresText(block['features']))
  add(resourcesText(block['resources']))
  return lines
})

function pairDamage(part: Record<string, unknown>): { dice: string; average: number } | null {
  const count = part['count']
  const sides = part['sides']
  if (!isFiniteNumber(count) || !isFiniteNumber(sides) || count < 1 || sides < 2) return null
  return { dice: `${count}d${sides}`, average: (count * (sides + 1)) / 2 }
}

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

function damageText(action: NamedEntry): string | null {
  const raw = action.damage
  if (!Array.isArray(raw)) return null
  const parts = raw.map(damagePartText).filter((part): part is string => part !== null)
  return parts.length > 0 ? parts.join(' + ') : null
}

function actionNumbers(action: NamedEntry): string {
  const damage = damageText(action)
  if (damage === null && !isFiniteNumber(action.to_hit)) return ''
  const toHit = isFiniteNumber(action.to_hit) ? `to-hit ${withSign(action.to_hit)}` : ''
  return [toHit, damage].filter(Boolean).join(' · ')
}

const skillLine = (skill: NamedEntry) =>
  `${typeof skill.name === 'string' && skill.name.trim() ? skill.name : '?'}${typeof skill.bonus === 'number' ? (skill.bonus >= 0 ? ' +' : ' ') + String(skill.bonus) : ''}`

const hasAnything = computed(
  () =>
    identity.value !== null ||
    abilityScores.value.length > 0 ||
    acText.value !== null ||
    hpText.value !== null ||
    initiativeText.value !== null ||
    powerFlag.value !== null ||
    aspectLines.value.length > 0 ||
    skills.value.length > 0 ||
    actions.value.length > 0 ||
    traits.value.length > 0 ||
    spells.value.length > 0,
)
</script>

<template>
  <div v-if="hasAnything" class="mc-stat">
    <p v-if="identityLine" class="mc-stat-identity">{{ identityLine }}</p>

    <div v-if="acText || hpText || initiativeText" class="mc-stat-strip">
      <div v-if="acText" class="mc-stat-tile">
        <span class="mc-stat-tile-label">AC</span>
        <strong class="mc-stat-tile-value">{{ acText }}</strong>
      </div>
      <div v-if="hpText" class="mc-stat-tile">
        <span class="mc-stat-tile-label">HP</span>
        <strong class="mc-stat-tile-value">{{ hpText }}</strong>
        <span v-if="hitDice" class="mc-stat-tile-sub">{{ hitDice }}</span>
      </div>
      <div v-if="initiativeText" class="mc-stat-tile">
        <span class="mc-stat-tile-label">Initiative</span>
        <strong class="mc-stat-tile-value">{{ initiativeText }}</strong>
      </div>
    </div>

    <div v-if="abilityScores.length > 0" class="mc-stat-abilities">
      <div v-for="ability in abilityScores" :key="ability.key" class="mc-stat-ability">
        <span class="mc-stat-ability-label">{{ ability.label }}</span>
        <strong class="mc-stat-ability-score">{{ ability.score }}</strong>
        <span v-if="ability.modifierText" class="mc-stat-ability-mod">{{
          ability.modifierText
        }}</span>
      </div>
    </div>

    <p v-if="powerFlag" class="mc-stat-power">{{ powerFlag }}</p>

    <details v-if="actions.length > 0" open class="mc-stat-details">
      <summary>Actions ({{ actions.length }})</summary>
      <article v-for="(action, index) in actions" :key="`${index}-${String(action.name)}`">
        <h4>{{ action.name ?? '?' }}</h4>
        <p v-if="action.description">{{ action.description }}</p>
        <p v-if="actionNumbers(action)" class="mc-stat-numbers">{{ actionNumbers(action) }}</p>
      </article>
    </details>

    <details v-if="traits.length > 0" class="mc-stat-details">
      <summary>Traits ({{ traits.length }})</summary>
      <article v-for="(trait, index) in traits" :key="`${index}-${String(trait.name)}`">
        <h4>{{ trait.name ?? '?' }}</h4>
        <p v-if="trait.description">{{ trait.description }}</p>
      </article>
    </details>

    <details v-if="spells.length > 0" class="mc-stat-details">
      <summary>Spells ({{ spells.length }})</summary>
      <p>{{ spells.join(', ') }}</p>
    </details>

    <details v-if="skills.length > 0" class="mc-stat-details">
      <summary>Skills</summary>
      <p>{{ skills.map(skillLine).join(', ') }}</p>
    </details>

    <details v-if="aspectLines.length > 0" class="mc-stat-details">
      <summary>Details</summary>
      <p v-for="line in aspectLines" :key="line">{{ line }}</p>
    </details>
  </div>
</template>

<style scoped>
.mc-stat {
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  padding: 1.25rem 1.4rem;
}
.mc-stat-identity {
  margin: 0 0 1rem;
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-stat-strip {
  display: flex;
  gap: var(--mc-gap-sm);
  margin-bottom: 1rem;
}
.mc-stat-tile {
  flex: 1;
  background: var(--mc-surface-raised);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  padding: 0.6rem 0.8rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.15rem;
}
.mc-stat-tile-label {
  font-size: var(--mc-meta-size);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-stat-tile-value {
  font-size: 1.5rem;
}
.mc-stat-tile-sub {
  font-size: 0.8rem;
  color: var(--mc-text-muted);
  font-family: ui-monospace, monospace;
}
.mc-stat-abilities {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: var(--mc-gap-sm);
  margin-bottom: 0.5rem;
}
.mc-stat-ability {
  background: var(--mc-surface-raised);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  padding: 0.5rem 0.25rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.1rem;
}
.mc-stat-ability-label {
  font-size: var(--mc-meta-size);
  color: var(--mc-text-muted);
}
.mc-stat-ability-score {
  font-size: 1.15rem;
}
.mc-stat-ability-mod {
  font-size: 0.8rem;
  color: var(--mc-text-secondary);
}
.mc-stat-power {
  color: var(--mc-warning);
  font-size: 0.9rem;
}
.mc-stat-details {
  border-top: 1px solid var(--mc-border);
  padding: 0.6rem 0;
}
.mc-stat-details summary {
  cursor: pointer;
  font-weight: 600;
  font-size: var(--mc-section-title-size);
}
.mc-stat-details article {
  margin: 0.6rem 0;
}
.mc-stat-details h4 {
  margin: 0 0 0.25rem;
  font-size: var(--mc-body-size);
}
.mc-stat-details p {
  margin: 0.25rem 0 0;
  line-height: 1.55;
  color: var(--mc-text-secondary);
}
.mc-stat-numbers {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
  color: var(--mc-text-muted);
}
@media (max-width: 640px) {
  .mc-stat-abilities {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
