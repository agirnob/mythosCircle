<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { components } from '../api/schema'
import { ApiError } from '../api/client'
import PageHeader from '../components/ui/PageHeader.vue'
import SectionHeader from '../components/ui/SectionHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
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
  <div class="mc-worlds-page">
    <p class="mc-eyebrow">Campaign library</p>
    <PageHeader
      title="Your worlds"
      description="Choose a campaign to continue, or start a new one."
    />

    <form class="create mc-create-world" @submit.prevent="create">
      <div class="mc-create-intro">
        <p class="mc-eyebrow">A new story</p>
        <h2 class="mc-display-title">Begin with a world.</h2>
        <p>
          Give it a name and a little direction. You can shape its places, factions, and people
          next.
        </p>
      </div>
      <div class="mc-create-fields">
        <label
          >World name
          <input
            v-model="title"
            class="mc-input"
            type="text"
            required
            placeholder="The Shattered Coast"
          />
        </label>
        <label
          >Theme
          <select v-model="theme" class="mc-input" required>
            <option value="" disabled>Choose a theme…</option>
            <option v-for="option in campaigns.themes" :key="option" :value="option">
              {{ option }}
            </option>
          </select>
        </label>
        <label
          >Description
          <span class="mc-field-hint">The short pitch you will see on your world.</span>
          <textarea
            v-model="description"
            class="mc-textarea"
            rows="2"
            placeholder="What kind of world is this?"
          ></textarea>
        </label>
        <label
          >Custom lore <span class="mc-field-hint">Optional details to guide generation.</span>
          <textarea
            v-model="customLore"
            class="mc-textarea"
            rows="3"
            placeholder="Secrets, history, or a conflict worth exploring."
          ></textarea>
        </label>
        <p v-if="themeError" class="error">{{ themeError }}</p>
        <p v-if="createError" class="error">{{ createError }}</p>
        <button type="submit" class="mc-btn" :disabled="creating">
          {{ creating ? 'Creating…' : 'Create world →' }}
        </button>
      </div>
    </form>

    <section class="mc-library" aria-label="Saved worlds">
      <SectionHeader title="Continue a world" :meta="`${campaigns.campaigns.length} saved`" />
      <p v-if="campaigns.loading" class="muted">Loading worlds…</p>
      <p v-else-if="loadError" class="error">{{ loadError }}</p>
      <div v-else-if="campaigns.campaigns.length === 0" class="mc-library-empty">
        No worlds yet. Create your first one above.
      </div>
      <ul v-else class="worlds">
        <li v-for="campaign in campaigns.campaigns" :key="campaign.id" class="world">
          <div class="mc-world-card-main">
            <StatusBadge variant="neutral">{{ campaign.theme }}</StatusBadge>
            <h3 class="mc-display-title">{{ campaign.title }}</h3>
            <p>{{ campaign.description || 'A world ready for its first story.' }}</p>
          </div>
          <div class="mc-world-card-actions">
            <RouterLink :to="{ name: 'overview', params: { id: campaign.id } }" class="mc-btn"
              >Open overview →</RouterLink
            >
            <RouterLink
              :to="{ name: 'build-in', params: { id: campaign.id } }"
              class="mc-btn mc-btn-secondary"
              >Guided build</RouterLink
            >
            <button
              v-if="confirmingId !== campaign.id"
              type="button"
              class="mc-delete-trigger"
              :disabled="deletingId !== null"
              @click="startDelete(campaign)"
            >
              Delete
            </button>
          </div>
          <form
            v-if="confirmingId === campaign.id"
            class="delete-world"
            @submit.prevent="confirmDelete(campaign)"
          >
            <label
              >Type “{{ campaign.title }}” to confirm
              <input
                v-model="confirmTitle"
                class="mc-input"
                type="text"
                :aria-label="`Type ${campaign.title} to confirm deletion`"
              />
            </label>
            <p class="muted small">
              Deletes the world and everything in it — revisions, entities, relations, jobs and
              media. This cannot be undone.
            </p>
            <p class="actions">
              <button type="submit" class="mc-btn" :disabled="deletingId === campaign.id">
                {{ deletingId === campaign.id ? 'Deleting…' : 'Delete world' }}
              </button>
              <button
                type="button"
                class="mc-btn mc-btn-secondary"
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
  </div>
</template>

<style scoped>
.mc-worlds-page {
  max-width: 1140px;
}
.mc-eyebrow {
  margin: 0 0 0.45rem;
  color: var(--mc-interactive-bright);
  font-size: var(--mc-meta-size);
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.mc-create-world {
  display: grid;
  grid-template-columns: minmax(210px, 0.8fr) minmax(0, 1.3fr);
  gap: 2rem;
  padding: 1.5rem;
  border: 1px solid var(--mc-border-bright);
  border-radius: var(--mc-radius);
  background:
    radial-gradient(circle at 0% 0%, rgba(139, 108, 255, 0.14), transparent 24rem),
    var(--mc-surface);
}
.mc-create-intro h2 {
  margin: 0 0 0.75rem;
  font-size: clamp(1.45rem, 3vw, 2rem);
  line-height: 1.1;
}
.mc-create-intro > p:last-child {
  max-width: 30ch;
  color: var(--mc-text-secondary);
}
.mc-create-fields {
  display: grid;
  gap: 0.85rem;
}
.mc-create-fields label {
  display: grid;
  gap: 0.35rem;
  color: var(--mc-text-primary);
  font-weight: 600;
}
.mc-create-fields .mc-input,
.mc-create-fields .mc-textarea {
  font-weight: 400;
}
.mc-field-hint {
  color: var(--mc-text-muted);
  font-size: 0.8rem;
  font-weight: 400;
}
.mc-create-fields button {
  justify-self: start;
}
.mc-library {
  margin-top: 2.75rem;
}
.worlds {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.85rem;
}
.mc-library-empty {
  padding: 2rem;
  border: 1px dashed var(--mc-border-bright);
  border-radius: var(--mc-radius);
  color: var(--mc-text-secondary);
  text-align: center;
}
.world {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 1rem;
  flex-wrap: wrap;
  padding: 1.25rem;
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  background: var(--mc-surface-raised);
}
.mc-world-card-main {
  min-width: 0;
  flex: 1 1 230px;
}
.mc-world-card-main h3 {
  margin: 0.65rem 0 0.25rem;
  font-size: 1.5rem;
  line-height: 1.2;
}
.mc-world-card-main p {
  margin: 0;
  color: var(--mc-text-secondary);
}
.mc-world-card-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.mc-delete-trigger {
  padding: 0.45rem;
  border: 0;
  background: transparent;
  color: var(--mc-text-muted);
  cursor: pointer;
}
.mc-delete-trigger:hover {
  color: var(--mc-danger);
}
.delete-world {
  flex-basis: 100%;
  display: grid;
  gap: 0.7rem;
  margin-top: 0.75rem;
  padding-top: 1rem;
  border-top: 1px solid var(--mc-border);
}
.delete-world label {
  display: grid;
  gap: 0.35rem;
  max-width: 30rem;
}
.delete-world .actions {
  display: flex;
  gap: 0.5rem;
  margin: 0;
}
@media (max-width: 900px) {
  .mc-create-world {
    grid-template-columns: 1fr;
    gap: 1rem;
  }
}
@media (max-width: 560px) {
  .mc-create-world {
    padding: 1rem;
  }
  .mc-world-card-actions {
    width: 100%;
  }
  .mc-world-card-actions .mc-btn {
    flex: 1 1 auto;
    text-align: center;
  }
}
</style>
