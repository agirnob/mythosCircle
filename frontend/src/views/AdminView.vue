<script setup lang="ts">
/* global HTMLButtonElement, URLSearchParams, Event */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError, apiFetch } from '../api/client'
import type { components } from '../api/schema'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
import { useAuthStore } from '../stores/auth'

type AdminUser = components['schemas']['AdminUser']
type UserPage = components['schemas']['AdminUserList']
const auth = useAuthStore()
const router = useRouter()
const users = ref<AdminUser[]>([])
const cursor = ref<string | null>(null)
const search = ref('')
const query = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const confirmation = ref<AdminUser | null>(null)
const uncertainTarget = ref<AdminUser | null>(null)
const confirmButton = ref<HTMLButtonElement | null>(null)
let trigger: HTMLButtonElement | null = null
let generation = 0
let denied = false
let retryMore = false

function clear() {
  generation++
  users.value = []
  cursor.value = null
  confirmation.value = null
  uncertainTarget.value = null
  loading.value = false
  saving.value = false
  error.value = ''
  trigger = null
}
async function accessLost() {
  denied = true
  clear()
  auth.denyAdmin()
  try {
    await auth.hydrate()
  } catch {
    auth.denyAdmin()
  }
  await router.replace({ name: 'campaigns', query: { notice: 'admin-access-denied' } })
}
function message(err: unknown) {
  return err instanceof ApiError ? err.message : 'Could not reach the server. Please retry.'
}
async function load(more = false) {
  if (denied) return
  const version = ++generation
  const next = more ? cursor.value : null
  if (!more) confirmation.value = null
  loading.value = true
  if (!uncertainTarget.value) error.value = ''
  retryMore = more
  try {
    const params = new URLSearchParams({ q: query.value, limit: '50' })
    if (next) params.set('cursor', next)
    const page = await apiFetch<UserPage>(`/api/admin/users?${params}`)
    if (version !== generation || denied) return
    users.value = [
      ...new Map(
        (more ? [...users.value, ...page.users] : page.users).map((user) => [user.id, user]),
      ).values(),
    ]
    cursor.value = page.next_cursor
    if (page.users.some((row) => row.id === uncertainTarget.value?.id)) {
      uncertainTarget.value = null
      error.value = ''
    }
  } catch (err) {
    if (denied) return
    if (err instanceof ApiError && err.status === 403) {
      await accessLost()
      return
    }
    if (version !== generation) return
    error.value = message(err)
  } finally {
    if (version === generation) loading.value = false
  }
}
function submitSearch() {
  if (saving.value) return
  users.value = []
  uncertainTarget.value = null
  query.value = search.value.trim().toLowerCase()
  cursor.value = null
  confirmation.value = null
  void load()
}
async function ask(user: AdminUser, event: Event) {
  confirmation.value = user
  trigger = event.currentTarget as HTMLButtonElement
  await nextTick()
  confirmButton.value?.focus()
}
async function cancel() {
  confirmation.value = null
  await nextTick()
  if (trigger) trigger.ownerDocument.getElementById(trigger.id)?.focus()
}
async function reconcile(user: AdminUser, version: number, failure: string) {
  uncertainTarget.value = user
  try {
    let next: string | null = null
    do {
      const params = new URLSearchParams({ q: user.email, limit: '100' })
      if (next) params.set('cursor', next)
      const page = await apiFetch<UserPage>(`/api/admin/users?${params}`)
      if (version !== generation || denied) return
      const updated = page.users.find((row) => row.id === user.id)
      if (updated) {
        users.value = users.value.map((row) => (row.id === updated.id ? updated : row))
        uncertainTarget.value = null
        error.value = `${failure} Account status was refreshed; retry if needed.`
        return
      }
      next = page.next_cursor
    } while (next)
    error.value = `${failure} Account status is uncertain: the account was not returned by the server.`
  } catch (err) {
    if (denied) return
    if (err instanceof ApiError && err.status === 403) {
      await accessLost()
      return
    }
    if (version === generation)
      error.value = `${failure} Refresh also failed. Account status is uncertain. ${message(err)}`
  }
}
async function retry() {
  if (loading.value || saving.value || denied) return
  const target = uncertainTarget.value
  if (!target) {
    await load(retryMore)
    return
  }
  saving.value = true
  try {
    await reconcile(target, ++generation, 'The previous save could not be confirmed.')
  } finally {
    if (!denied) saving.value = false
  }
}
async function confirm() {
  const user = confirmation.value
  if (!user || saving.value || denied) return
  const version = ++generation
  uncertainTarget.value = null
  saving.value = true
  loading.value = false
  error.value = ''
  try {
    const updated = await apiFetch<AdminUser>(`/api/admin/users/${user.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ disabled: user.disabled_at === null }),
    })
    if (version !== generation || denied) return
    users.value = users.value.map((row) => (row.id === updated.id ? updated : row))
    await cancel()
  } catch (err) {
    if (denied) return
    if (err instanceof ApiError && err.status === 403) {
      await accessLost()
      return
    }
    if (version !== generation) return
    const failure = message(err)
    confirmation.value = null
    // A transport, server or response-parse failure may follow a committed save.
    // Resolve this exact account without changing the table's query or cursor.
    if (!(err instanceof ApiError) || err.status >= 500 || err.code === 'invalid_response')
      await reconcile(user, version, failure)
    else error.value = failure
  } finally {
    if (!denied) {
      saving.value = false
      await nextTick()
      if (trigger) trigger.ownerDocument.getElementById(trigger.id)?.focus()
    }
  }
}
watch(
  () => auth.account?.id,
  () => {
    denied = true
    clear()
  },
  { flush: 'sync' },
)
watch(
  () => auth.isAdmin,
  (value) => {
    if (!value) {
      denied = true
      clear()
    }
  },
  { flush: 'sync' },
)
onMounted(() => {
  if (auth.isAdmin) void load()
})
onBeforeUnmount(() => {
  denied = true
  clear()
})
function date(value: string) {
  return new Date(value).toLocaleString()
}
</script>

<template>
  <section class="admin-page">
    <PageHeader title="User access" description="Inspect registered accounts and manage access." />
    <form class="admin-search" @submit.prevent="submitSearch">
      <label for="admin-email">Search email</label>
      <input id="admin-email" v-model="search" class="mc-input" type="search" maxlength="320" />
      <button class="mc-btn" :disabled="saving">Search</button>
      <button
        type="button"
        class="mc-btn mc-btn-secondary"
        :disabled="loading || saving"
        @click="load()"
      >
        Refresh
      </button>
    </form>
    <div
      v-if="confirmation"
      class="admin-confirm"
      role="region"
      aria-label="Confirm access change"
      @keydown.esc="!saving && cancel()"
    >
      <p id="admin-confirm-target">
        {{ confirmation.disabled_at ? 'Restore' : 'Disable' }} access for
        <strong>{{ confirmation.email }}</strong
        >?
      </p>
      <p v-if="!confirmation.disabled_at" id="admin-confirm-consequence">
        This signs out the account and closes its live connections. Previously signed portrait links
        remain valid until expiry (up to seven days), and queued or running work continues.
      </p>
      <p v-else id="admin-confirm-consequence">
        The user must sign in again. Previous sessions remain revoked.
      </p>
      <button
        ref="confirmButton"
        type="button"
        class="mc-btn"
        :disabled="saving"
        aria-describedby="admin-confirm-target admin-confirm-consequence"
        @click="confirm"
      >
        {{ saving ? 'Saving…' : 'Confirm' }}
      </button>
      <button type="button" class="mc-btn mc-btn-secondary" :disabled="saving" @click="cancel">
        Cancel
      </button>
    </div>
    <p v-if="error" role="alert">
      {{ error }}
      <button
        type="button"
        class="mc-btn mc-btn-secondary"
        :disabled="loading || saving"
        @click="retry"
      >
        Retry
      </button>
    </p>
    <p v-if="loading" role="status">Loading users…</p>
    <p v-else-if="!users.length && !error">No matching accounts.</p>
    <div
      v-if="users.length"
      class="admin-table"
      role="region"
      aria-label="User accounts"
      tabindex="0"
    >
      <table>
        <caption class="sr-only">
          Registered user access and campaign counts
        </caption>
        <thead>
          <tr>
            <th scope="col">Email</th>
            <th scope="col">Access</th>
            <th scope="col">Administrator</th>
            <th scope="col">Created</th>
            <th scope="col">Campaigns</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users" :key="user.id">
            <th scope="row">{{ user.email }}</th>
            <td>
              <StatusBadge :variant="user.disabled_at ? 'neutral' : 'canon'">{{
                user.disabled_at ? 'Disabled' : 'Active'
              }}</StatusBadge>
            </td>
            <td>{{ user.is_admin ? 'Yes' : 'No' }}</td>
            <td>
              <time :datetime="user.created_at">{{ date(user.created_at) }}</time>
            </td>
            <td>{{ user.campaign_count }}</td>
            <td>
              <button
                v-if="!user.is_admin || user.disabled_at"
                :id="`admin-action-${user.id}`"
                :aria-label="`${user.disabled_at ? 'Restore' : 'Disable'} access for ${user.email}`"
                type="button"
                class="mc-btn mc-btn-secondary"
                :disabled="loading || saving || confirmation !== null"
                @click="ask(user, $event)"
              >
                {{ user.disabled_at ? 'Restore' : 'Disable' }}</button
              ><span v-else>Protected</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <button
      v-if="cursor"
      type="button"
      class="mc-btn mc-btn-secondary"
      :disabled="loading || saving"
      @click="load(true)"
    >
      Load more
    </button>
  </section>
</template>

<style scoped>
.admin-page {
  max-width: 1200px;
}
.admin-search {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}
.admin-search input {
  flex: 1 1 240px;
}
.admin-table {
  overflow-x: auto;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  margin: 1rem 0;
}
table {
  border-collapse: collapse;
  width: 100%;
  text-align: left;
}
th,
td {
  padding: 0.85rem;
  border-bottom: 1px solid var(--mc-border);
  white-space: nowrap;
}
thead {
  background: var(--mc-surface-raised);
  color: var(--mc-text-secondary);
}
.admin-confirm {
  padding: 1rem;
  border: 1px solid var(--mc-border-bright);
  background: var(--mc-surface-raised);
  border-radius: var(--mc-radius);
}
.admin-confirm button + button {
  margin-left: 0.5rem;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}
</style>
