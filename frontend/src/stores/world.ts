/**
 * Read-only world view store (spec-2-7).
 *
 * Holds the export-JSON projection (`GET /api/campaigns/{id}/export`) per
 * campaign plus loading/error/not-found flags. Rendering decisions —
 * grouping, counter labels, stat-block sections — live in the view and
 * components; this store never writes world state.
 *
 * Live updates (FR1, NFR9): WS frames only signal "the world changed", so
 * `handleJobMessage` re-fetches the snapshot — once for a build-in
 * `job_progress` at/after the wave-1 commit (progress 0.5), once for any
 * terminal build-in frame (a mid-wave-2 failure still leaves wave 1
 * committed), and the view re-fetches on WS reconnect. Fetches coalesce:
 * a frame landing while a fetch is in flight marks the entry dirty and
 * one trailing fetch covers the delta; parallel fetches never stack.
 */
import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { ApiError, apiFetch } from '../api/client'
import { useJobsStore } from './jobs'
import type { WsMessage } from '../ws'

type WorldExport = components['schemas']['WorldExport']

/** Wave-1 commit boundary — build_in.py reports 0.5 (core) and 1.0 (complete). */
const WAVE1_PROGRESS = 0.5

/** Terminal frames: refetch even on failure/cancel (wave 1 may be committed). */
const TERMINAL_TYPES: ReadonlySet<WsMessage['type']> = new Set([
  'job_done',
  'job_failed',
  'job_cancelled',
])

export interface WorldEntry {
  world: WorldExport | null
  loading: boolean
  error: string | null
  notFound: boolean
  /** A fetch is in flight — frames arriving now set `dirty` instead of stacking. */
  fetching: boolean
  /** A refetch is owed once the in-flight fetch settles. */
  dirty: boolean
}

function emptyEntry(): WorldEntry {
  return {
    world: null,
    loading: false,
    error: null,
    notFound: false,
    fetching: false,
    dirty: false,
  }
}

export const useWorldStore = defineStore('world', {
  state: () => ({
    byCampaign: {} as Record<string, WorldEntry>,
  }),
  getters: {
    entry:
      (state) =>
      (campaignId: string): WorldEntry =>
        state.byCampaign[campaignId] ?? emptyEntry(),
  },
  actions: {
    /** Initial mount load — same coalescing fetch as refetches. */
    async load(campaignId: string) {
      await this.fetchSnapshot(campaignId)
    },
    /** Fire-and-forget re-sync (WS frame / reconnect); coalesced. */
    requestRefetch(campaignId: string) {
      void this.fetchSnapshot(campaignId)
    },
    /**
     * The single fetch path for an entry: never stacks — an overlapping
     * call marks `dirty` and one trailing fetch runs after the current
     * one settles, so a frame mid-fetch still lands its delta.
     */
    async fetchSnapshot(campaignId: string) {
      if (!this.byCampaign[campaignId]) {
        this.byCampaign[campaignId] = emptyEntry()
      }
      // Read the entry back through the reactive proxy — mutating a raw
      // object stashed in state never re-triggers the view.
      const entry = this.byCampaign[campaignId]
      if (entry.fetching) {
        entry.dirty = true
        return
      }
      entry.fetching = true
      entry.loading = true
      entry.error = null
      try {
        const world = await apiFetch<WorldExport>(
          `/api/campaigns/${encodeURIComponent(campaignId)}/export`,
        )
        entry.world = world
        entry.notFound = false
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          // Foreign or unknown campaign — the indistinguishable 404; a
          // previous snapshot must not linger in the not-found view.
          entry.world = null
          entry.notFound = true
        } else {
          entry.error = err instanceof ApiError ? err.message : 'Could not load the world.'
        }
      } finally {
        entry.fetching = false
        entry.loading = false
        if (entry.dirty) {
          entry.dirty = false
          void this.fetchSnapshot(campaignId)
        }
      }
    },
    /**
     * WS dispatch for the world view. Mirrors the jobs-store pattern:
     * let the jobs store absorb the frame first (it also REST-recovers an
     * uncached job id), then decide by the cached job's kind — the wire
     * frame itself carries no kind. Refetch on a build-in `job_progress`
     * at/after 0.5 (wave 1 committed) and on any terminal build-in frame;
     * sub-threshold progress, queue_changed, and non-build_in kinds are
     * ignored. A terminal frame for an UNRESOLVED kind (the job never
     * reached the cache — e.g. REST recovery failed) still refetches:
     * `job_done` is the last frame a build-in emits, and the fetch is
     * read-only with coalescing already preventing stacking.
     */
    async handleJobMessage(campaignId: string, message: WsMessage) {
      const jobs = useJobsStore()
      try {
        await jobs.handleWsMessage(campaignId, message)
      } catch {
        // REST recovery failed (e.g. transient server error) — fall
        // through to the cache check.
      }
      const terminal = TERMINAL_TYPES.has(message.type)
      const job = jobs.byId[message.job_id]
      if (job) {
        if (job.kind !== 'build_in' || job.campaign_id !== campaignId) return
        const qualifies =
          (message.type === 'job_progress' && (message.progress ?? 0) >= WAVE1_PROGRESS) || terminal
        if (!qualifies) return
      } else if (!terminal) {
        return
      }
      void this.fetchSnapshot(campaignId)
    },
  },
})
