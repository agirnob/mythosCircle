// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import { useAuthStore } from '../../stores/auth'
import AppShell from './AppShell.vue'
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: {} }),
  useRouter: () => ({ push: vi.fn() }),
  RouterLink: { template: '<a><slot /></a>' },
}))
it('shows administrator navigation only while capability is present', async () => {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.account = { id: 'A', email: 'a@example.com', is_admin: false }
  const wrapper = mount(AppShell)
  expect(wrapper.text()).not.toContain('Admin users')
  auth.account = { ...auth.account, is_admin: true }
  await wrapper.vm.$nextTick()
  expect(wrapper.text()).toContain('Admin users')
  auth.denyAdmin()
  await wrapper.vm.$nextTick()
  expect(wrapper.text()).not.toContain('Admin users')
})
