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
  projectionVersion?: number
  appliedProjectionVersion?: number
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
    /** Notes POST is authoritative; projection refresh failure cannot undo a save. */
    async saveNotes(campaignId: string, entityId: string, notes: string, expectedNotes: string) {
      const generation = sessionGeneration()
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}/session-verb`,
        {
          method: 'POST',
          body: JSON.stringify({ update: { notes }, expected_notes: expectedNotes }),
        },
      )
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      const entry = this.ensureEntry(campaignId)
      const minimumVersion = (entry.projectionVersion ?? 0) + 1
      const result = await this.refreshProjections(campaignId)
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      if (!result.refreshed) {
        if (
          this.byCampaign[campaignId] === entry &&
          entry.runState &&
          (entry.appliedProjectionVersion ?? 0) >= minimumVersion
        ) {
          const latest = entry.runState.session[entityId]?.notes
          return { refreshed: true, savedText: typeof latest === 'string' ? latest : '' }
        }
        // A failed save refresh may have invalidated the initial read. Ensure
        // a trailing read can populate it or report a usable load error.
        if (entry.fetching || !entry.runState) void this.fetchTonight(campaignId)
        return { refreshed: false }
      }
      const value = result.runState.session[entityId]?.notes
      return { refreshed: true, savedText: typeof value === 'string' ? value : '' }
    },
    /** Every actual projection read shares one sequence, including save refreshes. */
    async refreshProjections(campaignId: string) {
      const generation = sessionGeneration()
      const entry = this.ensureEntry(campaignId)
      const version = (entry.projectionVersion ?? 0) + 1
      entry.projectionVersion = version
      const current = () =>
        generation === sessionGeneration() &&
        this.byCampaign[campaignId] === entry &&
        entry.projectionVersion === version
      try {
        const [runState, feed] = await Promise.all([
          apiFetch<RunStateResponse>(`/api/campaigns/${encodeURIComponent(campaignId)}/run-state`),
          apiFetch<RevisionsResponse>(
            `/api/campaigns/${encodeURIComponent(campaignId)}/revisions?limit=20`,
          ),
        ])
        if (!current()) return { refreshed: false as const }
        entry.runState = runState
        entry.appliedProjectionVersion = version
        entry.revisions = feed.revisions
        entry.error = null
        return { refreshed: true as const, runState }
      } catch (err) {
        if (generation !== sessionGeneration() || err instanceof SessionChangedError)
          throw new SessionChangedError()
        if (current() && !(err instanceof ApiError && err.status === 404)) {
          entry.error = err instanceof ApiError ? err.message : 'Could not load Tonight.'
        }
        return { refreshed: false as const }
      }
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
        await this.refreshProjections(campaignId)
      } catch (err) {
        if (generation !== sessionGeneration() || err instanceof SessionChangedError) return
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
