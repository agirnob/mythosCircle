import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { apiFetch } from '../api/client'

type Campaign = components['schemas']['CampaignResponse']

export const useCampaignsStore = defineStore('campaigns', {
  state: () => ({
    campaigns: [] as Campaign[],
    current: null as Campaign | null,
    loading: false,
  }),
  actions: {
    async list() {
      this.loading = true
      try {
        const response =
          await apiFetch<components['schemas']['CampaignListResponse']>('/api/campaigns')
        this.campaigns = response.campaigns
      } finally {
        this.loading = false
      }
    },
    async fetchOne(campaignId: string) {
      this.current = await apiFetch<Campaign>(`/api/campaigns/${campaignId}`)
    },
  },
})
