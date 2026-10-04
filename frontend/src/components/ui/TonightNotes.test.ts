// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { ApiError } from '../../api/client'
import { useAuthStore } from '../../stores/auth'
import { useTonightStore } from '../../stores/tonight'
import TonightNotes from './TonightNotes.vue'

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
  useAuthStore().account = { id: 'A', email: 'a@example.com' }
})
const props = {
  campaignId: 'C1',
  entityId: 'E1',
  entityName: 'Blacksmith',
  savedText: 'Saved notes',
}
const button = (wrapper: ReturnType<typeof mount>, text: string) =>
  wrapper.findAll('button').find((b) => b.text() === text)!

describe('TonightNotes SQL editor', () => {
  it('uses explicit save/cancel and preserves multiline text without browser autosave', async () => {
    const save = vi.spyOn(useTonightStore(), 'saveNotes').mockResolvedValue({ refreshed: true })
    const wrapper = mount(TonightNotes, { props })
    expect(button(wrapper, 'Save notes').attributes('disabled')).toBeDefined()
    await wrapper.get('textarea').setValue('<context>\nSecond line')
    expect(save).not.toHaveBeenCalled()
    await button(wrapper, 'Save notes').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith('C1', 'E1', '<context>\nSecond line', 'Saved notes')
    expect(wrapper.text()).toContain('Notes saved to the world')
    expect(wrapper.find('context').exists()).toBe(false)
    await wrapper.get('textarea').setValue('discard')
    await button(wrapper, 'Cancel').trigger('click')
    expect(wrapper.get('textarea').element.value).toBe('<context>\nSecond line')
  })

  it('keeps failed drafts and distinguishes confirmed POST from refresh failure', async () => {
    const save = vi.spyOn(useTonightStore(), 'saveNotes').mockRejectedValue(new Error('Offline'))
    const wrapper = mount(TonightNotes, { props })
    await wrapper.get('textarea').setValue('Unsaved draft')
    await button(wrapper, 'Save notes').trigger('click')
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('Unsaved draft')
    expect(wrapper.text()).toContain('Notes not saved. Offline')
    save.mockResolvedValue({ refreshed: false })
    await button(wrapper, 'Save notes').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Notes saved to the world. Could not refresh')
    expect(wrapper.get('textarea').element.value).toBe('Unsaved draft')
    expect(button(wrapper, 'Save notes').attributes('disabled')).toBeDefined()
  })

  it('refreshes conflicting text, retains the draft, and requires explicit review', async () => {
    const store = useTonightStore()
    const save = vi
      .spyOn(store, 'saveNotes')
      .mockRejectedValue(new ApiError(409, 'stale', 'Changed'))
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ session: { E1: { notes: '<Other edit>\nLine two' } }, knowledge: {} }),
      ),
    )
    const wrapper = mount(TonightNotes, { props })
    await wrapper.get('textarea').setValue('My draft')
    await button(wrapper, 'Save notes').trigger('click')
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('My draft')
    expect(wrapper.get('pre').text()).toBe('<Other edit>\nLine two')
    expect(wrapper.find('other').exists()).toBe(false)
    expect(button(wrapper, 'Save notes').attributes('disabled')).toBeDefined()
    await button(wrapper, 'Keep my draft after review').trigger('click')
    save.mockResolvedValue({ refreshed: true })
    await button(wrapper, 'Save notes').trigger('click')
    expect(save).toHaveBeenLastCalledWith('C1', 'E1', 'My draft', '<Other edit>\nLine two')
  })

  it('keeps unread drafts across incoming projections and ignores old account input/results', async () => {
    let resolve!: (value: { refreshed: boolean }) => void
    vi.spyOn(useTonightStore(), 'saveNotes').mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
    const wrapper = mount(TonightNotes, { props })
    await wrapper.get('textarea').setValue('My draft')
    await wrapper.setProps({ savedText: 'Remote edit' })
    expect(wrapper.get('textarea').element.value).toBe('My draft')
    await button(wrapper, 'Save notes').trigger('click')
    const field = wrapper.get('textarea').element
    useAuthStore().account = { id: 'B', email: 'b@example.com' }
    field.value = 'Old account stale input'
    field.dispatchEvent(new Event('input', { bubbles: true }))
    await wrapper.vm.$nextTick()
    resolve({ refreshed: true })
    await flushPromises()
    expect(field.value).toBe('')
    expect(wrapper.text()).not.toContain('Notes saved')
    await wrapper.setProps({ entityId: 'E2', savedText: '' })
    expect(field.value).toBe('')
    useAuthStore().clearSession()
    await wrapper.vm.$nextTick()
    expect(field.disabled).toBe(true)
  })
})

it.each([{ campaignId: 'C2' }, { entityId: 'E2' }])(
  'ignores late saves after scope changes: %o',
  async (target) => {
    let resolve!: (value: { refreshed: boolean }) => void
    vi.spyOn(useTonightStore(), 'saveNotes').mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
    const wrapper = mount(TonightNotes, { props })
    await wrapper.get('textarea').setValue('Old scope draft')
    await button(wrapper, 'Save notes').trigger('click')
    expect(wrapper.text()).not.toContain('Notes saved to the world')
    await wrapper.setProps({ ...target, savedText: 'New scope notes' })
    resolve({ refreshed: true })
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('New scope notes')
    expect(wrapper.text()).not.toContain('Notes saved to the world')
    wrapper.unmount()
  },
)

it('keeps a conflicting draft when current saved notes cannot be read and offers retry', async () => {
  vi.spyOn(useTonightStore(), 'saveNotes').mockRejectedValue(new ApiError(409, 'stale', 'Changed'))
  const fetcher = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Offline'))
  const wrapper = mount(TonightNotes, { props })
  await wrapper.get('textarea').setValue('My draft')
  await button(wrapper, 'Save notes').trigger('click')
  await flushPromises()
  expect(wrapper.get('textarea').element.value).toBe('My draft')
  expect(wrapper.text()).toContain('Could not refresh current notes')
  fetcher.mockResolvedValue(
    new Response(JSON.stringify({ session: { E1: { notes: 'Other notes' } }, knowledge: {} })),
  )
  await button(wrapper, 'Retry review').trigger('click')
  await flushPromises()
  expect(wrapper.get('pre').text()).toBe('Other notes')
  await button(wrapper, 'Reload saved notes').trigger('click')
  expect(wrapper.get('textarea').element.value).toBe('Other notes')
  wrapper.unmount()
})

it('reconciles a successful save with newer returned server text and keeps Cancel available against confirmed text', async () => {
  const save = vi
    .spyOn(useTonightStore(), 'saveNotes')
    .mockResolvedValue({ refreshed: true, savedText: 'Newer server note' })
  const wrapper = mount(TonightNotes, { props })
  await wrapper.get('textarea').setValue('Submitted note')
  await button(wrapper, 'Save notes').trigger('click')
  await flushPromises()
  expect(wrapper.get('textarea').element.value).toBe('Newer server note')
  await wrapper.get('textarea').setValue('My subsequent draft')
  await button(wrapper, 'Save notes').trigger('click')
  expect(save).toHaveBeenLastCalledWith('C1', 'E1', 'My subsequent draft', 'Newer server note')
  await flushPromises()
  await wrapper.get('textarea').setValue('Unsaved')
  await wrapper.setProps({ savedText: 'Latest server note' })
  await wrapper.get('textarea').setValue('Newer server note')
  expect(button(wrapper, 'Cancel').attributes('disabled')).toBeUndefined()
  await button(wrapper, 'Cancel').trigger('click')
  expect(wrapper.get('textarea').element.value).toBe('Latest server note')
})

it('counts Unicode code points and retains an oversized draft with clear feedback', async () => {
  const save = vi.spyOn(useTonightStore(), 'saveNotes').mockResolvedValue({ refreshed: true })
  const wrapper = mount(TonightNotes, { props })
  expect(wrapper.get('textarea').attributes('maxlength')).toBeUndefined()
  const valid = '😀'.repeat(20000)
  await wrapper.get('textarea').setValue(valid)
  await button(wrapper, 'Save notes').trigger('click')
  await flushPromises()
  expect(save).toHaveBeenCalledWith('C1', 'E1', valid, 'Saved notes')
  await wrapper.get('textarea').setValue(valid + '😀')
  await button(wrapper, 'Save notes').trigger('click')
  expect(save).toHaveBeenCalledTimes(1)
  expect(wrapper.get('textarea').element.value).toBe(valid + '😀')
  expect(wrapper.text()).toContain('exceed 20,000 characters')
})

it('disables overlapping conflict review and ignores a review dismissed before its response', async () => {
  vi.spyOn(useTonightStore(), 'saveNotes').mockRejectedValue(new ApiError(409, 'stale', 'Changed'))
  let resolve!: (response: Response) => void
  const fetcher = vi
    .spyOn(globalThis, 'fetch')
    .mockRejectedValueOnce(new Error('Offline'))
    .mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
  const wrapper = mount(TonightNotes, { props })
  await wrapper.get('textarea').setValue('My draft')
  await button(wrapper, 'Save notes').trigger('click')
  await flushPromises()
  await button(wrapper, 'Retry review').trigger('click')
  expect(button(wrapper, 'Retry review').attributes('disabled')).toBeDefined()
  await button(wrapper, 'Retry review').trigger('click')
  expect(fetcher).toHaveBeenCalledTimes(2)
  await button(wrapper, 'Cancel').trigger('click')
  resolve(
    new Response(
      JSON.stringify({ session: { E1: { notes: 'Obsolete response' } }, knowledge: {} }),
    ),
  )
  await flushPromises()
  expect(wrapper.get('textarea').element.value).toBe('Saved notes')
  expect(wrapper.find('pre').exists()).toBe(false)
  await wrapper.get('textarea').setValue('Different draft')
  await button(wrapper, 'Cancel').trigger('click')
  expect(wrapper.get('textarea').element.value).toBe('Saved notes')
})

it('preserves the newer applied projection when the save refresh was superseded', async () => {
  let resolve!: (value: { refreshed: boolean; savedText: string }) => void
  vi.spyOn(useTonightStore(), 'saveNotes').mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r
      }),
  )
  const wrapper = mount(TonightNotes, { props })
  await wrapper.get('textarea').setValue('Submitted note')
  await button(wrapper, 'Save notes').trigger('click')
  // A later consequence refresh has already supplied this newer server text.
  await wrapper.setProps({ savedText: 'Newer applied text' })
  resolve({ refreshed: true, savedText: 'Newer applied text' })
  await flushPromises()
  expect(wrapper.get('textarea').element.value).toBe('Newer applied text')
  await wrapper.get('textarea').setValue('Draft after saving')
  await button(wrapper, 'Cancel').trigger('click')
  expect(wrapper.get('textarea').element.value).toBe('Newer applied text')
  wrapper.unmount()
})
