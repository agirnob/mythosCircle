/**
 * The canonical character-submission schema — the FRONTEND MIRROR half of
 * the three-layer validation gate (owner ruling F3, spec: hybrid
 * authorship). ONE contract, two consumers:
 *
 * - this module (the future Add-Character form mirrors it client-side:
 *   disable submit + inline field violations while invalid), and
 * - the backend's synchronous enqueue gate (`backend/app/store/direct.py`,
 *   enforced at POST /api/characters → 202 job / 422 violations) — which
 *   ALWAYS re-validates, so a drift between the mirrors can never commit.
 *
 * Template-form ruling F1-F2: prose lives ONLY in description slots;
 * conditional exceptions belong in an action's description. Ruling F4:
 * every submission commits a FRESH ULID — same-name characters coexist.
 * Path-2 guardrail: `target_name` is BANNED on the direct form (the
 * enqueue schema rejects it) — freeform targets are the hybrid path's
 * mandate mechanism, not this form's.
 */

/** A committed entity ULID (Tier-1 declared-relation target). */
export type TargetId = string;
/** Another staged sheet's key in the SAME submission (Tier-2 target). */
export type TargetKey = string;

/** One declared relation: `{type, counter?, target_id | target_key}` —
 * exactly one target key. `target_name` is a schema violation here. */
export interface DeclaredRelation {
  type: string; // closed EDGE_TYPES vocabulary, e.g. 'member_of', 'located_in'
  counter?: number;
  target_id?: TargetId;
  target_key?: TargetKey;
}

/** The canonical dice formula for an authored action's damage slot:
 * tight operators only ('2d6', '1d8+7', '2d6-1') — the form normalizes
 * the spaced '2d6 + 2' variant before submission. */
export const DICE_PATTERN = /^\d+d\d+([+-]\d+)?$/;

/** A stat-block skill entry: a {name, description} text block (the sheet
 * renders a bonus-less skill as the bare name). */
export interface SkillEntry {
  name: string;
  description: string;
}

/** A stat-block action: prose description (conditional exceptions live
 * HERE, ruling F1-F2) plus the optional dice-string damage. */
export interface ActionEntry {
  name: string;
  description: string;
  damage?: string; // must match DICE_PATTERN
}

export interface StatBlock {
  identity: {
    role: 'NPC' | 'BBEG' | 'Monster';
    race: string;
    /** NPC/BBEG carry level (1..20), Monster carries cr — never both. */
    level?: number;
    cr?: number | string; // integer or a CR fraction ('1/2')
    class?: string;
    alignment?: string;
  };
  attributes: { str: number; dex: number; con: number; int: number; wis: number; cha: number };
  combat: { ac: number; hp: number; hit_dice?: string };
  skills?: SkillEntry[];
  actions?: ActionEntry[];
  traits?: { name: string; description: string }[];
  spells?: string[];
}

export interface WorldIntegration {
  reputation: string;
  factions: string;
  current_location: string;
  reaction_matrix: string; // ONE non-blank prose string, never an object
  on_defeat: string;
}

/** The flat AR24 record: every named field a non-blank top-level string
 * (never grouped under 'identity'/'lore' subsections), plus the two
 * section objects and the conditional boss section (required iff role is
 * BBEG/Monster, ABSENT otherwise). Unknown keys are schema violations. */
export interface CharacterRecord {
  name: string;
  role: 'NPC' | 'BBEG' | 'Monster';
  level_cr: string; // display text only ('level 5' / 'CR 4')
  race_type: string;
  class_profession: string;
  alignment: string;
  personality: string;
  secret: string;
  rumor: string;
  party_hook: string;
  appearance: string;
  background: string;
  goals: string;
  relationships: string;
  voice_style: string;
  catchphrases: string;
  world_integration: WorldIntegration;
  stat_block: StatBlock;
  boss?: {
    lair_actions: string;
    legendary_actions: string;
    immunities: string;
    vulnerabilities: string;
  };
}

/** One fully-authored character sheet: a fresh-ULID character (F4) plus
 * its declared relations. `key` is REQUIRED on any sheet another sheet's
 * Tier-2 relation targets. */
export interface CharacterSheet {
  key?: string;
  record: CharacterRecord;
  relations?: DeclaredRelation[];
}

/** The POST /api/characters body: 202 {job_id} or 422 with
 * `details.violations` carrying the exact schema paths. */
export interface CharacterSubmission {
  campaign_id: string;
  characters: CharacterSheet[];
}
