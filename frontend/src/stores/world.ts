/**
 * World view store (spec-2-7; write surface added in spec-3-4).
 *
 * Holds the export-JSON projection (`GET /api/campaigns/{id}/export`) per
 * campaign plus loading/error/not-found flags. Rendering decisions —
 * grouping, counter labels, stat-block sections — live in the view and
 * components. Story 3.4 adds the DM's relation-editing writes: every
 * mutation goes through the edges REST surface (the store commit path on
 * the backend) and lands back here as a coalesced snapshot refetch —
 * the store still never mutates world state locally.
 *
 * Live updates (FR1, NFR9): WS frames only signal "the world changed", so
 * `handleJobMessage` re-fetches the snapshot — on a build-in
 * `job_progress` at/after the wave-1 commit (progress 0.5), on any
 * terminal build-in frame (a mid-wave-2 failure still leaves wave 1
 * committed), and on WS reconnect. Adjacent qualifying frames (the 0.5
 * wave-1 commit, then the 1.0 terminal frame) each trigger a fetch;
 * fetches coalesce — a frame landing while a fetch is in flight marks
 * the entry dirty and one trailing fetch covers the delta, so parallel
 * fetches never stack.
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

type MediaRow = components['schemas']['MediaResponse']

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
    /** The campaign's media manifest (spec-4.1) — fetched separately from
     * the snapshot: media is NOT woven into WorldExport (the export stays
     * media-free; story 4.3 owns export validation). */
    mediaByCampaign: {} as Record<string, MediaRow[]>,
    /** True when the campaign's media-list fetch last failed — the view
     * renders 'Portrait list unavailable' instead of 'No portrait.' so a
     * transient failure never reads as absence (review round). */
    mediaErrorByCampaign: {} as Record<string, boolean>,
  }),
  getters: {
    entry:
      (state) =>
      (campaignId: string): WorldEntry =>
        state.byCampaign[campaignId] ?? emptyEntry(),
    /** The campaign's manifest rows, insertion order (the wire order). */
    mediaFor:
      (state) =>
      (campaignId: string): MediaRow[] =>
        state.mediaByCampaign[campaignId] ?? [],
    /** True iff the last media-list fetch for the campaign failed. */
    mediaFetchFailed:
      (state) =>
      (campaignId: string): boolean =>
        state.mediaErrorByCampaign[campaignId] ?? false,
  },
  actions: {
    /** Initial mount load — same coalescing fetch as refetches. */
    async load(campaignId: string) {
      await this.fetchSnapshot(campaignId)
    },
    /**
     * Spec-4.1: the campaign's media manifest. Fired on mount and on an
     * image job's terminal frame (job_done/failed) — the portrait lands
     * with no manual refresh. A failure IS tracked: the 'Portrait list
     * unavailable' flag renders in place of 'No portrait.' (a transient
     * media-list failure must not read as absence), and the next image
     * frame or remount refetches.
     */
    async fetchMedia(campaignId: string) {
      try {
        const response = await apiFetch<components['schemas']['MediaListResponse']>(
          `/api/campaigns/${encodeURIComponent(campaignId)}/media`,
        )
        this.mediaByCampaign[campaignId] = response.media
        this.mediaErrorByCampaign[campaignId] = false
      } catch {
        this.mediaErrorByCampaign[campaignId] = true
      }
    },
    /** The portrait row for an entity, or null — the newest AVAILABLE
     * image by manifest rowid (spec-4.3 / epic-4 retro item 13): the
     * export_sheets._hero_portrait_src rule, mirrored exactly. The
     * manifest is explicitly rowid (insertion) order, so the LAST
     * image row that is not flagged missing on this entity's export is
     * the newest available by rowid — the old created_at sort is gone
     * (a broken newest row must not shadow an older good one, and a
     * same-second regen resolves by rowid, not by string compare).
     * Availability lives on the entity export's media refs (the
     * manifest itself does not carry it); kind === 'image' only — a
     * 4.2 video row must never displace the portrait as the <img> src. */
    portraitFor(campaignId: string, entityId: string): MediaRow | null {
      const entry = this.byCampaign[campaignId]
      const entity = entry?.world?.entities.find((candidate) => candidate.id === entityId)
      // A missing file is flagged per ref; any image ref NOT in the
      // missing set is available. (Refs and manifest come from the same
      // media rows; with no refs — pre-4.3 worlds — every row counts.)
      const missing = new Set(
        (entity?.media ?? [])
          .filter((ref) => ref.kind === 'image' && !ref.available)
          .map((ref) => ref.id),
      )
      const rows = this.mediaFor(campaignId).filter(
        (row) =>
          row.entity_id === entityId && row.kind === 'image' && !missing.has(row.id),
      )
      return rows.length > 0 ? rows[rows.length - 1]! : null
    },
    /**
     * The same-origin portrait file URL for an entity, or null — the ONE
     * source of the media URL convention (story 7.1 review): the manifest
     * row's entity_id + filename. The session cookie (path /api)
     * authenticates the request; this is a resource URL, never an apiFetch.
     */
    portraitSrc(campaignId: string, entityId: string): string | null {
      const row = this.portraitFor(campaignId, entityId)
      return row
        ? `/api/campaigns/${encodeURIComponent(campaignId)}/media/${encodeURIComponent(row.entity_id)}/${row.filename}`
        : null
    },
    /** The LATEST reveal-video row for an entity, or null (newest
     * created_at, ``kind === 'video'`` only) — the boss-tier card's
     * <video> source; a separate projection from ``portraitFor`` so a
     * video row never displaces the portrait <img>. */
    videoFor(campaignId: string, entityId: string): MediaRow | null {
      return (
        this.mediaFor(campaignId)
          .filter((row) => row.entity_id === entityId && row.kind === 'video')
          .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null
      )
    },
    /** Fire-and-forget re-sync (WS frame / reconnect); coalesced. */
    requestRefetch(campaignId: string) {
      void this.fetchSnapshot(campaignId)
    },
    /**
     * Relation editing (spec-3-4, FR9): add / edit-counter / delete a
     * typed edge through the edges REST surface. Each call commits
     * exactly one backend revision, then lands here as a coalesced
     * snapshot refetch — the store never mutates world state locally.
     * ApiError propagates to the caller (the view renders it inline).
     */
    async addEdge(
      campaignId: string,
      edge: { src: string; dst: string; type: string; counter: number },
    ): Promise<void> {
      await apiFetch(`/api/campaigns/${encodeURIComponent(campaignId)}/edges`, {
        method: 'POST',
        body: JSON.stringify(edge),
      })
      await this.fetchSnapshot(campaignId)
    },
    async updateEdgeCounter(campaignId: string, edgeId: string, counter: number): Promise<void> {
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/edges/${encodeURIComponent(edgeId)}`,
        { method: 'PATCH', body: JSON.stringify({ counter }) },
      )
      await this.fetchSnapshot(campaignId)
    },
    async deleteEdge(campaignId: string, edgeId: string): Promise<void> {
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/edges/${encodeURIComponent(edgeId)}`,
        { method: 'DELETE' },
      )
      await this.fetchSnapshot(campaignId)
    },
    /**
     * Undo (AD-2): one compensating commit — POST /undo, 204 with no
     * body. ``revisionId`` is the head the view is showing; the store's
     * undo only inverts the LATEST revision, so a head that moved since
     * the render is a 409 and nothing changes. An absent id falls back
     * to the route's own "undo the latest" default. The undo appends its
     * own revision, so the snapshot refetches — the log is never
     * rewritten and the store never mutates world state locally.
     */
    async undoLastCommit(campaignId: string, revisionId: string | null | undefined): Promise<void> {
      const init: RequestInit = { method: 'POST' }
      // Omit the body entirely when the head is unknown: the route reads
      // an absent body as "the latest revision" and only a present body
      // must parse as a JSON object.
      if (revisionId) init.body = JSON.stringify({ revision_id: revisionId })
      await apiFetch(`/api/campaigns/${encodeURIComponent(campaignId)}/undo`, init)
      await this.fetchSnapshot(campaignId)
    },
    /**
     * Entity deletion (FR4/AD-5): one revision, ``baseRevision`` pinned
     * to the snapshot the card rendered (the same optimistic-concurrency
     * idiom as updateEntity). ``cascade`` removes every edge touching the
     * entity in the same revision — it is the destructive option, so it
     * is only sent once the DM has confirmed it (the route refuses a bare
     * cascade body without ``confirm``). The snapshot refetches; neighbors
     * survive (AD-23).
     */
    async deleteEntity(
      campaignId: string,
      entityId: string,
      baseRevision: string | null | undefined,
      cascade: boolean,
    ): Promise<void> {
      const body: Record<string, unknown> = { confirm: true }
      if (cascade) body.cascade = true
      if (baseRevision) body.base_revision = baseRevision
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}`,
        { method: 'DELETE', body: JSON.stringify(body) },
      )
      await this.fetchSnapshot(campaignId)
    },
    /**
     * One media row (spec-4.3, AD-10): the DELETE drops the manifest row
     * (and the file, post-commit), and media is NOT world graph — no
     * event, no revision, and undo never restores it (regeneration is
     * the recovery). The manifest refetches; the snapshot is untouched.
     */
    async deleteMedia(campaignId: string, entityId: string, mediaId: string): Promise<void> {
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}/media/${encodeURIComponent(mediaId)}`,
        { method: 'DELETE' },
      )
      await this.fetchMedia(campaignId)
    },
    /**
     * Hand editing (spec-3-6, FR10): PATCH partial AR24 fields (or
     * ``text``) of a committed entity through the store commit path.
     * ``base_revision`` is the snapshot the editor was opened against —
     * optimistic concurrency (409 StaleRevisionError / rebase-or-reject
     * if the head moved). ApiError propagates to the caller (the view
     * renders 409 inline); on success the snapshot refetches.
     */
    async updateEntity(
      campaignId: string,
      entityId: string,
      patch: Record<string, unknown>,
      baseRevision: string | null | undefined,
    ): Promise<void> {
      const body: Record<string, unknown> = { ...patch }
      if (baseRevision) body.base_revision = baseRevision
      await apiFetch(
        `/api/campaigns/${encodeURIComponent(campaignId)}/entities/${encodeURIComponent(entityId)}`,
        { method: 'PATCH', body: JSON.stringify(body) },
      )
      await this.fetchSnapshot(campaignId)
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
      try {
        const world = await apiFetch<WorldExport>(
          `/api/campaigns/${encodeURIComponent(campaignId)}/export`,
        )
        entry.world = world
        entry.notFound = false
        // Clear only on success — an in-flight refetch must not blank
        // the sync-failed banner before the outcome is known.
        entry.error = null
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          // Foreign or unknown campaign — the indistinguishable 404; a
          // previous snapshot must not linger in the not-found view.
          entry.world = null
          entry.notFound = true
          entry.error = null
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
     * Resolves once no snapshot fetch is in flight or pending (the
     * coalesced trailing fetch included). Callers that must act on the
     * SETTLED outcome of their own refetch (the WorldView 409 rebase)
     * await this: fetchSnapshot's early return resolves before any data
     * lands, so a decision made right after it would read the previous
     * fetch's error.
     */
    async waitUntilQuiet(campaignId: string): Promise<void> {
      const deadline = Date.now() + 5000
      while (Date.now() < deadline) {
        const entry = this.byCampaign[campaignId]
        if (!entry || (!entry.fetching && !entry.dirty)) return
        // Promise.withResolvers needs lib es2024 (tsconfig target is
        // older) — executor form is the only type-safe delay here.
        await new Promise((resolve) => setTimeout(resolve, 25))
      }
    },
    /**
     * WS dispatch for the world view. Mirrors the jobs-store pattern:
     * let the jobs store absorb the frame first (it also REST-recovers an
     * uncached job id), then decide by the cached job's kind — the wire
     * frame itself carries no kind. Refetch on a build-in `job_progress`
     * at/after 0.5 (wave 1 committed) and on any terminal build-in frame;
     * Sub-threshold progress, queue_changed, and non-build_in kinds are
     * ignored — except a TERMINAL frame for an UNRESOLVED kind (the job
     * never reached the cache — e.g. REST recovery failed) still
     * refetches: `job_done` is the last frame a build-in emits, and the
     * fetch is read-only with coalescing already preventing stacking.
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
        if (job.campaign_id !== campaignId) return
        if (job.kind === 'build_in') {
          // Build-in: refetch on a wave-1 commit progress frame and on
          // any terminal frame (wave 1 may be committed).
          const qualifies =
            (message.type === 'job_progress' && (message.progress ?? 0) >= WAVE1_PROGRESS) ||
            terminal
          if (qualifies) void this.fetchSnapshot(campaignId)
          return
        }
        if (job.kind === 'image' || job.kind === 'video') {
          // Spec-4.1 portrait / spec-4.2 reveal video: a terminal media
          // frame means the file + manifest row landed (or the job
          // failed) — re-fetch the media list so the card renders with
          // no manual refresh.
          if (terminal) void this.fetchMedia(campaignId)
          return
        }
        return
      }
      if (!terminal) return
      // Terminal frame for an UNRESOLVED kind (the job never reached the
      // cache — e.g. REST recovery failed): refetch the snapshot and the
      // media list; `job_done` is the last frame an image/build-in emits.
      void this.fetchSnapshot(campaignId)
      void this.fetchMedia(campaignId)
    },
  },
})
