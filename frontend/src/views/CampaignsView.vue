<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { components } from '../api/schema'
import { ApiError } from '../api/client'
import { useCampaignsStore } from '../stores/campaigns'

type Campaign = components['schemas']['CampaignResponse']

const campaigns = useCampaignsStore()
const loadError = ref<string | null>(null)

const title = ref('')
const theme = ref('')
const description = ref('')
const customLore = ref('')
const createError = ref<string | null>(null)
const creating = ref(false)
const themeError = ref<string | null>(null)

async function create() {
  createError.value = null
  creating.value = true
  try {
    await campaigns.create({
      title: title.value.trim(),
      theme: theme.value.trim(),
      description: description.value.trim(),
      custom_lore: customLore.value.trim(),
    })
    title.value = ''
    theme.value = ''
    description.value = ''
    customLore.value = ''
  } catch (err) {
    createError.value = err instanceof ApiError ? err.message : 'Could not create the world.'
  } finally {
    creating.value = false
  }
}

/**
 * AR20 total hard delete (spec-1.6): the server gates it on an explicit
 * ``{"confirm": true}`` body, and the DM retypes the world's title so the
 * destructive action names exactly one world. The list refetches on
 * success; a failure leaves the row in place with its error inline.
 */
const confirmingId = ref<string | null>(null)
const confirmTitle = ref('')
const deletingId = ref<string | null>(null)
const deleteError = ref<string | null>(null)

function startDelete(campaign: Campaign) {
  confirmingId.value = campaign.id
  confirmTitle.value = ''
  deleteError.value = null
}

function cancelDelete() {
  confirmingId.value = null
  confirmTitle.value = ''
  deleteError.value = null
}

async function confirmDelete(campaign: Campaign) {
  if (deletingId.value) return
  if (confirmTitle.value.trim() !== campaign.title) {
    deleteError.value = 'The title did not match — nothing was deleted.'
    return
  }
  deletingId.value = campaign.id
  deleteError.value = null
  try {
    await campaigns.remove(campaign.id)
    // remove() refetches; a failed refetch leaves the row (and the form)
    // behind, so say which half failed rather than reporting a clean delete.
    if (campaigns.error) {
      deleteError.value = `The world was deleted, but the list could not be refreshed. (${campaigns.error})`
      return
    }
    cancelDelete()
  } catch (err) {
    deleteError.value = err instanceof ApiError ? err.message : 'Could not delete the world.'
  } finally {
    deletingId.value = null
  }
}

onMounted(async () => {
  try {
    await campaigns.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load your worlds.'
  }
  if (campaigns.error) {
    loadError.value = campaigns.error
  }
  // The theme picker's source (dogfood fix 2026-09-09): the store
  // validates against this seed list server-side, so the form must offer
  // exactly these — free text was the wrong affordance.
  try {
    await campaigns.fetchThemes()
  } catch (err) {
    themeError.value = err instanceof ApiError ? err.message : 'Could not load the themes.'
  }
})
</script>

<template>
  <section>
    <h1>Your worlds</h1>

    <form class="card create" @submit.prevent="create">
      <h2>Create a world</h2>
      <label>
        Title
        <input v-model="title" type="text" required placeholder="The Shattered Coast" />
      </label>
      <label>
        Theme
        <select v-model="theme" required>
          <option value="" disabled>Choose a theme…</option>
          <option v-for="option in campaigns.themes" :key="option" :value="option">
            {{ option }}
          </option>
        </select>
      </label>
      <label>
        Description
        <textarea
          v-model="description"
          rows="2"
          placeholder="What kind of world is this?"
        ></textarea>
      </label>
      <label>
        Custom lore
        <textarea
          v-model="customLore"
          rows="3"
          placeholder="Secrets, history, hooks to seed the world."
        ></textarea>
      </label>
      <p v-if="themeError" class="error">{{ themeError }}</p>
      <p v-if="createError" class="error">{{ createError }}</p>
      <button type="submit" :disabled="creating">
        {{ creating ? 'Creating…' : 'Create world' }}
      </button>
    </form>

    <p v-if="campaigns.loading" class="muted">Loading…</p>
    <p v-else-if="loadError" class="error">{{ loadError }}</p>
    <p v-else-if="campaigns.campaigns.length === 0" class="muted">
      No worlds yet — create your first one above.
    </p>
    <ul v-else class="worlds">
      <li v-for="campaign in campaigns.campaigns" :key="campaign.id" class="card world">
        <div>
          <strong>{{ campaign.title }}</strong>
          <p class="muted">{{ campaign.description || 'No description.' }}</p>
          <p class="muted">Theme: {{ campaign.theme }}</p>
        </div>
        <RouterLink :to="{ name: 'build-in', params: { id: campaign.id } }" class="cta">
          Open build-in
        </RouterLink>
        <RouterLink :to="{ name: 'world', params: { id: campaign.id } }" class="cta secondary">
          Open world
        </RouterLink>
        <button
          v-if="confirmingId !== campaign.id"
          type="button"
          class="cta secondary"
          :disabled="deletingId !== null"
          @click="startDelete(campaign)"
        >
          Delete
        </button>
        <!-- AR20 total hard delete: the typed title is the UI-side gate that
             names exactly one world (the server additionally requires the
             explicit {"confirm": true} body). -->
        <form v-else class="delete-world" @submit.prevent="confirmDelete(campaign)">
          <label>
            Type “{{ campaign.title }}” to confirm
            <input
              v-model="confirmTitle"
              type="text"
              :aria-label="`Type ${campaign.title} to confirm deletion`"
            />
          </label>
          <p class="muted small">
            Deletes the world and everything in it — revisions, entities, relations, jobs and media
            files. This cannot be undone.
          </p>
          <p class="actions">
            <button type="submit" :disabled="deletingId === campaign.id">
              {{ deletingId === campaign.id ? 'Deleting…' : 'Delete world' }}
            </button>
            <button
              type="button"
              class="cta secondary"
              :disabled="deletingId === campaign.id"
              @click="cancelDelete"
            >
              Cancel
            </button>
          </p>
          <p v-if="deleteError" class="error">{{ deleteError }}</p>
        </form>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.worlds {
  list-style: none;
  padding: 0;
}
.create {
  display: grid;
  gap: 0.75rem;
  max-width: 36rem;
}
.create h2 {
  margin: 0;
}
label {
  display: grid;
  gap: 0.25rem;
}
input,
textarea {
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px solid #2c3038;
  background: #14161a;
  color: inherit;
}
textarea {
  resize: vertical;
}
.world {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  /* The typed-title confirmation is a full-width row under the world's
     title/actions instead of a squeezed flex child. */
  flex-wrap: wrap;
}
.delete-world {
  flex-basis: 100%;
  display: grid;
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.delete-world .actions {
  display: flex;
  gap: 0.5rem;
  margin: 0;
}
.world p {
  margin: 0.25rem 0;
}
</style>
