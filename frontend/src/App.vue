<script setup lang="ts">
import { useCounterStore } from './stores/counter'

const counter = useCounterStore()
</script>

<template>
  <main>
    <h1>mythosCircle</h1>
    <p>The forge is warming.</p>
    <button type="button" @click="counter.increment()">Count: {{ counter.count }}</button>
  </main>
</template>
