<script setup lang="ts">
import { computed } from 'vue'

/**
 * Minimal 5e stat-block renderer (spec-2-7) for `data['stat_block']`.
 *
 * The pipeline (2.4) guarantees the committed shape — identity, six
 * ability scores, AC/HP, optional skills/actions/traits/spells — but the
 * view tolerates absence: anything that is not an object renders nothing,
 * and missing sections/fields render no line rather than an error.
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
const initiative = computed(() => {
  const dex = attributes.value?.dex
  if (!isFiniteNumber(dex)) return null
  const modifier = Math.floor((dex - 10) / 2)
  return modifier >= 0 ? `+${modifier}` : String(modifier)
})

const combatLine = computed(() => {
  const combatValue = combat.value
  if (!combatValue) return null
  const parts = [
    isFiniteNumber(combatValue['ac']) ? `AC ${combatValue['ac']}` : null,
    isFiniteNumber(combatValue['hp']) ? `HP ${combatValue['hp']}` : null,
    initiative.value !== null ? `Initiative ${initiative.value}` : null,
  ].filter((part): part is string => part !== null)
  return parts.length > 0 ? parts.join(' · ') : null
})

const skillLine = (skill: NamedEntry) =>
  `${typeof skill.name === 'string' && skill.name.trim() ? skill.name : '?'}${typeof skill.bonus === 'number' ? (skill.bonus >= 0 ? ' +' : ' ') + String(skill.bonus) : ''}`
</script>

<template>
  <section
    v-if="
      identity ||
      attributes ||
      combatLine ||
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
    <p v-if="skills.length > 0" class="mono">{{ skills.map(skillLine).join(', ') }}</p>
    <dl v-if="actions.length > 0">
      <dt>Actions</dt>
      <dd v-for="(action, index) in actions" :key="`${index}-${String(action.name)}`">
        <strong>{{ action.name ?? '?' }}</strong
        ><template v-if="action.description"> — {{ action.description }}</template>
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
