import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { apiFetch } from '../api/client'
import type { WsMessage } from '../ws'

type Job = components['schemas']['JobResponse']

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
    latestBuildIn: (state) => (campaignId: string) =>
      Object.values(state.byId)
        .filter((job) => job.campaign_id === campaignId && job.kind === 'build_in')
        .sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null,
  },
  actions: {
    upsert(job: Job) {
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
    /** Full re-sync after reconnect or a queue_changed (positions shift). */
    async syncList(campaignId: string) {
      const response = await apiFetch<components['schemas']['JobListResponse']>(
        `/api/jobs?campaign_id=${encodeURIComponent(campaignId)}`,
      )
      for (const job of response.jobs) {
        this.upsert(job)
      }
    },
    /**
     * AD-17 dispatch: WS frames carry only the delta ({type, job_id, state,
     * queue_position?, progress?}), never the full row — patch the cached row
     * in place, never clobber REST-fetched fields. queue_changed and terminal
     * frames re-sync the list (positions shift).
     */
    async handleWsMessage(campaignId: string, message: WsMessage) {
      const cached = this.byId[message.job_id]
      if (cached) {
        cached.state = message.state
        if (message.progress !== undefined && message.progress !== null) {
          cached.progress = message.progress
        }
        if (message.queue_position !== undefined) {
          cached.queue_position = message.queue_position
        }
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
