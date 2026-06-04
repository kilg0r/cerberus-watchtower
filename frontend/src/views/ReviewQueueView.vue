<script setup>
import { computed, ref } from 'vue'
import { apiFetch, usePolling } from '../composables/useApi'
import RepoCard from '../components/RepoCard.vue'
import EmptyState from '../components/EmptyState.vue'

const queue = ref(null) // null until first load completes
const backendDown = ref(false)
const lastScanned = ref(null)
const now = ref(Date.now())

const GROUP_LABELS = { paytable: 'PayTable', cerberus: 'Cerberus', personal: 'Personal' }

const groups = computed(() => {
  if (!queue.value) return []
  const byGroup = new Map()
  for (const entry of queue.value) {
    if (!byGroup.has(entry.group)) byGroup.set(entry.group, [])
    byGroup.get(entry.group).push(entry)
  }
  return [...byGroup.entries()].map(([key, entries]) => ({
    key,
    label: GROUP_LABELS[key] || key,
    entries,
  }))
})

const scannedAgo = computed(() => {
  if (!lastScanned.value) return null
  const seconds = Math.max(0, Math.round((now.value - lastScanned.value) / 1000))
  return seconds < 60 ? `${seconds}s ago` : `${Math.round(seconds / 60)}m ago`
})

async function refresh() {
  try {
    queue.value = await apiFetch('/api/review-queue')
    backendDown.value = false
    lastScanned.value = Date.now()
  } catch (err) {
    if (err.message === 'backend-unreachable') backendDown.value = true
  }
  now.value = Date.now()
}

usePolling(refresh, 30_000)
usePolling(() => (now.value = Date.now()), 10_000)
</script>

<template>
  <div>
    <header class="mb-6 flex items-end justify-between">
      <div>
        <h1 class="text-xl font-semibold text-white">Review Queue</h1>
        <p class="mt-1 text-sm text-slate-500">Uncommitted work across registered repos</p>
      </div>
      <p v-if="scannedAgo" class="text-xs text-slate-500">last scanned {{ scannedAgo }}</p>
    </header>

    <div
      v-if="backendDown"
      class="mb-6 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300"
    >
      Backend not running on :8765 - start it with
      <code class="font-mono text-xs">python -m watchtower</code>
    </div>

    <!-- first-load skeleton -->
    <div v-if="queue === null && !backendDown" class="space-y-4">
      <div v-for="i in 3" :key="i" class="h-32 animate-pulse rounded-lg border border-edge bg-panel/50" />
    </div>

    <EmptyState v-else-if="queue !== null && queue.length === 0" />

    <!-- side-by-side group columns on wide screens (1080p+) -->
    <div v-else-if="queue !== null" class="grid items-start gap-8 2xl:grid-cols-2">
      <section v-for="group in groups" :key="group.key">
        <h2 class="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
          {{ group.label }}
        </h2>
        <div class="space-y-4">
          <RepoCard v-for="entry in group.entries" :key="entry.repo_id" :entry="entry" />
        </div>
      </section>
    </div>
  </div>
</template>
