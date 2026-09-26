<script setup lang="ts">
/**
 * Entity card (§14) — portrait, name, type meta, short description,
 * relationship info, one primary action. Secondary actions belong in the
 * `menu` slot (contextual), never as a row of export links on the card.
 */
defineProps<{
  name: string
  meta?: string
  description?: string
  relationInfo?: string
  portraitUrl?: string | null
}>()
</script>

<template>
  <article class="mc-entity-card">
    <img v-if="portraitUrl" :src="portraitUrl" :alt="`Portrait of ${name}`" class="mc-entity-portrait" />
    <div v-else class="mc-entity-portrait mc-entity-portrait-fallback" aria-hidden="true">
      {{ name.charAt(0).toUpperCase() }}
    </div>
    <div class="mc-entity-body">
      <h3 class="mc-entity-name">{{ name }}</h3>
      <p v-if="meta" class="mc-entity-meta">{{ meta }}</p>
      <p v-if="description" class="mc-entity-description">{{ description }}</p>
      <p v-if="relationInfo" class="mc-entity-relations">{{ relationInfo }}</p>
      <div class="mc-entity-footer">
        <span class="mc-entity-primary">
          <slot name="primary" />
        </span>
        <span v-if="$slots.menu" class="mc-entity-menu">
          <slot name="menu" />
        </span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.mc-entity-card {
  background: var(--mc-surface);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.mc-entity-portrait {
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  background: var(--mc-surface-raised);
}
.mc-entity-portrait-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2.5rem;
  color: var(--mc-text-muted);
}
.mc-entity-body {
  padding: 0.9rem 1rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.mc-entity-name {
  margin: 0;
  font-size: var(--mc-entity-name-size);
  font-weight: 650;
}
.mc-entity-meta {
  margin: 0;
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-entity-description {
  margin: 0.25rem 0 0;
  font-size: var(--mc-body-size);
  color: var(--mc-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.mc-entity-relations {
  margin: 0.25rem 0 0;
  font-size: 0.85rem;
  color: var(--mc-text-muted);
}
.mc-entity-footer {
  margin-top: 0.6rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--mc-gap-sm);
}
</style>
