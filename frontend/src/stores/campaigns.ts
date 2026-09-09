import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { ApiError, apiFetch } from '../api/client'

type Campaign = components['schemas']['CampaignResponse']

export const useCampaignsStore = defineStore('campaigns', {
  state: () => ({
    campaigns: [] as Campaign[],
    current: null as Campaign | null,
    /** The AR27 theme seed list (GET /api/campaigns/themes) — the create
     * form picks from it; free text is a 422 server-side. */
    themes: [] as string[],
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
    /** Create a campaign (POST /api/campaigns, story 1.6 contract). Rethrows so the form can show the ApiError. */
    async create(payload: components['schemas']['CampaignCreate']) {
      this.error = null
      const campaign = await apiFetch<Campaign>('/api/campaigns', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      this.campaigns.unshift(campaign)
      return campaign
    },
    async fetchOne(campaignId: string) {
      this.error = null
      try {
        this.current = await apiFetch<Campaign>(`/api/campaigns/${campaignId}`)
      } catch (err) {
        this.error = err instanceof ApiError ? err.message : 'Could not load the campaign.'
      }
    },
    async fetchThemes() {
      const response =
        await apiFetch<components['schemas']['ThemesResponse']>('/api/campaigns/themes')
      this.themes = response.themes
    },
  },
})
