// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import AdminView from './AdminView.vue'
import type { components } from '../api/schema'

const { apiFetch, replace } = vi.hoisted(() => ({ apiFetch: vi.fn(), replace: vi.fn() }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  apiFetch,
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ replace }) }))
const user: components['schemas']['AdminUser'] = {
  id: 'U',
  email: 'user@example.com',
  is_admin: false,
  disabled_at: null,
  created_at: '2026-10-10T00:00:00Z',
  campaign_count: 3,
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}
async function start() {
  const wrapper = mount(AdminView, { attachTo: document.body })
  await flushPromises()
  return wrapper
}
function button(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('button').find((node) => node.text() === label)!
}
describe('administrative user access', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    useAuthStore().account = { id: 'A', email: 'admin@example.com', is_admin: true }
    apiFetch.mockResolvedValue({ users: [user], next_cursor: null })
  })
  it('confirms the named account, explains signed links, restores focus, and uses server status', async () => {
    const wrapper = await start()
    const disable = button(wrapper, 'Disable')
    await disable.trigger('click')
    expect(wrapper.get('[aria-label="Confirm access change"]').text()).toContain(user.email)
    expect(wrapper.text()).toContain('up to seven days')
    expect(wrapper.text()).toContain('queued or running work continues')
    expect(document.activeElement?.textContent).toBe('Confirm')
    expect(disable.attributes('aria-label')).toBe(`Disable access for ${user.email}`)
    const descriptions = button(wrapper, 'Confirm').attributes('aria-describedby')!.split(' ')
    expect(descriptions.map((id) => wrapper.get(`#${id}`).text()).join(' ')).toContain(user.email)
    expect(descriptions.map((id) => wrapper.get(`#${id}`).text()).join(' ')).toContain(
      'queued or running work continues',
    )
    await button(wrapper, 'Cancel').trigger('click')
    expect(document.activeElement).toBe(disable.element)
    await disable.trigger('click')
    apiFetch.mockResolvedValueOnce({ ...user, disabled_at: '2026-10-10T01:00:00Z' })
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(apiFetch).toHaveBeenLastCalledWith(
      '/api/admin/users/U',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ disabled: true }),
      }),
    )
    expect(wrapper.text()).toContain('Disabled')
    expect(button(wrapper, 'Restore').exists()).toBe(true)
    expect(document.activeElement).toBe(button(wrapper, 'Restore').element)
    await button(wrapper, 'Restore').trigger('click')
    apiFetch.mockResolvedValueOnce(user)
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(button(wrapper, 'Disable').element)
    expect(apiFetch).toHaveBeenLastCalledWith(
      '/api/admin/users/U',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ disabled: false }),
      }),
    )
    wrapper.unmount()
  })
  it('rejects inverted search responses and normalizes load-more query', async () => {
    const wrapper = await start()
    const old = deferred<components['schemas']['AdminUserList']>()
    const newest = deferred<components['schemas']['AdminUserList']>()
    apiFetch.mockReturnValueOnce(old.promise).mockReturnValueOnce(newest.promise)
    await wrapper.get('input').setValue(' old ')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('input').setValue(' NEW ')
    await wrapper.get('form').trigger('submit')
    newest.resolve({ users: [{ ...user, email: 'new@example.com' }], next_cursor: 'cursor' })
    await flushPromises()
    old.resolve({ users: [{ ...user, email: 'old@example.com' }], next_cursor: null })
    await flushPromises()
    expect(wrapper.text()).toContain('new@example.com')
    expect(wrapper.text()).not.toContain('old@example.com')
    apiFetch.mockResolvedValueOnce({ users: [user], next_cursor: null })
    await button(wrapper, 'Load more').trigger('click')
    await flushPromises()
    expect(apiFetch).toHaveBeenLastCalledWith('/api/admin/users?q=new&limit=50&cursor=cursor')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    wrapper.unmount()
  })
  it('clears rows and pending confirmations on 403 even when capability refresh fails', async () => {
    const wrapper = await start()
    await button(wrapper, 'Disable').trigger('click')
    apiFetch
      .mockRejectedValueOnce(new ApiError(403, 'forbidden', 'Access denied.'))
      .mockRejectedValueOnce(new Error('offline'))
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain(user.email)
    expect(wrapper.find('[aria-label="Confirm access change"]').exists()).toBe(false)
    expect(useAuthStore().isAdmin).toBe(false)
    expect(replace).toHaveBeenCalledWith({
      name: 'campaigns',
      query: { notice: 'admin-access-denied' },
    })
    wrapper.unmount()
  })
  it('keeps an uncertain outcome when both save and reconciliation fail', async () => {
    const wrapper = await start()
    await button(wrapper, 'Disable').trigger('click')
    apiFetch
      .mockRejectedValueOnce(new ApiError(500, 'internal_error', 'Internal server error.'))
      .mockRejectedValueOnce(new Error('offline'))
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Account status is uncertain')
    expect(wrapper.text()).not.toContain('Account status was refreshed')
    expect(wrapper.text()).toContain(user.email)
    wrapper.unmount()
  })
  it('does not repopulate after account change or a response pending across 403', async () => {
    const wrapper = await start()
    const pending = deferred<components['schemas']['AdminUserList']>()
    apiFetch.mockReturnValueOnce(pending.promise)
    await button(wrapper, 'Refresh').trigger('click')
    useAuthStore().clearSession()
    pending.resolve({ users: [user], next_cursor: null })
    await flushPromises()
    expect(wrapper.text()).not.toContain(user.email)
    wrapper.unmount()
  })
  it('discards a late successful list after a concurrent admin request returns 403', async () => {
    const wrapper = await start()
    const old = deferred<components['schemas']['AdminUserList']>()
    const latest = deferred<components['schemas']['AdminUserList']>()
    const refresh = deferred<components['schemas']['AccountResponse']>()
    apiFetch
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(latest.promise)
      .mockReturnValueOnce(refresh.promise)
    await wrapper.get('input').setValue('old')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('input').setValue('new')
    await wrapper.get('form').trigger('submit')
    latest.reject(new ApiError(403, 'forbidden', 'Access denied.'))
    await flushPromises()
    expect(wrapper.text()).not.toContain(user.email)
    old.resolve({ users: [user], next_cursor: 'late' })
    refresh.reject(new Error('offline'))
    await flushPromises()
    expect(wrapper.text()).not.toContain(user.email)
    expect(wrapper.findAll('tbody tr')).toHaveLength(0)
    wrapper.unmount()
  })
  it('reconciles a later-page target by identity across matching-email pages without resetting the shown query or cursor', async () => {
    const first = { ...user, id: 'FIRST', email: 'first@example.com' }
    const firstPage = { users: [first], next_cursor: 'page2' }
    apiFetch.mockResolvedValueOnce(firstPage)
    const wrapper = await start()
    await wrapper.get('input').setValue(' EXAMPLE ')
    apiFetch.mockResolvedValueOnce(firstPage)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    apiFetch.mockResolvedValueOnce({ users: [user], next_cursor: 'page3' })
    await button(wrapper, 'Load more').trigger('click')
    await flushPromises()
    await wrapper.get('#admin-action-U').trigger('click')
    apiFetch
      .mockRejectedValueOnce(new ApiError(500, 'internal_error', 'Save failed.'))
      .mockResolvedValueOnce({
        users: [{ ...user, id: 'LONGER', email: 'prefixuser@example.com' }],
        next_cursor: 'matching2',
      })
      .mockResolvedValueOnce({
        users: [{ ...user, disabled_at: '2026-10-10T01:00:00Z' }],
        next_cursor: null,
      })
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(apiFetch).toHaveBeenCalledWith('/api/admin/users?q=user%40example.com&limit=100')
    expect(apiFetch).toHaveBeenLastCalledWith(
      '/api/admin/users?q=user%40example.com&limit=100&cursor=matching2',
    )
    expect(wrapper.get('[role="alert"]').text()).toContain('Account status was refreshed')
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    expect(wrapper.text()).toContain(first.email)
    expect(wrapper.text()).not.toContain('prefixuser@example.com')
    expect(wrapper.get('#admin-action-U').text()).toBe('Restore')
    apiFetch.mockResolvedValueOnce({ users: [], next_cursor: null })
    await button(wrapper, 'Load more').trigger('click')
    await flushPromises()
    expect(apiFetch).toHaveBeenLastCalledWith('/api/admin/users?q=example&limit=50&cursor=page3')
    wrapper.unmount()
  })
  it('reconciles a successful save whose response cannot be parsed', async () => {
    const wrapper = await start()
    await button(wrapper, 'Disable').trigger('click')
    apiFetch
      .mockRejectedValueOnce(new ApiError(200, 'invalid_response', 'Non-JSON response.'))
      .mockResolvedValueOnce({
        users: [{ ...user, disabled_at: '2026-10-10T01:00:00Z' }],
        next_cursor: null,
      })
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(wrapper.get('#admin-action-U').text()).toBe('Restore')
    expect(wrapper.get('[role="alert"]').text()).toContain('Account status was refreshed')
    wrapper.unmount()
  })
  it('keeps a missing target uncertain and retries its identity lookup', async () => {
    const wrapper = await start()
    await button(wrapper, 'Disable').trigger('click')
    apiFetch
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ users: [], next_cursor: null })
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Account status is uncertain')
    expect(wrapper.text()).not.toContain('Account status was refreshed')
    apiFetch.mockResolvedValueOnce({ users: [user], next_cursor: null })
    await button(wrapper, 'Retry').trigger('click')
    await flushPromises()
    expect(apiFetch).toHaveBeenLastCalledWith('/api/admin/users?q=user%40example.com&limit=100')
    expect(wrapper.get('[role="alert"]').text()).toContain('Account status was refreshed')
    wrapper.unmount()
  })
  it('clears old-query accounts and confirmations even when submitted search fails', async () => {
    const wrapper = await start()
    await button(wrapper, 'Disable').trigger('click')
    await wrapper.get('input').setValue('different')
    apiFetch.mockRejectedValueOnce(new Error('offline'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(0)
    expect(wrapper.find('[aria-label="Confirm access change"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(user.email)
    wrapper.unmount()
  })
  it('cancels a stale confirmation before refreshing an externally changed account', async () => {
    const wrapper = await start()
    await button(wrapper, 'Disable').trigger('click')
    const refresh = deferred<components['schemas']['AdminUserList']>()
    apiFetch.mockReturnValueOnce(refresh.promise)
    await button(wrapper, 'Refresh').trigger('click')
    expect(wrapper.find('[aria-label="Confirm access change"]').exists()).toBe(false)
    refresh.resolve({
      users: [{ ...user, disabled_at: '2026-10-10T01:00:00Z' }],
      next_cursor: null,
    })
    await flushPromises()
    expect(wrapper.get('#admin-action-U').text()).toBe('Restore')
    await button(wrapper, 'Restore').trigger('click')
    expect(wrapper.get('[aria-label="Confirm access change"]').text()).toContain('Restore access')
    wrapper.unmount()
  })
  it('never offers disable for protected administrator rows', async () => {
    apiFetch.mockResolvedValue({ users: [{ ...user, is_admin: true }], next_cursor: null })
    const wrapper = await start()
    expect(wrapper.text()).toContain('Protected')
    expect(wrapper.findAll('button').some((node) => node.text() === 'Disable')).toBe(false)
    wrapper.unmount()
  })
})
