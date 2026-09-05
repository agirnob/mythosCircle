import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { apiFetch } from '../api/client'
import type { WsMessage } from '../ws'

type Job = components['schemas']['JobResponse']

const TERMINAL_STATES: ReadonlySet<string> = new Set(['succeeded', 'failed', 'cancelled'])

/** Monotonic state ordinal: queued(0) < running(1) < terminal(2). */
function stateOrdinal(state: string): number {
  if (state === 'running') return 1
  if (TERMINAL_STATES.has(state)) return 2
  return 0
}

/** The guided build-in submission shape (spec-2.1): four free-form sections. */
export interface BuildInPayload {
  places: string[]
  factions: string[]
  key_figures: string[]
  notes: string
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
     * those AR24 content sections.
     */
    async submitRegenerate(
      campaignId: string,
      target: { kind: 'entity' | 'candidate'; id: string },
      sections: string[] | null,
    ) {
      const payload: Record<string, unknown> = { target }
      if (sections !== null) payload['sections'] = sections
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
     * payload + appearance at enqueue).
     */
    async submitPortrait(campaignId: string, entityId: string) {
      const job = await apiFetch<Job>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: campaignId,
          kind: 'image',
          payload: { entity_id: entityId },
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
