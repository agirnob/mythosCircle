import { defineStore } from 'pinia'
import type { components } from '../api/schema'
import { ApiError, apiFetch } from '../api/client'
import { sessionGeneration } from '../api/session'
import { useJobsStore } from './jobs'
import { useWorldStore } from './world'

type Snapshot = components['schemas']['WorkspaceResponse']
export type WorkspaceNode = components['schemas']['WorkspaceNode']
type Job = components['schemas']['JobResponse']
interface Entry {
  snapshot: Snapshot | null
  nodes: WorkspaceNode[]
  error: string | null
  loading: boolean
  saving: boolean
  dirty: boolean
  conflict: boolean
  epoch: number
  handledProposalIds: string[]
  ownerId: string | null
  pending: { action: string; body: string } | null
  proposal: {
    jobId?: string
    draft: { nodes: WorkspaceNode[] }
    draftVersion: number
    worldVersion: string | null
  } | null
}
const blank = (): Entry => ({
  snapshot: null,
  nodes: [],
  error: null,
  loading: false,
  saving: false,
  dirty: false,
  conflict: false,
  epoch: 0,
  handledProposalIds: [],
  ownerId: null,
  pending: null,
  proposal: null,
})
const drainingSaves = new WeakMap<Entry, Promise<boolean>>()
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
export function workspaceId(): string {
  const alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
  let timestamp = Date.now(),
    prefix = ''
  for (let i = 0; i < 10; i++) {
    prefix = alphabet[timestamp % 32] + prefix
    timestamp = Math.floor(timestamp / 32)
  }
  return (
    prefix +
    Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) => alphabet[n % 32]).join('')
  )
}
export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({ byCampaign: {} as Record<string, Entry> }),
  actions: {
    entry(id: string): Entry {
      return (this.byCampaign[id] ??= blank())
    },
    async load(id: string, discardLocal = false) {
      const entry = this.entry(id),
        generation = sessionGeneration()
      if (!discardLocal) {
        const saving = drainingSaves.get(entry)
        if (saving) await saving
        if (generation !== sessionGeneration()) return false
      }
      const epoch = ++entry.epoch
      entry.loading = true
      try {
        const snapshot = await apiFetch<Snapshot>(`/api/campaigns/${id}/workspace`)
        if (generation !== sessionGeneration() || epoch !== entry.epoch) return false
        if (
          entry.dirty &&
          !discardLocal &&
          entry.snapshot &&
          (entry.snapshot.draft_version !== snapshot.draft_version ||
            entry.snapshot.world_version !== snapshot.world_version)
        ) {
          entry.conflict = true
          entry.error =
            'The saved workspace changed while you were editing. Your local additions are preserved.'
          return false
        }
        entry.snapshot = snapshot
        if (!entry.dirty || discardLocal) {
          entry.nodes = clone(snapshot.draft.nodes ?? [])
          entry.dirty = false
          entry.conflict = false
        } else {
          // Preserve local edits while promoting only durable completed identities.
          const completed = new Map(
            snapshot.receipts.map((r) => [r.node_id as string, r.entity_id as string]),
          )
          entry.nodes = entry.nodes
            .filter((n) => !completed.has(n.id))
            .map((n) => ({
              ...n,
              parent_id: completed.get(n.parent_id ?? '') ?? n.parent_id,
              faction_id: completed.get(n.faction_id ?? '') ?? n.faction_id,
            }))
        }
        entry.error = null
        return true
      } catch (error) {
        if (generation === sessionGeneration() && epoch === entry.epoch)
          entry.error = error instanceof Error ? error.message : 'Could not load workspace.'
        return false
      } finally {
        entry.loading = false
      }
    },
    edit(id: string) {
      this.entry(id).dirty = true
    },
    async save(id: string): Promise<boolean> {
      const entry = this.entry(id),
        generation = sessionGeneration()
      const pending = drainingSaves.get(entry)
      if (pending) return pending
      if (!entry.snapshot || entry.conflict) return false
      if (!entry.dirty) return true
      entry.saving = true
      let activeEpoch = entry.epoch
      const draining = (async () => {
        try {
          // The store owns this loop so edits survive their view unmounting mid-save.
          while (entry.dirty && entry.snapshot && !entry.conflict) {
            const sent = clone(entry.nodes),
              epoch = ++entry.epoch
            activeEpoch = epoch
            const snapshot = await apiFetch<Snapshot>(`/api/campaigns/${id}/workspace`, {
              method: 'PUT',
              body: JSON.stringify({
                expected_draft_version: entry.snapshot.draft_version,
                expected_world_version: entry.snapshot.world_version,
                draft: { nodes: sent },
              }),
            })
            if (generation !== sessionGeneration() || epoch !== entry.epoch) return false
            entry.snapshot = snapshot
            entry.dirty = JSON.stringify(sent) !== JSON.stringify(entry.nodes)
            entry.error = null
          }
          return !entry.dirty
        } catch (error) {
          if (generation !== sessionGeneration() || activeEpoch !== entry.epoch) return false
          entry.conflict = error instanceof ApiError && error.status === 409
          entry.error = error instanceof Error ? error.message : 'Could not save planned additions.'
          return false
        } finally {
          entry.saving = false
          drainingSaves.delete(entry)
        }
      })()
      drainingSaves.set(entry, draining)
      return draining
    },
    configureOwner(id: string, ownerId: string | null) {
      const entry = this.entry(id)
      if (entry.ownerId === ownerId) return
      entry.ownerId = ownerId
      entry.handledProposalIds = []
      if (!ownerId) return
      try {
        const value: unknown = JSON.parse(
          globalThis.localStorage.getItem(`mythoscircle:workspace-proposals:${ownerId}:${id}`) ??
            '[]',
        )
        if (Array.isArray(value))
          entry.handledProposalIds = value.filter(
            (value): value is string => typeof value === 'string',
          )
      } catch {
        /* Browser preferences are optional; server drafts remain authoritative. */
      }
    },
    dismissProposal(id: string, requestedJobId?: string) {
      const entry = this.entry(id)
      const jobId = requestedJobId ?? entry.proposal?.jobId
      if (jobId && !entry.handledProposalIds.includes(jobId)) entry.handledProposalIds.push(jobId)
      if (!jobId || entry.proposal?.jobId === jobId) entry.proposal = null
      if (!entry.ownerId) return
      try {
        globalThis.localStorage.setItem(
          `mythoscircle:workspace-proposals:${entry.ownerId}:${id}`,
          JSON.stringify(entry.handledProposalIds),
        )
      } catch {
        /* A storage-denied browser still remembers dismissal for this session. */
      }
    },
    async submit(
      id: string,
      action: 'plan' | 'generate',
      guidance = '',
      scopeId: string | null = null,
    ): Promise<Job | null> {
      const entry = this.entry(id),
        generation = sessionGeneration()
      if (!(await this.save(id)) || !entry.snapshot || generation !== sessionGeneration())
        return null
      const request = {
        expected_draft_version: entry.snapshot.draft_version,
        expected_world_version: entry.snapshot.world_version,
        selected_ids: entry.nodes.map((n) => n.id),
        guidance,
        scope_id: scopeId,
      }
      const candidate = JSON.stringify(request)
      if (
        !entry.pending ||
        entry.pending.action !== action ||
        JSON.stringify(JSON.parse(entry.pending.body), (key, value) =>
          key === 'request_id' ? undefined : value,
        ) !== candidate
      )
        entry.pending = { action, body: JSON.stringify({ ...request, request_id: workspaceId() }) }
      try {
        const job = await apiFetch<Job>(`/api/campaigns/${id}/workspace/${action}`, {
          method: 'POST',
          body: entry.pending.body,
        })
        if (generation !== sessionGeneration()) return null
        entry.pending = null
        useJobsStore().byId[job.id] = job
        return job
      } catch (error) {
        if (generation !== sessionGeneration()) return null
        entry.error = error instanceof Error ? error.message : 'Could not submit planned additions.'
        return null
      }
    },
    async reconcile(id: string, job: Job) {
      const generation = sessionGeneration()
      const entry = this.entry(id)
      if (job.campaign_id !== id || entry.saving) return false
      if (job.kind === 'plan_expansion' && job.state === 'succeeded' && job.result?.proposal) {
        const newest = useJobsStore()
          .forCampaign(id)
          .find((candidate) => candidate.kind === 'plan_expansion')
        if (entry.handledProposalIds.includes(job.id) || (newest && newest.id !== job.id))
          return true
        const result = job.result
        this.entry(id).proposal = {
          jobId: job.id,
          draft: result.proposal as { nodes: WorkspaceNode[] },
          draftVersion: result.source_draft_version as number,
          worldVersion: result.source_world_version as string | null,
        }
      }
      if (job.kind === 'expand_world') {
        // Active generation disables editing; the server checkpoint is authoritative.
        if (!entry.dirty) {
          const loaded = await this.load(id)
          if (!loaded || generation !== sessionGeneration()) return false
          await useWorldStore().load(id)
          return !useWorldStore().entry(id).error
        } else {
          const epoch = ++entry.epoch
          const snapshot = await apiFetch<Snapshot>(`/api/campaigns/${id}/workspace`)
          if (generation !== sessionGeneration() || epoch !== entry.epoch || entry.saving)
            return false
          const completed = new Map(
            snapshot.receipts.map((r) => [r.node_id as string, r.entity_id as string]),
          )
          entry.nodes = entry.nodes
            .filter((n) => !completed.has(n.id))
            .map((n) => ({
              ...n,
              parent_id: completed.get(n.parent_id ?? '') ?? n.parent_id,
              faction_id: completed.get(n.faction_id ?? '') ?? n.faction_id,
            }))
          if (
            entry.snapshot &&
            (entry.snapshot.draft_version !== snapshot.draft_version ||
              entry.snapshot.world_version !== snapshot.world_version)
          ) {
            entry.snapshot = { ...entry.snapshot, receipts: snapshot.receipts }
            entry.conflict = true
            entry.error =
              'The saved workspace changed while you were editing. Your local additions are preserved.'
          } else entry.snapshot = snapshot
          await useWorldStore().load(id)
          return !useWorldStore().entry(id).error
        }
      }
      return true
    },
    async applyProposal(id: string) {
      const entry = this.entry(id),
        proposal = entry.proposal
      if (!proposal || !entry.snapshot) return
      if (
        entry.dirty ||
        entry.snapshot.draft_version !== proposal.draftVersion ||
        entry.snapshot.world_version !== proposal.worldVersion
      ) {
        entry.error = 'This suggestion is stale. Request a new suggestion for the current draft.'
        return
      }
      entry.nodes = clone(proposal.draft.nodes)
      entry.dirty = true
      if (await this.save(id)) this.dismissProposal(id, proposal.jobId)
    },
  },
})
