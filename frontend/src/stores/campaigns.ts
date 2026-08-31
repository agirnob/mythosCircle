import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { ApiError, apiFetch } from '../api/client'

type Campaign = components['schemas']['CampaignResponse']

export const useCampaignsStore = defineStore('campaigns', {
  state: () => ({
    campaigns: [] as Campaign[],
    current: null as Campaign | null,
    loading: false,
    error: null as string | null,
  }),
  actions: {
    async list() {
      this.error = null
      this.loading = true
      try {
        const response =
          await apiFetch<components['schemas']['CampaignListResponse']>('/api/campaigns')
        this.campaigns = response.campaigns
      } catch (err) {
        this.error = err instanceof ApiError ? err.message : 'Could not load campaigns.'
      } finally {
        this.loading = false
      }
    },
    async fetchOne(campaignId: string) {
      this.error = null
      try {
        this.current = await apiFetch<Campaign>(`/api/campaigns/${campaignId}`)
      } catch (err) {
        this.error = err instanceof ApiError ? err.message : 'Could not load the campaign.'
      }
    },
  },
})
