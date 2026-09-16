<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { apiFetch, ApiError } from '../api/client'
import type { components } from '../api/schema'
import {
  DICE_PATTERN,
  type ActionEntry,
  type CharacterRecord,
  type CharacterSheet,
  type DeclaredRelation,
  type SkillEntry,
  type StatBlock,
} from '../api/characterSchema'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useJobsStore } from '../stores/jobs'
import { connectJobSocket } from '../ws'
import type { WsMessage } from '../ws'

type Job = components['schemas']['JobResponse']

const route = useRoute()
const router = useRouter()

const campaigns = useCampaignsStore()
const jobs = useJobsStore()
const loadError = ref<string | null>(null)

/** The forge works without opening a world first: entering via /forge
 * shows the world picker; a sheet is authored freely and only bound to a
 * world at submission (the gate needs a campaign_id). A per-campaign
 * route (/campaigns/:id/forge) pins the world immediately. */
const pickedCampaignId = ref<string | null>((route.params.id as string) || null)
const campaignId = computed(() => pickedCampaignId.value ?? '')
const picking = ref(false)

const error = ref<string | null>(null)
const violations = ref<string[]>([])
const submitting = ref(false)

let disconnectSocket: (() => void) | null = null

/** The post-pick load: world meta, this world's job list + live frames,
 * and the relation picker's entity list. */
async function loadCampaign(id: string) {
  try {
    await Promise.all([campaigns.fetchOne(id), jobs.syncList(id)])
    if (campaigns.error) {
      loadError.value = campaigns.error
      return
    }
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load the world.'
    return
  }
  disconnectSocket?.()
  disconnectSocket = connectJobSocket(
    id,
    (message: WsMessage) => {
      void jobs.handleWsMessage(id, message)
    },
    {
      onReconnect: () => {
        void jobs.syncList(id)
      },
      onAuthFailure: () => {
        const auth = useAuthStore()
        auth.account = null
        void router.push({ name: 'login' })
      },
    },
  )
  void loadWorldEntities()
}

onMounted(async () => {
  if (pickedCampaignId.value) {
    await loadCampaign(pickedCampaignId.value)
    return
  }
  // No world pinned (global /forge entry): list the DM's worlds; authoring
  // can start immediately, only submission needs a world.
  picking.value = true
  await campaigns.list()
})

async function pickWorld(id: string) {
  pickedCampaignId.value = id
  picking.value = false
  loadError.value = null
  await loadCampaign(id)
}

onUnmounted(() => {
  disconnectSocket?.()
})

const recentJobs = computed(() => jobs.addCharacterJobs(campaignId.value).slice(0, 10))
const inFlight = computed(() => jobs.addCharacterInFlight(campaignId.value))

// ---------------------------------------------------------------------------
// Form state — the flat AR24 record plus the stat block. Every field a
// non-blank string by the canonical schema; the DM types it, the gate
// verifies it (the form mirrors `characterSchema.ts`, it never re-rules it).
// ---------------------------------------------------------------------------

type RecordTextField = Exclude<
  keyof CharacterRecord,
  'role' | 'world_integration' | 'stat_block' | 'boss'
>

const RECORD_FIELDS: Array<{ key: RecordTextField; label: string; long?: boolean; hint?: string }> =
  [
    { key: 'name', label: 'Name' },
    { key: 'level_cr', label: 'Level / CR', hint: 'display text, e.g. "level 5" or "CR 4"' },
    { key: 'race_type', label: 'Race / type' },
    { key: 'class_profession', label: 'Class / profession' },
    { key: 'alignment', label: 'Alignment' },
    { key: 'personality', label: 'Personality', long: true },
    { key: 'secret', label: 'Secret', long: true },
    { key: 'rumor', label: 'Rumor', long: true },
    { key: 'party_hook', label: 'Party hook', long: true },
    { key: 'appearance', label: 'Appearance', long: true },
    { key: 'background', label: 'Background', long: true },
    { key: 'goals', label: 'Goals', long: true },
    { key: 'relationships', label: 'Relationships', long: true },
    { key: 'voice_style', label: 'Voice style', long: true },
    { key: 'catchphrases', label: 'Catchphrases', long: true },
  ]

const ROLES = ['NPC', 'BBEG', 'Monster'] as const

type WorldIntegrationField = keyof CharacterRecord['world_integration']

const WORLD_INTEGRATION_FIELDS: Array<{ key: WorldIntegrationField; label: string }> = [
  { key: 'reputation', label: 'Reputation' },
  { key: 'factions', label: 'Factions' },
  { key: 'current_location', label: 'Current location' },
  { key: 'reaction_matrix', label: 'Reaction matrix' },
  { key: 'on_defeat', label: 'On defeat' },
]

type BossField = keyof NonNullable<CharacterRecord['boss']>

const BOSS_FIELDS: Array<{ key: BossField; label: string }> = [
  { key: 'lair_actions', label: 'Lair actions' },
  { key: 'legendary_actions', label: 'Legendary actions' },
  { key: 'immunities', label: 'Immunities' },
  { key: 'vulnerabilities', label: 'Vulnerabilities' },
]

const ATTRIBUTES = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const

/** The closed SRD vocabularies the canonical schema pins — the form
 * offers them as dropdowns so a legal value is one click (the gate's
 * violation text lists exactly these). */
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

function blankRecord(): CharacterRecord {
  return {
    name: '',
    role: 'NPC',
    level_cr: '',
    race_type: '',
    class_profession: '',
    alignment: '',
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
    world_integration: {
      reputation: '',
      factions: '',
      current_location: '',
      reaction_matrix: '',
      on_defeat: '',
    },
    stat_block: blankStatBlock(),
  }
}

function blankStatBlock(): StatBlock {
  return {
    identity: { role: 'NPC', race: '' },
    attributes: { str: 10, dex: 10, con: 10, int: 10, wis: 10, cha: 10 },
    combat: { ac: 10, hp: 1 },
    skills: [],
    actions: [],
    traits: [],
    spells: [],
  }
}

const record = ref<CharacterRecord>(blankRecord())

/** boss is REQUIRED iff role is BBEG/Monster and ABSENT otherwise — the
 * form mirrors the conditional by adding/removing the section. */
const roleNeedsBoss = computed(() => record.value.role !== 'NPC')

function syncBossSection() {
  if (roleNeedsBoss.value && !record.value.boss) {
    record.value.boss = {
      lair_actions: '',
      legendary_actions: '',
      immunities: '',
      vulnerabilities: '',
    }
  } else if (!roleNeedsBoss.value) {
    delete record.value.boss
  }
}

// --- Stat block ------------------------------------------------------------

const statOpen = ref(true)
const statBlock = computed(() => record.value.stat_block)

/** Monster carries cr, NPC/BBEG carry level — never both (AR25). */
const powerSlot = ref<'level' | 'cr' | 'none'>('level')
const levelValue = ref<number | null>(null)
const crValue = ref('')

function syncPowerSlot() {
  const identity = statBlock.value.identity
  delete identity.level
  delete identity.cr
  if (powerSlot.value === 'level' && levelValue.value !== null) {
    identity.level = levelValue.value
  } else if (powerSlot.value === 'cr' && crValue.value.trim()) {
    identity.cr = crValue.value.trim()
  }
}

const skillDrafts = ref<SkillEntry[]>([])
const actionDrafts = ref<ActionEntry[]>([])
const traitDrafts = ref<Array<{ name: string; description: string }>>([])
const spellText = ref('')

function syncLists() {
  statBlock.value.skills = skillDrafts.value.filter((s) => s.name.trim() && s.description.trim())
  statBlock.value.actions = actionDrafts.value
    .filter((a) => a.name.trim() && a.description.trim())
    .map((a) => {
      const damage = normalizeDamage(a.damage)
      const entry: ActionEntry = { name: a.name.trim(), description: a.description }
      if (damage) entry.damage = damage
      return entry
    })
  statBlock.value.traits = traitDrafts.value.filter((t) => t.name.trim() && t.description.trim())
  statBlock.value.spells = spellText.value
    .split(/\r?\n/)
    .map((line: string) => line.trim())
    .filter(Boolean)
}

/** The spaced variant ('2d6 + 2') normalizes to the tight canonical
 * form before submission (the schema contract's ruling). */
function normalizeDamage(raw: string | undefined): string {
  const tight = (raw ?? '').replace(/\s+/g, '')
  return DICE_PATTERN.test(tight) ? tight : ''
}

function addSkill() {
  skillDrafts.value.push({ name: '', description: '' })
}
function addAction() {
  actionDrafts.value.push({ name: '', description: '' })
}
function addTrait() {
  traitDrafts.value.push({ name: '', description: '' })
}

// --- Declared relations (Tier-1: a committed entity ULID) -------------------

interface RelationDraft {
  type: string
  counter: string
  target_id: string
}

const relationDrafts = ref<RelationDraft[]>([])
/** The closed EDGE_TYPES vocabulary — the picker never invents a type. */
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
  try {
    const response = await apiFetch<ExportShape>(
      `/api/campaigns/${encodeURIComponent(campaignId.value)}/export`,
    )
    worldEntities.value = response.entities.map((entity: EntityRef) => ({
      id: entity.id,
      name: entity.name,
      kind: entity.kind,
    }))
  } catch {
    // The relation picker degrades to empty when the world cannot be
    // read; the gate re-checks target existence either way.
  }
}

function addRelation() {
  relationDrafts.value.push({ type: 'member_of', counter: '', target_id: '' })
}

function buildRelations(): DeclaredRelation[] | undefined {
  const relations: DeclaredRelation[] = []
  for (const draft of relationDrafts.value) {
    if (!draft.target_id.trim()) continue
    const relation: DeclaredRelation = { type: draft.type, target_id: draft.target_id.trim() }
    const counter = Number.parseInt(draft.counter, 10)
    if (draft.counter.trim() && Number.isFinite(counter)) relation.counter = counter
    relations.push(relation)
  }
  return relations.length > 0 ? relations : undefined
}

// --- Submission -------------------------------------------------------------

function formSheet(): CharacterSheet {
  syncPowerSlot()
  syncLists()
  // A deep copy: the form keeps its state while the submission snapshot
  // goes to the gate (the job commits whatever was submitted).
  const sheet = JSON.parse(JSON.stringify(record.value)) as CharacterRecord
  // The stat block's identity.role must match the record role — one
  // control (the sheet's Role select) drives both, so the mismatch
  // violation can never fire.
  sheet.stat_block.identity.role = sheet.role
  // An empty optional ('— none —') is an ABSENT key, never a blank string.
  if (!sheet.stat_block.identity.class) delete sheet.stat_block.identity.class
  if (!sheet.stat_block.identity.alignment) delete sheet.stat_block.identity.alignment
  return {
    record: sheet,
    relations: buildRelations(),
  }
}

const hasContent = computed(() => record.value.name.trim().length > 0)

async function submit() {
  if (!pickedCampaignId.value) {
    error.value = 'Pick a world first — the commit needs somewhere to land.'
    return
  }
  error.value = null
  violations.value = []
  submitting.value = true
  try {
    await jobs.submitCharacters(campaignId.value, [formSheet()])
  } catch (err) {
    if (err instanceof ApiError) {
      error.value = err.message
      const details = err.details as { violations?: unknown } | undefined
      if (Array.isArray(details?.violations)) {
        violations.value = (details.violations as unknown[]).filter(
          (v): v is string => typeof v === 'string',
        )
      }
    } else {
      error.value = 'Could not enqueue the character.'
    }
  } finally {
    submitting.value = false
  }
}

function stateLabel(job: Job): string {
  return job.state === 'queued' ? `Queued (position ${job.queue_position ?? '…'})` : job.state
}

function jobError(job: Job): string | null {
  return job.state === 'failed' ? (job.error ?? 'The commit failed.') : null
}

function entityCount(job: Job): number | null {
  if (job.state !== 'succeeded' || !job.result) return null
  const ids = job.result['entity_ids']
  return Array.isArray(ids) ? ids.length : null
}

function edgeCount(job: Job): number | null {
  if (job.state !== 'succeeded' || !job.result) return null
  const edges = job.result['edges']
  return Array.isArray(edges) ? edges.length : null
}
</script>

<template>
  <section>
    <h1>Character forge</h1>
    <p class="muted">
      A fully-authored sheet goes straight into the world — no model touches it. Every field is
      yours; the forge only checks the sheet's shape.
    </p>

    <div v-if="loadError" class="card">
      <p class="error">{{ loadError }}</p>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>

    <!-- Global /forge entry: pick the world a sheet will enter. Authoring
         below works before any pick — only the submit binds to a world. -->
    <div v-if="picking || !pickedCampaignId" class="card">
      <h2>Choose a world</h2>
      <p class="muted small">
        The sheet is authored here; pick which world it enters when you commit. (You can start
        writing first — the choice is only needed at submit.)
      </p>
      <p v-if="campaigns.loading" class="muted">Loading your worlds…</p>
      <p v-else-if="campaigns.campaigns.length === 0" class="muted">
        No worlds yet — create one first, or keep authoring and bind later.
        <RouterLink :to="{ name: 'campaigns' }">Your worlds</RouterLink>
      </p>
      <ul v-else class="world-picker">
        <li v-for="campaign in campaigns.campaigns" :key="campaign.id">
          <button type="button" class="link" @click="pickWorld(campaign.id)">
            {{ campaign.title }}
          </button>
        </li>
      </ul>
    </div>
    <template v-if="!loadError">
      <form class="card" @submit.prevent="submit">
        <h2>Sheet</h2>
        <div class="grid">
          <label>
            Name
            <input v-model="record.name" type="text" required />
          </label>
          <label>
            Role
            <select v-model="record.role" @change="syncBossSection">
              <option v-for="role in ROLES" :key="role" :value="role">{{ role }}</option>
            </select>
          </label>
        </div>
        <div class="grid">
          <label v-for="field in RECORD_FIELDS.filter((f) => f.key !== 'name')" :key="field.key">
            {{ field.label }}
            <textarea v-if="field.long" v-model="record[field.key]" rows="2"></textarea>
            <input v-else v-model="record[field.key]" type="text" />
          </label>
        </div>

        <h3>World integration</h3>
        <div class="grid">
          <label v-for="field in WORLD_INTEGRATION_FIELDS" :key="field.key">
            {{ field.label }}
            <textarea v-model="record.world_integration[field.key]" rows="2"></textarea>
          </label>
        </div>

        <div v-if="record.boss">
          <h3>Boss section ({{ record.role }})</h3>
          <div class="grid">
            <label v-for="field in BOSS_FIELDS" :key="field.key">
              {{ field.label }}
              <textarea v-model="record.boss[field.key]" rows="2"></textarea>
            </label>
          </div>
        </div>

        <h2>
          <button type="button" class="link" @click="statOpen = !statOpen">
            {{ statOpen ? '▾' : '▸' }} Stat block
          </button>
        </h2>
        <div v-if="statOpen">
          <h3>Identity</h3>
          <div class="grid">
            <label>
              Race (SRD)
              <select v-model="statBlock.identity.race" required>
                <option value="" disabled>pick a race…</option>
                <option v-for="race in SRD_RACES" :key="race" :value="race">{{ race }}</option>
              </select>
            </label>
            <label>
              Power slot
              <select v-model="powerSlot">
                <option value="level">Level (NPC/BBEG)</option>
                <option value="cr">CR (Monster)</option>
                <option value="none">Leave blank</option>
              </select>
            </label>
            <label v-if="powerSlot === 'level'">
              Level (1–20)
              <input
                v-model.number="levelValue"
                type="number"
                min="1"
                max="20"
                @change="syncPowerSlot"
              />
            </label>
            <label v-if="powerSlot === 'cr'">
              CR (integer or '1/2')
              <input v-model="crValue" type="text" @change="syncPowerSlot" />
            </label>
            <label>
              Class (SRD)
              <select v-model="statBlock.identity.class">
                <option value="">— none —</option>
                <option v-for="cls in SRD_CLASSES" :key="cls" :value="cls">{{ cls }}</option>
              </select>
            </label>
            <label>
              Alignment
              <select v-model="statBlock.identity.alignment">
                <option value="">— none —</option>
                <option v-for="alignment in ALIGNMENTS" :key="alignment" :value="alignment">
                  {{ alignment }}
                </option>
              </select>
            </label>
            <label class="muted">
              Role (from the sheet)
              <input :value="record.role" type="text" disabled />
            </label>
          </div>

          <h3>Attributes &amp; combat</h3>
          <div class="grid">
            <label v-for="attr in ATTRIBUTES" :key="attr">
              {{ attr.toUpperCase() }}
              <input v-model.number="statBlock.attributes[attr]" type="number" min="1" max="30" />
            </label>
            <label>
              AC
              <input v-model.number="statBlock.combat.ac" type="number" min="0" />
            </label>
            <label>
              HP
              <input v-model.number="statBlock.combat.hp" type="number" min="1" />
            </label>
            <label>
              Hit dice
              <input
                v-model="statBlock.combat.hit_dice"
                type="text"
                placeholder="e.g. 5d8+9, 2d6, 4d10+8"
              />
              <span class="muted small">free text — NdM[+K] shape; the gate checks the dice</span>
            </label>
          </div>

          <h3>
            Skills
            <button type="button" class="link" @click="addSkill">+ add</button>
          </h3>
          <div v-for="(skill, i) in skillDrafts" :key="`s${i}`" class="row">
            <input v-model="skill.name" type="text" placeholder="Religion" />
            <input v-model="skill.description" type="text" placeholder="+7, ritual caster…" />
            <button type="button" class="link" @click="skillDrafts.splice(i, 1)">✕</button>
          </div>

          <h3>
            Actions
            <button type="button" class="link" @click="addAction">+ add</button>
          </h3>
          <div v-for="(action, i) in actionDrafts" :key="`a${i}`" class="action-row">
            <div class="row">
              <input v-model="action.name" type="text" placeholder="Longsword" />
              <input
                v-model="action.damage"
                type="text"
                placeholder="1d8+2 (blank = no damage roll)"
              />
              <button type="button" class="link" @click="actionDrafts.splice(i, 1)">✕</button>
            </div>
            <textarea
              v-model="action.description"
              rows="2"
              placeholder="Melee Weapon Attack: +5 to hit. Hit: 7 (1d8+2) slashing damage."
            ></textarea>
          </div>

          <h3>
            Traits
            <button type="button" class="link" @click="addTrait">+ add</button>
          </h3>
          <div v-for="(trait, i) in traitDrafts" :key="`t${i}`" class="row">
            <input v-model="trait.name" type="text" placeholder="Magic Resistance" />
            <input v-model="trait.description" type="text" placeholder="Advantage on saves…" />
            <button type="button" class="link" @click="traitDrafts.splice(i, 1)">✕</button>
          </div>

          <h3>Spells (one per line)</h3>
          <textarea v-model="spellText" rows="3"></textarea>
        </div>

        <h2>Relations</h2>
        <p class="muted small">
          Wire the sheet to entities already in the world. The forge never auto-creates a target —
          build the place first, then bind.
        </p>
        <div v-for="(relation, i) in relationDrafts" :key="`r${i}`" class="row relation">
          <select v-model="relation.type">
            <option v-for="edgeType in EDGE_TYPES" :key="edgeType" :value="edgeType">
              {{ edgeType.replaceAll('_', ' ') }}
            </option>
          </select>
          <select v-model="relation.target_id">
            <option value="" disabled>pick a committed entity…</option>
            <option v-for="entity in worldEntities" :key="entity.id" :value="entity.id">
              {{ entity.name }} ({{ entity.kind }})
            </option>
          </select>
          <input v-model="relation.counter" type="text" placeholder="counter" />
          <button type="button" class="link" @click="relationDrafts.splice(i, 1)">✕</button>
        </div>
        <button type="button" class="link" @click="addRelation">+ relation</button>

        <details v-if="violations.length > 0" class="violations" open>
          <summary class="error">
            The sheet needs fixes before it can enter the world ({{ violations.length }})
          </summary>
          <ul class="error">
            <li v-for="violation in violations" :key="violation" class="mono">
              {{ violation }}
            </li>
          </ul>
        </details>
        <p v-if="violations.length === 0 && error" class="error">{{ error }}</p>

        <button type="submit" :disabled="submitting || inFlight || !hasContent">
          {{ inFlight ? 'Committing…' : submitting ? 'Enqueuing…' : 'Commit to the world' }}
        </button>
        <p v-if="!pickedCampaignId" class="muted small">
          Pick a world above when you're ready — the commit needs somewhere to land.
        </p>
        <p class="muted small">
          Zero-model by construction: this commit makes no LLM call, and characters never merge —
          every sheet is a fresh, distinct person even with a duplicate name.
        </p>
      </form>

      <div v-if="recentJobs.length > 0" class="card job">
        <h2>Recent commits</h2>
        <div v-for="job in recentJobs" :key="job.id" class="job-row">
          <dl>
            <dt>State</dt>
            <dd>{{ stateLabel(job) }}</dd>
            <template v-if="entityCount(job) !== null">
              <dt>Committed</dt>
              <dd>
                {{ entityCount(job) }} character{{ entityCount(job) === 1 ? '' : 's'
                }}<template v-if="(edgeCount(job) ?? 0) > 0">
                  + {{ edgeCount(job) }} relation{{ edgeCount(job) === 1 ? '' : 's' }}</template
                >
              </dd>
            </template>
            <template v-else-if="jobError(job)">
              <dt>Error</dt>
              <dd class="error mono">{{ jobError(job) }}</dd>
            </template>
          </dl>
        </div>
      </div>
    </template>
  </section>
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
.world-picker {
  list-style: none;
  padding: 0;
  margin: 0.5rem 0 0;
  display: grid;
  gap: 0.35rem;
}
.violations summary {
  cursor: pointer;
  font-weight: 600;
}
.violations ul {
  margin: 0.5rem 0 0;
  max-height: 14rem;
  overflow-y: auto;
}
textarea {
  resize: vertical;
}
.row {
  grid-template-columns: 1fr 2fr auto;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
  align-items: center;
}
.row.relation {
  grid-template-columns: 1fr 2fr 1fr auto;
}
.action-row {
  margin-bottom: 0.5rem;
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
.job dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 1rem;
  margin: 0;
}
.job dt {
  color: #9aa0a6;
}
.job-row + .job-row {
  border-top: 1px solid #2c3038;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
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
