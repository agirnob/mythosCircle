// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../../api/schema'
import { apiFetch } from '../../api/client'
import { useJobsStore } from '../../stores/jobs'
import { useWorldStore } from '../../stores/world'
import EntityMediaActions from './EntityMediaActions.vue'
import { useAuthStore } from '../../stores/auth'
import { connectJobSocket } from '../../ws'
import { invalidateSession } from '../../api/session'

vi.mock('../../ws', () => ({ connectJobSocket: vi.fn(() => () => {}) }))
vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  apiFetch: vi.fn(),
}))

type Job = components['schemas']['JobResponse']
type Entity = components['schemas']['EntityExport']

const entity: Entity = {
  id: 'E1',
  kind: 'character',
  name: 'The Grafted Captain',
  text: null,
  data: { role: 'BBEG', appearance: 'Brass plates and one amber eye.' },
  media: [],
}
const automaticPrompt = 'Brass plates and one amber eye.\nSlow cinematic character reveal.'
const storedDrafts = new Map<string, string>()
const draftStorage = {
  getItem: (key: string) => storedDrafts.get(key) ?? null,
  setItem: (key: string, value: string) => { storedDrafts.set(key, value) },
  removeItem: (key: string) => { storedDrafts.delete(key) },
  clear: () => { storedDrafts.clear() },
}

function draftJob(id: string, state: string, prompt: string, createdAt: string): Job {
  return {
    id,
    campaign_id: 'C1',
    kind: 'video_prompt',
    payload: { entity_id: 'E1' },
    state,
    progress: state === 'succeeded' ? 1 : 0,
    max_llm_calls: 1,
    max_media_calls: 0,
    error: null,
    result: state === 'succeeded' ? { entity_id: 'E1', prompt } : null,
    created_at: createdAt,
    started_at: null,
    finished_at: null,
    queue_position: state === 'succeeded' ? null : 1,
  }
}

function mountActions() {
  return mount(EntityMediaActions, { props: { campaignId: 'C1', entity } })
}

describe('EntityMediaActions reveal prompt', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    vi.stubGlobal('localStorage', draftStorage)
    globalThis.localStorage.clear()
    vi.mocked(apiFetch).mockReset().mockResolvedValue({ prompt: automaticPrompt })
    vi.spyOn(useWorldStore(), 'fetchMedia').mockResolvedValue()
    vi.spyOn(useJobsStore(), 'syncList').mockResolvedValue()
  })

  it('does not start requests or subscribe if the account changes before its mount hook', async () => {
    const auth = useAuthStore()
    auth.account = { id: 'A', email: 'a@example.com', is_admin: false }
    vi.mocked(connectJobSocket).mockClear()
    const wrapper = mount(EntityMediaActions, {
      props: { campaignId: 'C1', entity },
      global: { mixins: [{ beforeMount() { invalidateSession() } }] },
    })
    await flushPromises()
    expect(connectJobSocket).not.toHaveBeenCalled()
    expect(apiFetch).not.toHaveBeenCalled()
    expect(useWorldStore().fetchMedia).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('subscribes with the session captured when the component was created', async () => {
    const wrapper = mountActions()
    await flushPromises()
    expect(connectJobSocket).toHaveBeenLastCalledWith('C1', expect.any(Function), { generation: expect.any(Number) })
    wrapper.unmount()
  })

  it('prefills the appearance prompt and ignores older text-model drafts and failures', async () => {
    const jobs = useJobsStore()
    jobs.upsert(draftJob('J0', 'succeeded', 'Old LLM draft.', '2026-09-29T19:59:00Z'))
    jobs.upsert({
      ...draftJob('J1', 'failed', '', '2026-09-29T20:00:00Z'),
      error: 'video prompt generation failed: provider connection error',
      queue_position: null,
    })
    const render = vi.spyOn(jobs, 'submitRevealVideo').mockResolvedValue({} as Job)
    const wrapper = mountActions()
    await flushPromises()

    expect(wrapper.text()).not.toContain('Draft reveal prompt')
    expect(wrapper.text()).not.toContain('provider connection error')
    expect((wrapper.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value)
      .toBe(automaticPrompt)
    expect(wrapper.text()).toContain('Prompt sent to video generator')
    await wrapper.findAll('button').find((button) => button.text() === 'Render reveal video')!.trigger('click')
    expect(render).toHaveBeenCalledWith('C1', 'E1', automaticPrompt, undefined)
    wrapper.unmount()
  })

  it('submits manual whitespace exactly as shown and can restore the appearance prompt', async () => {
    const jobs = useJobsStore()
    const render = vi.spyOn(jobs, 'submitRevealVideo').mockResolvedValue({} as Job)
    const wrapper = mountActions()
    await flushPromises()
    const textarea = wrapper.get('textarea[aria-label="Reveal video prompt"]')
    expect((textarea.element as HTMLTextAreaElement).value).toBe(automaticPrompt)

    await textarea.setValue('  A closer camera move.\n')
    await wrapper.findAll('button').find((button) => button.text() === 'Render reveal video')!.trigger('click')
    expect(render).toHaveBeenCalledWith('C1', 'E1', '  A closer camera move.\n', undefined)

    await wrapper.findAll('button').find((button) => button.text() === 'Use appearance prompt')!.trigger('click')
    expect((textarea.element as HTMLTextAreaElement).value).toBe(automaticPrompt)
    expect(globalThis.localStorage.getItem('mythoscircle:reveal-prompt:C1:E1')).toBeNull()
    wrapper.unmount()
  })

  it('restores the DM’s exact unfinished prompt after a remount and renders that text', async () => {
    const render = vi.spyOn(useJobsStore(), 'submitRevealVideo').mockResolvedValue({} as Job)
    const firstVisit = mountActions()
    await flushPromises()
    await firstVisit.get('textarea[aria-label="Reveal video prompt"]').setValue('  Begin in shadow.\nReveal the amber eye.  ')
    firstVisit.unmount()

    const refreshed = mountActions()
    await flushPromises()
    expect((refreshed.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value)
      .toBe('  Begin in shadow.\nReveal the amber eye.  ')
    await refreshed.findAll('button').find((button) => button.text() === 'Render reveal video')!.trigger('click')
    expect(render).toHaveBeenCalledWith('C1', 'E1', '  Begin in shadow.\nReveal the amber eye.  ', undefined)

    await refreshed.findAll('button').find((button) => button.text() === 'Use appearance prompt')!.trigger('click')
    refreshed.unmount()
    const afterReset = mountActions()
    await flushPromises()
    expect((afterReset.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value)
      .toBe(automaticPrompt)
    afterReset.unmount()
  })

  it('keeps a deliberately cleared prompt empty after refresh until the DM restores the default', async () => {
    const firstVisit = mountActions()
    await flushPromises()
    await firstVisit.get('textarea[aria-label="Reveal video prompt"]').setValue('')
    firstVisit.unmount()

    const refreshed = mountActions()
    await flushPromises()
    expect((refreshed.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value).toBe('')
    expect(refreshed.findAll('button').find((button) => button.text() === 'Render reveal video')!.attributes('disabled')).toBeDefined()
    await refreshed.findAll('button').find((button) => button.text() === 'Use appearance prompt')!.trigger('click')
    expect((refreshed.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value).toBe(automaticPrompt)
    refreshed.unmount()
  })

  it('keeps drafts separate by character and preserves edits when appearance changes', async () => {
    const wrapper = mountActions()
    await flushPromises()
    const textarea = wrapper.get('textarea[aria-label="Reveal video prompt"]')
    await textarea.setValue('My own reveal.')

    await wrapper.setProps({ entity: { ...entity, id: 'E2' } })
    await flushPromises()
    expect((textarea.element as HTMLTextAreaElement).value).toBe(automaticPrompt)

    await wrapper.setProps({ campaignId: 'C2', entity })
    await flushPromises()
    expect((textarea.element as HTMLTextAreaElement).value).toBe(automaticPrompt)

    vi.mocked(apiFetch).mockResolvedValue({ prompt: 'New silver mask.\nSlow reveal.' })
    await wrapper.setProps({ campaignId: 'C1', entity: { ...entity, data: { ...entity.data, appearance: 'New silver mask.' } } })
    await flushPromises()
    expect((textarea.element as HTMLTextAreaElement).value).toBe('My own reveal.')
    await wrapper.findAll('button').find((button) => button.text() === 'Use appearance prompt')!.trigger('click')
    expect((textarea.element as HTMLTextAreaElement).value).toBe('New silver mask.\nSlow reveal.')
    wrapper.unmount()
  })

  it('keeps rendering unavailable until there is a visible prompt', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error('offline'))
    const wrapper = mountActions()
    await flushPromises()
    const render = wrapper.findAll('button').find((button) => button.text() === 'Render reveal video')!
    expect(render.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Could not load the automatic prompt.')

    await wrapper.get('textarea[aria-label="Reveal video prompt"]').setValue('My own reveal.')
    expect(render.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('shows the prompt saved with the displayed clip even while a newer render is queued', async () => {
    useWorldStore().mediaByCampaign['C1'] = [{
      id: 'M1', campaign_id: 'C1', entity_id: 'E1', filename: 'first.mp4', kind: 'video',
      created_at: '2026-09-29T20:00:00Z',
    }]
    const jobs = useJobsStore()
    const usedPrompt = '  Reveal the amber eye.\nHold the shot.  '
    jobs.upsert({
      ...draftJob('JV1', 'succeeded', '', '2026-09-29T20:00:00Z'),
      kind: 'video',
      payload: { entity_id: 'E1' },
      result: { entity_id: 'E1', filename: 'first.mp4', prompt: usedPrompt },
    })
    jobs.upsert({
      ...draftJob('JV2', 'queued', '', '2026-09-29T20:01:00Z'),
      kind: 'video',
      payload: { entity_id: 'E1', prompt: 'A newer prompt.' },
    })

    const wrapper = mountActions()
    expect(wrapper.get('details.mc-media-prompt-history pre').element.textContent).toBe(usedPrompt)
    expect(wrapper.text()).toContain('Showing the previous video while the new reveal renders.')
    wrapper.unmount()
  })
})

describe('H3 portrait enhancement', () => {
  const source = {
    id: 'M1',
    campaign_id: 'C1',
    entity_id: 'E1',
    kind: 'image',
    filename: 'first.png',
    created_at: '2026-10-20T00:00:00Z',
  }
  const enhanced = '[Shot 1] Blue portrait turns.\n\noverall_soundscape: N/A'
  function enhancement(state = 'queued'): Job {
    return {
      ...draftJob('JH3', state, enhanced, '2026-10-20T00:00:00Z'),
      payload: { entity_id: 'E1', image_id: 'M1', prompt: 'Turn slowly.' },
      result: state === 'succeeded' ? { entity_id: 'E1', image_id: 'M1', prompt: enhanced } : null,
    }
  }
  const button = (w: ReturnType<typeof mountActions>, text: string) =>
    w.findAll('button').find((b) => b.text() === text)!
  async function start(w: ReturnType<typeof mountActions>) {
    await flushPromises()
    await w.get('textarea[aria-label="Your video direction"]').setValue('Turn slowly.')
    await button(w, 'Enhance with portrait').trigger('click')
    await flushPromises()
  }
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    vi.stubGlobal('localStorage', draftStorage)
    localStorage.clear()
    vi.mocked(apiFetch).mockReset().mockResolvedValue({ prompt: automaticPrompt })
    vi.spyOn(useWorldStore(), 'fetchMedia').mockResolvedValue()
    vi.spyOn(useJobsStore(), 'syncList').mockResolvedValue()
    useWorldStore().mediaByCampaign['C1'] = [source]
    vi.spyOn(useJobsStore(), 'submitRevealVideoPrompt').mockImplementation(async () => {
      const job = enhancement()
      useJobsStore().upsert(job)
      return job
    })
  })
  it('enhances image+direction, preserves review, and renders edited text with pinned image after refresh/new portrait', async () => {
    const render = vi.spyOn(useJobsStore(), 'submitRevealVideo').mockResolvedValue({} as Job)
    let w = mountActions()
    await start(w)
    expect(useJobsStore().submitRevealVideoPrompt).toHaveBeenCalledWith(
      'C1',
      'E1',
      'M1',
      'Turn slowly.',
    )
    expect(render).not.toHaveBeenCalled()
    useJobsStore().upsert(enhancement('succeeded'))
    await flushPromises()
    expect(
      (w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value,
    ).toBe(enhanced)
    await w.get('textarea[aria-label="Reveal video prompt"]').setValue('  Approved exactly.\n')
    w.unmount()
    useWorldStore().mediaByCampaign['C1']!.push({
      ...source,
      id: 'M2',
      filename: 'new.png',
      created_at: '2026-10-20T01:00:00Z',
    })
    w = mountActions()
    await flushPromises()
    expect(w.get('img[alt$="source portrait for reveal"]').attributes('src')).toContain('first.png')
    await button(w, 'Render reveal video').trigger('click')
    expect(render).toHaveBeenCalledWith('C1', 'E1', '  Approved exactly.\n', 'M1')
    w.unmount()
  })
  it('keeps edits made during enhancement and offers its result for explicit review', async () => {
    const w = mountActions()
    await start(w)
    await w.get('textarea[aria-label="Reveal video prompt"]').setValue('Keep my edits')
    useJobsStore().upsert(enhancement('succeeded'))
    await flushPromises()
    expect(
      (w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value,
    ).toBe('Keep my edits')
    expect(w.text()).toContain('Your edits were kept.')
    await button(w, 'Use enhanced prompt').trigger('click')
    expect(
      (w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value,
    ).toBe(enhanced)
    w.unmount()
  })
  it('shows provider failure and retains editable direction and manual text', async () => {
    const w = mountActions()
    await flushPromises()
    await w.get('textarea[aria-label="Reveal video prompt"]').setValue('Manual')
    await start(w)
    useJobsStore().upsert({
      ...enhancement(),
      state: 'failed',
      error: 'Model does not support vision',
    })
    await flushPromises()
    expect(w.text()).toContain('Model does not support vision')
    expect(
      (w.get('textarea[aria-label="Your video direction"]').element as HTMLTextAreaElement).value,
    ).toBe('Turn slowly.')
    expect(
      (w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value,
    ).toBe('Manual')
    expect(button(w, 'Enhance with portrait').attributes('disabled')).toBeUndefined()
    w.unmount()
  })
  it('restores pending work after refresh and ignores unrelated completion', async () => {
    let w = mountActions()
    await start(w)
    w.unmount()
    w = mountActions()
    await flushPromises()
    useJobsStore().upsert({ ...enhancement('succeeded'), id: 'UNRELATED' })
    await flushPromises()
    expect(
      (w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value,
    ).toBe(automaticPrompt)
    useJobsStore().upsert(enhancement('succeeded'))
    await flushPromises()
    expect(
      (w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value,
    ).toBe(enhanced)
    w.unmount()
  })
  it('preserves entity separation when a draft completes after a switch', async () => {
    const w = mountActions()
    await start(w)
    await w.setProps({ entity: { ...entity, id: 'E2' } })
    await w.get('textarea[aria-label="Reveal video prompt"]').setValue('Entity two')
    useJobsStore().upsert(enhancement('succeeded'))
    await flushPromises()
    expect(
      (w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value,
    ).toBe('Entity two')
    await w.setProps({ entity })
    await flushPromises()
    expect(
      (w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value,
    ).toBe(enhanced)
    w.unmount()
  })
  it('recovers image-aware completion without localStorage and never resurfaces older jobs', async () => {
    vi.stubGlobal('localStorage', { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') }, removeItem() {} })
    useJobsStore().upsert({ ...enhancement('succeeded'), id: 'OLD', created_at: '2026-09-01T00:00:00Z' })
    useJobsStore().upsert(enhancement('succeeded'))
    const w = mountActions()
    await flushPromises()
    expect((w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value).toBe(enhanced)
    useJobsStore().upsert({ ...enhancement('succeeded'), id: 'OLD', created_at: '2026-09-01T00:00:00Z' })
    await flushPromises()
    expect(w.text()).not.toContain('Use enhanced prompt')
    w.unmount()
  })

  it('recovers an async completed enhancement as a candidate while preserving manual edits', async () => {
    const w = mountActions()
    await flushPromises()
    await w.get('textarea[aria-label="Reveal video prompt"]').setValue('Manual text')
    useJobsStore().upsert(enhancement('succeeded'))
    await flushPromises()
    expect((w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value).toBe('Manual text')
    expect(w.text()).toContain('Use enhanced prompt')
    w.unmount()
  })

  it('resets deleted pinned source explicitly and discards late submission after resetting to appearance', async () => {
    const render = vi.spyOn(useJobsStore(), 'submitRevealVideo').mockResolvedValue({} as Job)
    const w = mountActions()
    await start(w)
    useJobsStore().upsert(enhancement('succeeded'))
    await flushPromises()
    await w.get('textarea[aria-label="Reveal video prompt"]').setValue('My manual text')
    useWorldStore().mediaByCampaign['C1'] = [{ ...source, id: 'M2', filename: 'new.png', created_at: '2026-10-20T01:00:00Z' }]
    await flushPromises()
    await button(w, 'Use latest portrait').trigger('click')
    expect((w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value).toBe('My manual text')
    expect(w.get('img[alt$="source portrait for reveal"]').attributes('src')).toContain('new.png')
    await button(w, 'Render reveal video').trigger('click')
    expect(render).toHaveBeenCalledWith('C1', 'E1', 'My manual text', undefined)
    let finish!: (job: Job) => void
    vi.mocked(useJobsStore().submitRevealVideoPrompt).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    await button(w, 'Enhance with portrait').trigger('click')
    await button(w, 'Use appearance prompt').trigger('click')
    const late = { ...enhancement('succeeded'), id: 'LATE', created_at: '2026-10-21T00:00:00Z' }
    useJobsStore().upsert(late)
    finish(late)
    await flushPromises()
    expect((w.get('textarea[aria-label="Reveal video prompt"]').element as HTMLTextAreaElement).value).toBe(automaticPrompt)
    expect(w.text()).not.toContain('Use enhanced prompt')
    w.unmount()
  })

})
