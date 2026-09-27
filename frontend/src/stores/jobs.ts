import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { apiFetch } from '../api/client'
import type { WsMessage } from '../ws'
import type { PortraitOptions } from '../lib/portrait'
import { toPortraitPayload } from '../lib/portrait'

type Job = components['schemas']['JobResponse']

const TERMINAL_STATES: ReadonlySet<string> = new Set(['succeeded', 'failed', 'cancelled'])

/** Monotonic state ordinal: queued(0) < running(1) < terminal(2). */
function stateOrdinal(state: string): number {
  if (state === 'running') return 1
  if (TERMINAL_STATES.has(state)) return 2
  return 0
}

/**
 * The guided build-in submission shape (spec-2.1): four free-form
 * sections. Hybrid authorship (path 1) extends `key_figures`: an entry
 * is a legacy plain string OR one authored seed — `{name, role?, record?,
 * relations?, key?}` where `relations` carry `target_name` (the
 * mandate tier; path 1 owns it). The backend's canonical validator is
 * the authority; these types mirror its entry shape.
 */
export interface AuthoredRelationSeed {
  type: string
  /** Exactly one target tier: an existing entity's ULID, another seed's
   * key, or a mandated name (created when missing — path 1 only). */
  target_id?: string
  target_key?: string
  target_name?: string
  counter?: number
}

export interface AuthoredFigureSeed {
  name: string
  role?: string
  /** The authored AR24 fields — filled ones only; a partial stat_block
   * carries object subsections, so the value type is open. */
  record?: Record<string, unknown>
  relations?: AuthoredRelationSeed[]
  key?: string
}

export interface BuildInPayload {
  /** Generic-library drafts omit the world-shaping sections entirely. */
  places?: string[]
  factions?: string[]
  key_figures: (string | AuthoredFigureSeed)[]
  notes?: string
  /** Generic-library builds only: the theme whose default seed the runner
   * substitutes for the library's own (never generation context). */
  theme?: string
}

export const useJobsStore = defineStore('jobs', {
  state: () => ({
    byId: {} as Record<string, Job>,
  }),
  getters: {
    /** Jobs for a campaign, newest first (the wire list is oldest first). */
    forCampaign: (state) => (campaignId: string) =>
      Object.values(state.byId)
        .filter((job) => job.campaign_id === campaignId)
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
    /** Build-in jobs for a campaign, newest first. */
    buildInJobs: (state) => (campaignId: string) =>
      Object.values(state.byId)
        .filter((job) => job.campaign_id === campaignId && job.kind === 'build_in')
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
    latestBuildIn: (state) => (campaignId: string) =>
      Object.values(state.byId)
        .filter((job) => job.campaign_id === campaignId && job.kind === 'build_in')
        .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null,
    /** A regenerate for this target (entity or candidate row) still
     * queued/running — the in-flight discipline (spec-3-6): a second
     * enqueue for the same target must not burn a second generation;
     * the second replace_candidate_payload would win, stranding the
     * first. */
    regenerateInFlight:
      (state) =>
      (campaignId: string, kind: 'entity' | 'candidate', id: string): boolean =>
        Object.values(state.byId).some((job) => {
          if (job.campaign_id !== campaignId || job.kind !== 'regenerate') return false
          if (TERMINAL_STATES.has(job.state)) return false
          const payload = job.payload as { target?: { kind?: string; id?: string } } | null
          return payload?.target?.kind === kind && payload?.target?.id === id
        }),
    /** A build-in for this campaign still queued/running (spec-2.1) — the
     * in-flight discipline mirroring ``regenerateInFlight``: the seed form
     * keeps its text after submit, so a second click must not burn a second
     * build; a FAILED job releases the button with the text still there for
     * the retry. */
    buildInInFlight:
      (state) =>
      (campaignId: string): boolean =>
        Object.values(state.byId).some(
          (job) =>
            job.campaign_id === campaignId &&
            job.kind === 'build_in' &&
            !TERMINAL_STATES.has(job.state),
        ),
    /** A portrait job for this entity still queued/running (spec-4.1) —
     * the in-flight discipline mirroring ``regenerateInFlight``: a second
     * enqueue for the same entity must not burn a second generation
     * while the first is still pending; a FAILED job releases the button
     * so the DM can re-trigger. */
    portraitInFlight:
      (state) =>
      (campaignId: string, entityId: string): boolean =>
        Object.values(state.byId).some((job) => {
          if (job.campaign_id !== campaignId || job.kind !== 'image') return false
          if (TERMINAL_STATES.has(job.state)) return false
          const payload = job.payload as { entity_id?: string } | null
          return payload?.entity_id === entityId
        }),
    /** A reveal-video job for this entity still queued/running
     * (spec-4.2) — the in-flight discipline mirroring
     * ``portraitInFlight``: a second enqueue for the same entity must
     * not burn a second generation while the first is still pending; a
     * FAILED job releases the button so the DM can re-trigger. */
    videoInFlight:
      (state) =>
      (campaignId: string, entityId: string): boolean =>
        Object.values(state.byId).some((job) => {
          if (job.campaign_id !== campaignId || job.kind !== 'video') return false
          if (TERMINAL_STATES.has(job.state)) return false
          const payload = job.payload as { entity_id?: string } | null
          return payload?.entity_id === entityId
        }),
    /** A video-prompt draft job for this entity still queued/running
     * (spec-4.6) — the in-flight discipline mirroring ``videoInFlight``:
     * a second enqueue for the same entity must not burn a second draft
     * while the first is pending; a FAILED job releases the button. */
    videoPromptInFlight:
      (state) =>
      (campaignId: string, entityId: string): boolean =>
        Object.values(state.byId).some((job) => {
          if (job.campaign_id !== campaignId || job.kind !== 'video_prompt') return false
          if (TERMINAL_STATES.has(job.state)) return false
          const payload = job.payload as { entity_id?: string } | null
          return payload?.entity_id === entityId
        }),
    /** The latest SUCCEEDED draft prompt for an entity (spec-4.6), or
     * null. The draft is a job result the frontend holds — session-only
     * durability (Ask-First 1 answer): a reload loses it and the DM
     * re-drafts; never a media row (a prompt is not a file). */
    videoPromptFor:
      (state) =>
      (campaignId: string, entityId: string): string | null => {
        const job = Object.values(state.byId)
          .filter((j) => {
            if (j.campaign_id !== campaignId || j.kind !== 'video_prompt') return false
            if (j.state !== 'succeeded') return false
            const payload = j.payload as { entity_id?: string } | null
            return payload?.entity_id === entityId
          })
          .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
        if (!job?.result) return null
        const prompt = job.result['prompt']
        return typeof prompt === 'string' && prompt.trim() ? prompt : null
      },
  },
  actions: {
    /**
     * Monotonic merge (review round 2): WS frames and REST snapshots can
     * arrive out of order, so a row only moves forward. A stale snapshot
     * (lower state ordinal, or equal ordinal with lower progress) never
     * regresses the cached row.
     */
    upsert(job: Job) {
      const cached = this.byId[job.id]
      if (cached) {
        const incoming = stateOrdinal(job.state)
        const current = stateOrdinal(cached.state)
        if (incoming < current) return // stale snapshot — keep the newer row
        if (incoming === current && job.progress < cached.progress) {
          this.byId[job.id] = { ...job, progress: cached.progress }
          return
        }
      }
      this.byId[job.id] = job
    },
    async submitBuildIn(campaignId: string, payload: BuildInPayload) {
      const job = await apiFetch<Job>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: campaignId,
          kind: 'build_in',
          payload,
        }),
      })
      this.upsert(job)
      return job
    },
    /**
     * Spec-3.5 re-roll: one regenerate job. `target` is the entity
     * (whole-character re-roll, staging a NEW proposal) or the proposed
     * candidate (whole or per-section re-roll, replacing its own row).
     * `sections` null = whole character; a non-empty list = exactly
     * those AR24 content sections. v3 (AD-33/36/38): `dial` and `guide`
     * ride the envelope — the enqueue re-validates both, and the dial
     * levels the shaped regenerate request.
     */
    async submitRegenerate(
      campaignId: string,
      target: { kind: 'entity' | 'candidate'; id: string },
      sections: string[] | null,
      options?: { dial?: string | null; guide?: string | null },
    ) {
      const payload: Record<string, unknown> = { target }
      if (sections !== null) payload['sections'] = sections
      if (options?.dial) payload['dial'] = options.dial
      if (options?.guide && options.guide.trim() !== '') payload['guide'] = options.guide.trim()
      const job = await apiFetch<Job>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: campaignId,
          kind: 'regenerate',
          payload,
        }),
      })
      this.upsert(job)
      return job
    },
    /**
     * Spec-4.1 portrait: one image job whose payload names the committed
     * entity — the portrait is a projection of the entity's committed
     * AR24 appearance, never free text (the backend validates the
     * payload + appearance at enqueue). The optional P1 knobs (style /
     * framing / background / custom_style) steer the generation; an
     * absent knob is the backend default.
     */
    async submitPortrait(campaignId: string, entityId: string, options?: PortraitOptions) {
      const job = await apiFetch<Job>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: campaignId,
          kind: 'image',
          payload: { entity_id: entityId, ...(options ? toPortraitPayload(options) : {}) },
        }),
      })
      this.upsert(job)
      return job
    },
    /**
     * Spec-4.6 reveal-video prompt draft: one video_prompt job whose
     * payload names the committed boss-tier entity (with a non-blank
     * appearance) — gemma authors a MiniMax-I2VA-compliant draft from
     * the entity's AR24 record + the writing guide. The draft lands as
     * the job result {entity_id, prompt}; the DM reviews/edits it in
     * the card before any render (two-phase; the backend validates the
     * payload + role + appearance at enqueue).
     */
    async submitRevealVideoPrompt(campaignId: string, entityId: string) {
      const job = await apiFetch<Job>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: campaignId,
          kind: 'video_prompt',
          payload: { entity_id: entityId },
        }),
      })
      this.upsert(job)
      return job
    },
    /**
     * Spec-4.2/4.6 reveal video: one video job whose payload names the
     * committed boss-tier entity. Without a prompt the clip prompt is a
     * backend projection of the entity's committed AR24 record
     * (appearance + boss + identity) — never free text (the backend
     * validates the payload + role + prompt at enqueue). With a
     * non-blank prompt (spec-4.6) the DM's approved text is used
     * VERBATIM by run_video — the draft-and-edit path's render.
     */
    async submitRevealVideo(campaignId: string, entityId: string, prompt?: string) {
      const promptValue = prompt?.trim() ?? ''
      const job = await apiFetch<Job>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: campaignId,
          kind: 'video',
          payload: promptValue
            ? { entity_id: entityId, prompt: promptValue }
            : { entity_id: entityId },
        }),
      })
      this.upsert(job)
      return job
    },
    /** Full re-sync after reconnect or a queue_changed (positions shift). */
    async syncList(campaignId: string) {
      let cursor: string | null = null
      let query: string
      let response: components['schemas']['JobListResponse']
      do {
        query = cursor === null ? '' : `&cursor=${encodeURIComponent(cursor)}`
        response = await apiFetch<components['schemas']['JobListResponse']>(
          `/api/jobs?campaign_id=${encodeURIComponent(campaignId)}${query}`,
        )
        for (const job of response.jobs) {
          this.upsert(job)
        }
        cursor = response.next_cursor
      } while (cursor !== null)
    },
    /**
     * AD-17 dispatch: WS frames carry only the delta ({type, job_id, state,
     * queue_position?, progress?}), never the full row — patch the cached row
     * in place, never clobber REST-fetched fields. queue_changed and terminal
     * frames re-sync the list (positions shift). A frame for an uncached job
     * (another tab's job, or a frame that beat the mount-time sync) recovers
     * via a full REST re-sync.
     */
    async handleWsMessage(campaignId: string, message: WsMessage) {
      const cached = this.byId[message.job_id]
      if (!cached) {
        await this.syncList(campaignId)
        return
      }
      cached.state = message.state
      if (message.progress !== undefined && message.progress !== null) {
        cached.progress = message.progress
      }
      if (message.queue_position !== undefined) {
        cached.queue_position = message.queue_position
      }
      const shiftsPositions: WsMessage['type'][] = [
        'queue_changed',
        'job_done',
        'job_failed',
        'job_cancelled',
      ]
      if (shiftsPositions.includes(message.type)) {
        await this.syncList(campaignId)
      }
    },
  },
})
