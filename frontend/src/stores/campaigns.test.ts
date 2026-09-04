import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { ApiError } from '../api/client'
import { useCampaignsStore } from './campaigns'

const CAMPAIGN = {
  id: '01M1',
  owner_id: '01A',
  title: 'The Shattered Coast',
  description: 'A heist world.',
  theme: 'High Fantasy',
  custom_lore: 'Seed lore.',
  created_at: '2026-09-03T00:00:00Z',
}

describe('campaigns store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('create POSTs the wire contract payload and preprends the campaign (campaigns view form)', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(CAMPAIGN), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const campaigns = useCampaignsStore()
    campaigns.campaigns = [{ ...CAMPAIGN, id: '01M0' }]
    const created = await campaigns.create({
      title: CAMPAIGN.title,
      theme: CAMPAIGN.theme,
      description: CAMPAIGN.description,
      custom_lore: CAMPAIGN.custom_lore,
    })
    // POST /api/campaigns with the exact 1.6 wire shape, session cookie.
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/campaigns',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          title: CAMPAIGN.title,
          theme: CAMPAIGN.theme,
          description: CAMPAIGN.description,
          custom_lore: CAMPAIGN.custom_lore,
        }),
      }),
    )
    expect(created).toEqual(CAMPAIGN)
    // Newest first — the freshly created world leads the list.
    expect(campaigns.campaigns[0].id).toBe('01M1')
    expect(campaigns.campaigns).toHaveLength(2)
  })

  it('create rethrows the ApiError so the form surfaces the message', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ code: 'invalid_theme', message: "theme 'X' is not in the seed list." }),
        { status: 400, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const campaigns = useCampaignsStore()
    await expect(
      campaigns.create({ title: 'T', theme: 'X', description: '', custom_lore: '' }),
    ).rejects.toBeInstanceOf(ApiError)
    expect(campaigns.campaigns).toEqual([])
  })
})
