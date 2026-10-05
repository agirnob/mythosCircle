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
import type { PlaySession, JournalEntry, JournalDraft, JournalReference } from '../api/journal'

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
  sessions: PlaySession[]
  activeSessionId: string | null
  journal: Record<string, { entries: JournalEntry[]; nextCursor: string | null }>
  journalVersion: Record<string, number>
  journalError: string | null
  sessionsError: string | null
  changesError: string | null
  changesLoading: boolean
  revisionsCursor: string | null
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
    sessions: [],
    activeSessionId: null,
    journal: {},
    journalVersion: {},
    journalError: null,
    sessionsError: null,
    changesError: null,
    changesLoading: false,
    revisionsCursor: null,
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
      await Promise.all([this.fetchTonight(campaignId), this.fetchSessions(campaignId)])
    },
    /** Per-mount registry fetch (AD-34) — the views call this on every
     * walk mount; a previous mount's payload is never served as fresh. */
    async fetchKinds(campaignId: string) {
      const generation = sessionGeneration()
      const entry = this.ensureEntry(campaignId)
      entry.loading = true
      try {
        const kinds = await apiFetch<KindsResponse>('/api/campaigns/kinds')
        if (generation !== sessionGeneration() || this.byCampaign[campaignId] !== entry) return
        entry.kinds = kinds
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
    async fireVerb(
      campaignId: string,
      entityId: string,
      update: Record<string, unknown>,
      journal?: {
        session_id: string
        headline?: string
        context?: string
        references?: JournalReference[]
        request_key: string
      },
    ) {
      const generation = sessionGeneration()
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}/session-verb`,
        { method: 'POST', body: JSON.stringify({ update, ...journal }) },
      )
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      // Refresh is best effort after the authoritative mutation succeeded.
      await this.fetchTonight(campaignId)
      if (journal) await this.fetchJournal(campaignId, { sessionId: journal.session_id })
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
        entry.revisionsCursor = feed.next_cursor ?? null
        entry.changesError = null
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
    async fetchSessions(campaignId: string) {
      const generation = sessionGeneration()
      const entry = this.ensureEntry(campaignId)
      const version = (entry.journalVersion.sessions ?? 0) + 1
      entry.journalVersion.sessions = version
      const current = () =>
        generation === sessionGeneration() &&
        this.byCampaign[campaignId] === entry &&
        entry.journalVersion.sessions === version
      try {
        let cursor: string | null = null
        const sessions: PlaySession[] = []
        let active: string | null = null
        do {
          const page: {
            sessions: PlaySession[]
            active_session_id: string | null
            next_cursor: string | null
          } = await apiFetch(
            `/api/campaigns/${encodeURIComponent(campaignId)}/play-sessions?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
          )
          sessions.push(...page.sessions)
          active = page.active_session_id
          cursor = page.next_cursor
        } while (cursor && current())
        if (!current()) return
        entry.sessions = sessions
        entry.activeSessionId = active
        entry.sessionsError = null
      } catch (error) {
        if (!current() || error instanceof SessionChangedError) return
        entry.sessionsError =
          error instanceof Error ? error.message : 'Could not load play sessions.'
      }
    },
    async createSession(campaignId: string, title: string, playDate: string, requestKey: string) {
      const generation = sessionGeneration()
      const result = await apiFetch<PlaySession>(
        `/api/campaigns/${encodeURIComponent(campaignId)}/play-sessions`,
        {
          method: 'POST',
          body: JSON.stringify({ title, play_date: playDate, request_key: requestKey }),
        },
      )
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      const entry = this.ensureEntry(campaignId)
      entry.journalVersion.sessions = (entry.journalVersion.sessions ?? 0) + 1
      if (!entry.sessions.some((session) => session.id === result.id)) entry.sessions.push(result)
      await this.fetchSessions(campaignId)
      return result
    },
    async activateSession(campaignId: string, sessionId: string) {
      const generation = sessionGeneration()
      const entry = this.ensureEntry(campaignId)
      const version = (entry.journalVersion.sessions ?? 0) + 1
      entry.journalVersion.sessions = version
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/play-sessions/${encodeURIComponent(sessionId)}/activate`,
        { method: 'POST' },
      )
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      if (this.byCampaign[campaignId] !== entry || entry.journalVersion.sessions !== version) return
      entry.activeSessionId = sessionId
    },
    async updateSession(campaignId: string, session: PlaySession, title: string, playDate: string) {
      const generation = sessionGeneration()
      const result = await apiFetch<PlaySession>(
        `/api/campaigns/${encodeURIComponent(campaignId)}/play-sessions/${session.id}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ version: session.version, title, play_date: playDate }),
        },
      )
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      const entry = this.ensureEntry(campaignId)
      entry.journalVersion.sessions = (entry.journalVersion.sessions ?? 0) + 1
      entry.sessions = entry.sessions.map((row) => (row.id === result.id ? result : row))
      return result
    },
    journalKey(filter: { sessionId?: string; entityId?: string } = {}) {
      return JSON.stringify([filter.sessionId ?? null, filter.entityId ?? null])
    },
    async fetchJournal(
      campaignId: string,
      filter: { sessionId?: string; entityId?: string } = {},
      more = false,
      targetId?: string,
    ) {
      const generation = sessionGeneration()
      const entry = this.ensureEntry(campaignId)
      const key = this.journalKey(filter)
      const version = (entry.journalVersion[key] ?? 0) + 1
      entry.journalVersion[key] = version
      const current = () =>
        generation === sessionGeneration() &&
        this.byCampaign[campaignId] === entry &&
        entry.journalVersion[key] === version
      let cursor = more ? (entry.journal[key]?.nextCursor ?? null) : null
      if (more && !cursor) return
      // A refresh keeps the extent already read. A saved/deep-linked target
      // extends that bounded page walk instead of vanishing beyond page one.
      const extent = more ? 0 : Math.max(100, entry.journal[key]?.entries.length ?? 0)
      let rows: JournalEntry[] = more ? [...(entry.journal[key]?.entries ?? [])] : []
      let fetched = 0
      const visited = new Set<string>()
      try {
        do {
          if (cursor) {
            if (visited.has(cursor))
              throw new Error('Journal pagination did not advance. Retry loading the journal.')
            visited.add(cursor)
          }
          const query = new URLSearchParams({ limit: '100' })
          if (filter.sessionId) query.set('session_id', filter.sessionId)
          if (filter.entityId) query.set('entity_id', filter.entityId)
          if (cursor) query.set('cursor', cursor)
          const page = await apiFetch<{ entries: JournalEntry[]; next_cursor: string | null }>(
            `/api/campaigns/${encodeURIComponent(campaignId)}/journal-entries?${query}`,
          )
          if (!current()) return
          rows.push(...page.entries)
          fetched += page.entries.length
          cursor = page.next_cursor
        } while (
          cursor &&
          ((!more && fetched < extent) || (targetId && !rows.some((row) => row.id === targetId)))
        )
        if (!current()) return
        rows = [...new Map(rows.map((row) => [row.id, row])).values()]
        entry.journal[key] = { entries: rows, nextCursor: cursor }
        entry.journalError = null
        return true
      } catch (error) {
        if (!current() || error instanceof SessionChangedError) return
        entry.journalError = error instanceof Error ? error.message : 'Could not load the journal.'
        return false
      }
    },
    applyJournalEntry(campaignId: string, result: JournalEntry) {
      const entry = this.ensureEntry(campaignId)
      // Invalidate older reads before applying the confirmed response.
      for (const key of Object.keys(entry.journalVersion)) {
        if (key !== 'sessions') entry.journalVersion[key] = (entry.journalVersion[key] ?? 0) + 1
      }
      for (const [key, page] of Object.entries(entry.journal)) {
        const [sessionId, entityId] = JSON.parse(key) as [string | null, string | null]
        page.entries = page.entries.filter((row) => row.id !== result.id)
        if (
          (!sessionId || sessionId === result.session_id) &&
          (!entityId ||
            result.action_entity_id === entityId ||
            result.references.some((reference) => reference.entity_id === entityId))
        )
          page.entries.push(result)
      }
    },
    async saveJournal(
      campaignId: string,
      sessionId: string,
      draft: JournalDraft,
      requestKey: string,
      existing?: JournalEntry,
      extra: { position?: number; source_event_id?: string } = {},
    ) {
      const generation = sessionGeneration()
      const result = await apiFetch<JournalEntry>(
        `/api/campaigns/${encodeURIComponent(campaignId)}/journal-entries${existing ? `/${existing.id}` : ''}`,
        {
          method: existing ? 'PATCH' : 'POST',
          body: JSON.stringify({
            ...draft,
            ...extra,
            ...(existing
              ? { version: existing.version }
              : { session_id: sessionId, request_key: requestKey }),
          }),
        },
      )
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      this.applyJournalEntry(campaignId, result)
      void this.fetchTonight(campaignId)
      return result
    },
    async correctJournal(campaignId: string, row: JournalEntry) {
      const generation = sessionGeneration()
      const result = await apiFetch<JournalEntry>(
        `/api/campaigns/${encodeURIComponent(campaignId)}/journal-entries/${row.id}/correct`,
        { method: 'POST', body: JSON.stringify({ version: row.version }) },
      )
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      this.applyJournalEntry(campaignId, result)
      void this.fetchTonight(campaignId)
      return result
    },
    async removeJournal(campaignId: string, row: JournalEntry) {
      const generation = sessionGeneration()
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/journal-entries/${row.id}?version=${row.version}`,
        { method: 'DELETE' },
      )
      if (generation !== sessionGeneration()) throw new SessionChangedError()
      const entry = this.ensureEntry(campaignId)
      for (const key of Object.keys(entry.journalVersion)) {
        if (key !== 'sessions') entry.journalVersion[key] = (entry.journalVersion[key] ?? 0) + 1
      }
      for (const page of Object.values(entry.journal))
        page.entries = page.entries.filter((item) => item.id !== row.id)
      void this.fetchTonight(campaignId)
    },
    async moreChanges(campaignId: string) {
      const generation = sessionGeneration()
      const entry = this.ensureEntry(campaignId)
      if (!entry.revisionsCursor || entry.changesLoading) return
      const version = entry.projectionVersion
      const current = () =>
        generation === sessionGeneration() &&
        this.byCampaign[campaignId] === entry &&
        entry.projectionVersion === version
      entry.changesLoading = true
      entry.changesError = null
      try {
        const page = await apiFetch<RevisionsResponse>(
          `/api/campaigns/${encodeURIComponent(campaignId)}/revisions?limit=20&cursor=${encodeURIComponent(entry.revisionsCursor)}`,
        )
        if (!current()) return
        entry.revisions = [
          ...new Map(
            [...(entry.revisions ?? []), ...page.revisions].map((row) => [row.revision_id, row]),
          ).values(),
        ]
        entry.revisionsCursor = page.next_cursor ?? null
      } catch (error) {
        if (current() && !(error instanceof SessionChangedError))
          entry.changesError =
            error instanceof Error ? error.message : 'Could not load older changes. Retry.'
      } finally {
        if (this.byCampaign[campaignId] === entry) entry.changesLoading = false
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
