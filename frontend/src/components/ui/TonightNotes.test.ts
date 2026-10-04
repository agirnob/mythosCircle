// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { useAuthStore } from '../../stores/auth'
import TonightNotes from './TonightNotes.vue'

const account = (id: string) => ({ id, email: `${id}@example.com`, created_at: '' })
const key = (owner = 'A', campaign = 'C1') => `mythoscircle:tonight:${owner}:${campaign}`
let saved: Map<string, string>
let storage: {
  getItem: ReturnType<typeof vi.fn>
  setItem: ReturnType<typeof vi.fn>
  removeItem: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  setActivePinia(createPinia())
  useAuthStore().account = account('A')
  saved = new Map()
  storage = {
    getItem: vi.fn((key: string) => saved.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => saved.set(key, value)),
    removeItem: vi.fn((key: string) => saved.delete(key)),
  }
  vi.stubGlobal('localStorage', storage)
})
afterEach(() => vi.unstubAllGlobals())

describe('TonightNotes', () => {
  it('labels multiline notes, saves immediately, restores after remount, and removes empty drafts', async () => {
    const wrapper = mount(TonightNotes, { props: { campaignId: 'C1' } })
    const field = wrapper.get('textarea')
    expect(wrapper.get('label').attributes('for')).toBe(field.attributes('id'))
    expect(field.attributes('aria-describedby')).toBeTruthy()
    expect(wrapper.text()).toContain('saved only in this browser')
    await field.setValue('Meet Mira\nResolve the thread')
    expect(saved.get(key())).toBe('Meet Mira\nResolve the thread')
    expect(wrapper.get('[role=status]').text()).toBe('Saved in this browser.')
    wrapper.unmount()
    const restored = mount(TonightNotes, { props: { campaignId: 'C1' } })
    expect(restored.get('textarea').element.value).toBe('Meet Mira\nResolve the thread')
    await restored.get('textarea').setValue('')
    expect(saved.has(key())).toBe(false)
    expect(storage.removeItem).toHaveBeenCalledWith(key())
    expect(restored.get('[role=status]').text()).toBe('No notes saved.')
    restored.unmount()
  })

  it('clears synchronously across accounts and restores only the selected campaign', async () => {
    saved.set(key(), 'A private notes')
    saved.set(key('B'), 'B private notes')
    saved.set(key('B', 'C2'), 'Second campaign')
    const wrapper = mount(TonightNotes, { props: { campaignId: 'C1' } })
    expect(wrapper.get('textarea').element.value).toBe('A private notes')
    useAuthStore().account = account('B')
    await wrapper.vm.$nextTick()
    expect(wrapper.get('textarea').element.value).toBe('B private notes')
    await wrapper.get('textarea').setValue('B changed notes')
    expect(saved.get(key())).toBe('A private notes')
    expect(saved.get(key('B'))).toBe('B changed notes')
    await wrapper.setProps({ campaignId: 'C2' })
    expect(wrapper.get('textarea').element.value).toBe('Second campaign')
    await wrapper.setProps({ campaignId: 'C3' })
    expect(wrapper.get('textarea').element.value).toBe('')
    await wrapper.setProps({ campaignId: 'C2' })
    expect(wrapper.get('textarea').element.value).toBe('Second campaign')
    useAuthStore().clearSession()
    await wrapper.vm.$nextTick()
    expect(wrapper.get('textarea').element.value).toBe('')
    expect(wrapper.get('textarea').element.disabled).toBe(true)
    expect(wrapper.text()).toContain('Sign in')
    expect(saved.get(key('B', 'C2'))).toBe('Second campaign')
    wrapper.unmount()
  })

  it('rejects old rendered account input before the next render and accepts the new scope afterward', async () => {
    saved.set(key(), 'A private notes')
    saved.set(key('B'), 'B private notes')
    const wrapper = mount(TonightNotes, { props: { campaignId: 'C1' } })
    const field = wrapper.get('textarea').element
    expect(field.dataset.storageKey).toBe(key())
    useAuthStore().account = account('B')
    // Input may still arrive from the old DOM before Vue patches it for B.
    field.value = 'A private notes from a stale input'
    field.dispatchEvent(new Event('input', { bubbles: true }))
    expect(storage.setItem).not.toHaveBeenCalled()
    expect(saved.get(key('B'))).toBe('B private notes')
    expect(saved.get(key())).toBe('A private notes')
    await wrapper.vm.$nextTick()
    expect(field.dataset.storageKey).toBe(key('B'))
    expect(field.value).toBe('B private notes')
    await wrapper.get('textarea').setValue('B new notes')
    expect(saved.get(key('B'))).toBe('B new notes')
    expect(saved.get(key())).toBe('A private notes')
    wrapper.unmount()
  })

  it('disables notes without an authenticated account and never accesses storage', () => {
    useAuthStore().clearSession()
    const wrapper = mount(TonightNotes, { props: { campaignId: 'C1' } })
    expect(wrapper.get('textarea').element.disabled).toBe(true)
    expect(storage.getItem).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it.each(['getItem', 'setItem', 'removeItem'] as const)(
    'keeps memory notes editable when %s throws and never claims they were saved',
    async (method) => {
      storage[method].mockImplementation(() => {
        throw new Error('Storage denied')
      })
      if (method === 'getItem') {
        storage.setItem.mockImplementation(() => {
          throw new Error('Storage denied')
        })
      }
      const wrapper = mount(TonightNotes, { props: { campaignId: 'C1' } })
      if (method === 'getItem') expect(wrapper.text()).toContain('Notes are not saved')
      await wrapper.get('textarea').setValue(method === 'removeItem' ? '' : 'Memory only')
      expect(wrapper.text()).toContain('Notes are not saved')
      expect(wrapper.get('textarea').element.disabled).toBe(false)
      if (method !== 'removeItem') expect(wrapper.get('textarea').element.value).toBe('Memory only')
      wrapper.unmount()
    },
  )
})
