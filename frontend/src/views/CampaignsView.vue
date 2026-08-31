<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '../api/client'
import { useCampaignsStore } from '../stores/campaigns'

const campaigns = useCampaignsStore()
const loadError = ref<string | null>(null)

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
    <p v-if="campaigns.loading" class="muted">Loading…</p>
    <p v-else-if="loadError" class="error">{{ loadError }}</p>
    <p v-else-if="campaigns.campaigns.length === 0" class="muted">
      No worlds yet. Create one from the campaigns API (story 2.1 ships the build-in surface).
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
      </li>
    </ul>
  </section>
</template>

<style scoped>
.worlds {
  list-style: none;
  padding: 0;
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
.cta {
  white-space: nowrap;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: #2f6feb;
  color: #fff;
  text-decoration: none;
}
</style>
