// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ApiError } from '../api/client'
import { useCandidatesStore } from '../stores/candidates'
import { useJobsStore } from '../stores/jobs'
import { useCampaignsStore } from '../stores/campaigns'
import { useWorldStore } from '../stores/world'
import type { components } from '../api/schema'
import NewAskView from './NewAskView.vue'
vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ params: { id: 'C1' } }),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('../ws', () => ({ connectJobSocket: () => () => {} }))
const proposal = {
  id: 'CA1',
  campaign_id: 'C1',
  job_id: 'J1',
  kind: 'entity',
  status: 'proposed',
  regenerates_entity_id: 'E1',
  payload: { name: 'Sable', personality: 'Generated' },
  created_at: '',
} as components['schemas']['CandidateResponse']
describe('Ask regeneration conflict recovery', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })
  async function setup(message = 'Target changed since this candidate was generated') {
    const candidates = useCandidatesStore(),
      jobs = useJobsStore()
    candidates.upsert(structuredClone(proposal))
    vi.spyOn(candidates, 'syncList').mockResolvedValue()
    vi.spyOn(jobs, 'syncList').mockResolvedValue()
    vi.spyOn(useCampaignsStore(), 'fetchOne').mockResolvedValue()
    vi.spyOn(useWorldStore(), 'load').mockResolvedValue()
    const accept = vi
      .spyOn(candidates, 'accept')
      .mockRejectedValue(new ApiError(409, 'conflict', message))
    const wrapper = mount(NewAskView)
    await flushPromises()
    const button = (text: string) =>
      wrapper.findAll('button').find((entry) => entry.text() === text)!
    await button('Accept').trigger('click')
    await flushPromises()
    return { wrapper, button, accept, candidates, jobs }
  }
  it('requires two explicit overwrite clicks and preserves the reviewed payload', async () => {
    const { wrapper, button, accept } = await setup()
    await button('Review / edit').trigger('click')
    await wrapper.get('#review-CA1-personality').setValue('My edited proposal')
    await button('Accept generated version anyway').trigger('click')
    expect(accept).toHaveBeenCalledOnce()
    accept.mockResolvedValue({ ...proposal, status: 'accepted', accepted_entity_id: 'E1' })
    await button('Confirm overwrite').trigger('click')
    await flushPromises()
    expect(accept).toHaveBeenLastCalledWith(
      'C1',
      'CA1',
      expect.objectContaining({ personality: 'My edited proposal' }),
      true,
    )
    expect(wrapper.text()).not.toContain('Confirm overwrite')
    wrapper.unmount()
  })
  it('re-rolls the candidate, discards its old review draft and blocks accept until the job settles', async () => {
    const { wrapper, button, jobs } = await setup()
    await button('Review / edit').trigger('click')
    const submit = vi.spyOn(jobs, 'submitRegenerate').mockImplementation(async () => {
      const job = {
        id: 'R1',
        campaign_id: 'C1',
        kind: 'regenerate',
        state: 'queued',
        payload: { target: { kind: 'candidate', id: 'CA1' } },
        created_at: '',
        progress: 0,
        max_llm_calls: 0,
        max_media_calls: 0,
        error: null,
        result: null,
        started_at: null,
        finished_at: null,
        queue_position: 1,
      } as components['schemas']['JobResponse']
      jobs.upsert(job)
      return job
    })
    await button('Re-roll proposal').trigger('click')
    await flushPromises()
    expect(submit).toHaveBeenCalledWith('C1', { kind: 'candidate', id: 'CA1' }, null)
    expect(wrapper.find('#review-CA1-personality').exists()).toBe(false)
    expect(button('Accept').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
  it('does not offer overwrite for an already settled candidate conflict', async () => {
    const { wrapper } = await setup('Candidate already accepted')
    expect(wrapper.text()).not.toContain('Accept generated version anyway')
    expect(wrapper.text()).toContain('Candidate already accepted')
    wrapper.unmount()
  })
  it('cancel leaves the candidate proposed and sends no additional accept', async () => {
    const { wrapper, button, accept, candidates } = await setup()
    await button('Cancel').trigger('click')
    expect(accept).toHaveBeenCalledOnce()
    expect(candidates.proposed('C1')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Accept generated version anyway')
    wrapper.unmount()
  })
})
