// @vitest-environment happy-dom
/* global HTMLInputElement, HTMLSelectElement */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { components } from '../api/schema'
import AdminLlmSettings from './AdminLlmSettings.vue'

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('../api/client', async (original) => ({
  ...(await original<typeof import('../api/client')>()),
  apiFetch,
}))
const profile = {
  endpoint: 'http://local/v1',
  model: 'local-model',
  timeout: 2400,
  max_tokens: 8192,
  enable_thinking: null,
  temperature: null,
  top_p: null,
  seed: null,
}
const settings: components['schemas']['LLMSettingsView'] = {
  primary: 'local',
  backup: null,
  local: profile,
  gemini: {
    ...profile,
    endpoint: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    model: 'gemini-3.8-flash',
  },
  saved: false,
  local_key_configured: false,
  gemini_key_configured: true,
}
function button(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('button').find((node) => node.text() === label)!
}
async function start() {
  const wrapper = mount(AdminLlmSettings, { attachTo: document.body })
  await flushPromises()
  return wrapper
}
describe('administrator LLM settings', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    useAuthStore().account = { id: 'A', email: 'admin@example.com', is_admin: true }
    apiFetch.mockImplementation(() => Promise.resolve(structuredClone(settings)))
  })
  it('prefills profiles, saves non-secret routing and tests unsaved profile explicitly', async () => {
    const wrapper = await start()
    expect(wrapper.text()).toContain('Using deployment defaults')
    await wrapper.findAll('select')[0]!.setValue('gemini')
    await wrapper.findAll('select')[1]!.setValue('local')
    await wrapper.findAll('input')[8]!.setValue('gemini-test-model')
    apiFetch.mockResolvedValueOnce({ success: true, message: 'Connection test succeeded.' })
    await button(wrapper, 'Test Gemini connection').trigger('click')
    await flushPromises()
    const test = JSON.parse(apiFetch.mock.calls.at(-1)![1].body)
    expect(test.provider).toBe('gemini')
    expect(test.profile.model).toBe('gemini-test-model')
    expect(wrapper.text()).toContain('Connection test succeeded.')
    apiFetch.mockResolvedValueOnce({ ...settings, primary: 'gemini', backup: 'local', saved: true })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const body = JSON.parse(apiFetch.mock.calls.at(-1)![1].body)
    expect(body.primary).toBe('gemini')
    expect(body.backup).toBe('local')
    expect(Object.keys(body)).toEqual(['primary', 'backup', 'local', 'gemini'])
    expect(JSON.stringify(body)).not.toContain('key')
    expect(wrapper.text()).toContain('Saved. Jobs started from now on')
    wrapper.unmount()
  })
  it('explains exact key placement and disables Gemini until configured', async () => {
    apiFetch.mockResolvedValueOnce({ ...settings, gemini_key_configured: false })
    const wrapper = await start()
    expect(button(wrapper, 'Test Gemini connection').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('/home/homest/mythos-redeploy.yml')
    expect(wrapper.text()).toContain('Recreate the API container')
    expect(wrapper.text()).toContain('Portainer stack variables')
    wrapper.unmount()
  })
  it('explains the wait while a Gemini connection test is pending', async () => {
    const wrapper = await start()
    let resolve!: (value: unknown) => void
    apiFetch.mockReturnValueOnce(
      new Promise((res) => {
        resolve = res
      }),
    )
    await button(wrapper, 'Test Gemini connection').trigger('click')
    expect(wrapper.text()).toContain('Testing Gemini… This may take up to two minutes.')
    resolve({ success: true, message: 'Connection test succeeded.' })
    await flushPromises()
    expect(wrapper.text()).not.toContain('This may take up to two minutes.')
    expect(wrapper.text()).toContain('Connection test succeeded.')
    wrapper.unmount()
  })
  it('retries a failed save with the same unsaved settings', async () => {
    const wrapper = await start()
    await wrapper.findAll('select')[0]!.setValue('gemini')
    apiFetch.mockRejectedValueOnce(new Error('offline'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Save could not be confirmed')
    apiFetch.mockResolvedValueOnce({ ...settings, primary: 'gemini', saved: true })
    await button(wrapper, 'Retry').trigger('click')
    await flushPromises()
    expect(JSON.parse(apiFetch.mock.calls.at(-1)![1].body).primary).toBe('gemini')
    wrapper.unmount()
  })
  it('clears profiles and notifies existing denied navigation on 403', async () => {
    const wrapper = await start()
    apiFetch.mockRejectedValueOnce(new ApiError(403, 'forbidden', 'Denied'))
    await button(wrapper, 'Test local connection').trigger('click')
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.emitted('accessLost')).toHaveLength(1)
    wrapper.unmount()
  })
  it.each(['access', 'unmount'])('ignores pending responses after %s loss', async (kind) => {
    const wrapper = await start()
    let resolve!: (value: unknown) => void
    apiFetch.mockReturnValueOnce(
      new Promise((res) => {
        resolve = res
      }),
    )
    await button(wrapper, 'Reload saved settings').trigger('click')
    if (kind === 'access') useAuthStore().denyAdmin()
    else wrapper.unmount()
    resolve({ ...settings, local: { ...profile, model: 'stale-model' } })
    await flushPromises()
    if (kind === 'access') {
      expect(wrapper.find('form').exists()).toBe(false)
      wrapper.unmount()
    } else expect(document.body.querySelector('form')).toBeNull()
  })
  it('clears success feedback synchronously on edits and preserves new save feedback', async () => {
    const wrapper = await start()
    apiFetch.mockResolvedValueOnce({ success: true, message: 'Connection test succeeded.' })
    await button(wrapper, 'Test local connection').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Connection test succeeded.')
    await wrapper.findAll('input')[1]!.setValue('edited-model')
    expect(wrapper.text()).not.toContain('Connection test succeeded.')
    apiFetch.mockResolvedValueOnce({ ...settings, saved: true })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Saved. Jobs started from now on')
    const timeout = wrapper.findAll('input')[2]!
    expect(timeout.attributes('step')).toBe('any')
    await timeout.setValue('0.5')
    expect(wrapper.text()).not.toContain('Saved. Jobs started from now on')
    expect((timeout.element as HTMLInputElement).checkValidity()).toBe(true)
    await timeout.setValue('0')
    expect((timeout.element as HTMLInputElement).checkValidity()).toBe(false)
    wrapper.unmount()
  })
  it('clears a backup that conflicts with a newly selected primary', async () => {
    const wrapper = await start()
    const primary = wrapper.findAll('select')[0]!
    const backup = wrapper.findAll('select')[1]!
    await backup.setValue('gemini')
    await primary.setValue('gemini')
    expect((backup.element as HTMLSelectElement).selectedOptions[0]?.textContent).toBe('None')
    apiFetch.mockResolvedValueOnce({ ...settings, primary: 'gemini', saved: true })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(JSON.parse(apiFetch.mock.calls.at(-1)![1].body).backup).toBeNull()
    wrapper.unmount()
  })
})
