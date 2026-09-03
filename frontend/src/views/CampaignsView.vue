<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '../api/client'
import { useCampaignsStore } from '../stores/campaigns'

const campaigns = useCampaignsStore()
const loadError = ref<string | null>(null)

const title = ref('')
const theme = ref('')
const description = ref('')
const customLore = ref('')
const createError = ref<string | null>(null)
const creating = ref(false)

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

onMounted(async () => {
  try {
    await campaigns.list()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : 'Could not load your worlds.'
  }
  if (campaigns.error) {
    loadError.value = campaigns.error
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
        <input v-model="theme" type="text" required placeholder="Dark fantasy heist" />
      </label>
      <label>
        Description
        <textarea v-model="description" rows="2" placeholder="What kind of world is this?"></textarea>
      </label>
      <label>
        Custom lore
        <textarea v-model="customLore" rows="3" placeholder="Secrets, history, hooks to seed the world."></textarea>
      </label>
      <p v-if="createError" class="error">{{ createError }}</p>
      <button type="submit" :disabled="creating">{{ creating ? 'Creating…' : 'Create world' }}</button>
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
}
.world p {
  margin: 0.25rem 0;
}
</style>
