import { sessionGeneration, SessionChangedError } from '../api/session'
/**
 * Tonight store (v3 Tier-1/2; AD-26..29, AD-34/35).
 *
 * Holds the two Tonight READ projections per campaign: the run-state
 * (session images + knowledge toggles, `GET /run-state`) and the recent-
 * changes feed (`GET /revisions`) — plus the kinds registry
 * (`GET /kinds`) which the matrix-driven pickers re-fetch per walk mount
 * (AD-34: never bundled, never served stale past the mount — the
 * commit-time backend validation is the backstop).
 *
 * Writes: the Tier-2 gestures (session verbs, knowledge toggles) go
 * through their own REST routes and land back here as a run-state
 * refetch. The store never mutates world state locally — the same
 * discipline as the world store. Fetch coalescing mirrors the world
 * store: an overlapping call marks the entry dirty and one trailing
 * fetch covers the delta.
 */

import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { ApiError, apiFetch } from '../api/client'

type RevisionsResponse = components['schemas']['RevisionsResponse']
type RunStateResponse = components['schemas']['RunStateResponse']
type KindsResponse = components['schemas']['KindsResponse']
type RevisionSummary = components['schemas']['RevisionSummary']

export interface TonightEntry {
  /** Newest-first feed (AD-35: default 20, max 100). */
  revisions: RevisionSummary[] | null
  runState: RunStateResponse | null
  /** The kinds registry — re-fetched per walk mount by the views (AD-34). */
  kinds: KindsResponse | null
  loading: boolean
  error: string | null
  /** A fetch is in flight — frames arriving now set `dirty` instead of stacking. */
  fetching: boolean
  /** A refetch is owed once the in-flight fetch settles. */
  dirty: boolean
}

function emptyEntry(): TonightEntry {
  return {
    revisions: null,
    runState: null,
    kinds: null,
    loading: false,
    error: null,
    fetching: false,
    dirty: false,
  }
}

export const useTonightStore = defineStore('tonight', {
  state: () => ({
    byCampaign: {} as Record<string, TonightEntry>,
  }),
  getters: {
    entry:
      (state) =>
      (campaignId: string): TonightEntry =>
        state.byCampaign[campaignId] ?? emptyEntry(),
    /** The registry for a campaign, or null before the first mount fetch. */
    kindsFor:
      (state) =>
      (campaignId: string): KindsResponse | null =>
        state.byCampaign[campaignId]?.kinds ?? null,
  },
  actions: {
    /** Initial mount load: run-state + feed in parallel (coalesced). */
    async load(campaignId: string) {
      await this.fetchTonight(campaignId)
    },
    /** Per-mount registry fetch (AD-34) — the views call this on every
     * walk mount; a previous mount's payload is never served as fresh. */
    async fetchKinds(campaignId: string) {
      const generation = sessionGeneration()
      const entry = this.ensureEntry(campaignId)
      entry.loading = true
      try {
        entry.kinds = await apiFetch<KindsResponse>('/api/campaigns/kinds')
        entry.error = null
      } catch (err) {
        if (generation !== sessionGeneration() || err instanceof SessionChangedError) return
        entry.error = err instanceof ApiError ? err.message : 'Could not load the kinds registry.'
      } finally {
        entry.loading = false
      }
    },
    /** One Tier-2a consequence verb (AD-26/28): the delta merges onto the
     * session image server-side; a repeated fire is a no-op (204, zero
     * revisions) — the UI treats 204 as success either way. Run-state
     * refetches on success; ApiError propagates to the caller. */
    async fireVerb(campaignId: string, entityId: string, update: Record<string, unknown>) {
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}/session-verb`,
        { method: 'POST', body: JSON.stringify({ update }) },
      )
      await this.fetchTonight(campaignId)
    },
    /** One Tier-2b knowledge toggle (AD-29): absolute target state, one
     * undoable step; a same-value repeat is a no-op. Run-state refetches
     * on success; ApiError propagates. */
    async toggleKnowledge(campaignId: string, entityId: string, field: string, known: boolean) {
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}/knowledge-toggle`,
        { method: 'POST', body: JSON.stringify({ field, known }) },
      )
      await this.fetchTonight(campaignId)
    },
    /** The single coalesced fetch for the two Tonight projections. */
    async fetchTonight(campaignId: string) {
      const generation = sessionGeneration()
      const entry = this.ensureEntry(campaignId)
      if (entry.fetching) {
        entry.dirty = true
        return
      }
      entry.fetching = true
      entry.loading = true
      try {
        const [runState, feed] = await Promise.all([
          apiFetch<RunStateResponse>(`/api/campaigns/${encodeURIComponent(campaignId)}/run-state`),
          apiFetch<RevisionsResponse>(
            `/api/campaigns/${encodeURIComponent(campaignId)}/revisions?limit=20`,
          ),
        ])
        entry.runState = runState
        entry.revisions = feed.revisions
        entry.error = null
      } catch (err) {
        if (generation !== sessionGeneration() || err instanceof SessionChangedError) return
        // The feed and run-state ride one entry: a failure of either
        // surface keeps the other's last-known projection (never blanks
        // on a transient error). A 404/ownership miss is the view's
        // existing not-found flow, not a Tonight error.
        if (!(err instanceof ApiError && err.status === 404)) {
          entry.error = err instanceof ApiError ? err.message : 'Could not load Tonight.'
        }
      } finally {
        entry.fetching = false
        entry.loading = false
        if (entry.dirty && this.byCampaign[campaignId] === entry) {
          entry.dirty = false
          void this.fetchTonight(campaignId)
        }
      }
    },
    /** Ensure the entry exists and return it through the reactive proxy. */
    ensureEntry(campaignId: string): TonightEntry {
      if (!this.byCampaign[campaignId]) {
        this.byCampaign[campaignId] = emptyEntry()
      }
      return this.byCampaign[campaignId]
    },
  },
})
