/**
 * Proposed-candidate accept-screen store (spec-3-3).
 *
 * Holds the staged candidate rows (`GET /api/campaigns/{id}/candidates`,
 * default status `proposed`) per campaign plus the accept/reject
 * lifecycle actions. Settled rows returned by accept/reject replace
 * their cached entry (status flip) so the proposed getter drops them;
 * a full re-sync (a snapshot, with pruning of server-discarded
 * proposed rows) covers anything else.
 *
 * The ask box enqueues a `generate` job through the shared jobs queue;
 * the jobs WebSocket signals completion and the view re-syncs this
 * store — new candidates appear without navigation (spec-3-3 Design
 * Notes).
 */
import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { apiFetch } from '../api/client'

type Candidate = components['schemas']['CandidateResponse']
type JobResponse = components['schemas']['JobResponse']

export const useCandidatesStore = defineStore('candidates', {
  state: () => ({
    byId: {} as Record<string, Candidate>,
  }),
  getters: {
    /**
     * Proposed candidates for a campaign, oldest first. Ordered by id —
     * ULIDs are time-ordered, so id order matches the wire's rowid
     * order even inside a same-timestamp staging batch.
     */
    proposed: (state) => (campaignId: string) =>
      Object.values(state.byId)
        .filter(
          (candidate) => candidate.campaign_id === campaignId && candidate.status === 'proposed',
        )
        .sort((a, b) => a.id.localeCompare(b.id)),
  },
  actions: {
    upsert(candidate: Candidate) {
      this.byId[candidate.id] = candidate
    },
    /**
     * Full re-sync (mount, reconnect, or a generate job settling). The
     * sync is a snapshot, not a merge: after the pages are read, cached
     * rows of this campaign that no page returned AND are still
     * `proposed` are dropped — rows the server discarded (job failed or
     * cancelled) must not linger as ghost cards. Settled rows are kept
     * (audit-trail facts; they are never re-fetched here).
     */
    async syncList(campaignId: string) {
      const seen = new Set<string>()
      let cursor: string | null = null
      let query: string
      do {
        query = cursor === null ? '' : `&cursor=${encodeURIComponent(cursor)}`
        const response = await apiFetch<components['schemas']['CandidateListResponse']>(
          `/api/campaigns/${encodeURIComponent(campaignId)}/candidates?limit=100${query}`,
        )
        for (const candidate of response.candidates) {
          seen.add(candidate.id)
          this.upsert(candidate)
        }
        cursor = response.next_cursor
      } while (cursor !== null)
      for (const id of Object.keys(this.byId)) {
        const cached = this.byId[id]
        if (cached.campaign_id === campaignId && cached.status === 'proposed' && !seen.has(id)) {
          delete this.byId[id]
        }
      }
    },
    /** Enqueue a generate job from the accept screen's ask box. */
    async submitAsk(campaignId: string, ask: string): Promise<JobResponse> {
      return apiFetch<JobResponse>('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({ campaign_id: campaignId, kind: 'generate', payload: { ask } }),
      })
    },
    /**
     * Accept, optionally with the edit-before-accept payload override
     * (spec-3-3, relaxed by spec-3-4): the override is the full
     * candidate record with the DM's section edits AND the DM's own
     * edge set (added/edited/deleted staged edges). The store validates
     * every override edge against committed world state AND the
     * required AR24 section shape — any violation rejects with 4xx and
     * the row stays proposed. No body = accept unedited.
     */
    async accept(
      campaignId: string,
      candidateId: string,
      payload?: Record<string, unknown>,
    ): Promise<Candidate> {
      const candidate = await apiFetch<Candidate>(
        `/api/campaigns/${encodeURIComponent(campaignId)}/candidates/${encodeURIComponent(candidateId)}/accept`,
        payload === undefined
          ? { method: 'POST' }
          : { method: 'POST', body: JSON.stringify({ payload }) },
      )
      this.byId[candidate.id] = candidate
      return candidate
    },
    /** Reject — the world is untouched (AR7). */
    async reject(campaignId: string, candidateId: string): Promise<Candidate> {
      const candidate = await apiFetch<Candidate>(
        `/api/campaigns/${encodeURIComponent(campaignId)}/candidates/${encodeURIComponent(candidateId)}/reject`,
        { method: 'POST' },
      )
      this.byId[candidate.id] = candidate
      return candidate
    },
  },
})
