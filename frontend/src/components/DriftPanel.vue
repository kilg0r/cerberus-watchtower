<script setup>
import { computed } from 'vue'

const props = defineProps({
  drift: { type: Object, required: true },
})

const sections = computed(() => {
  const c = props.drift.changes
  if (!c) return []
  const defs = [
    { key: 'added_projects', label: 'Projects added', class: 'text-emerald-400', prefix: '+' },
    { key: 'removed_projects', label: 'Projects removed', class: 'text-red-400', prefix: '−' },
    { key: 'added_edges', label: 'New dependency edges', class: 'text-amber-400', prefix: '+' },
    { key: 'removed_edges', label: 'Removed dependency edges', class: 'text-slate-400', prefix: '−' },
    { key: 'added_endpoints', label: 'New endpoints', class: 'text-emerald-400', prefix: '+' },
    { key: 'removed_endpoints', label: 'Removed endpoints', class: 'text-red-400', prefix: '−' },
    { key: 'added_routes', label: 'New routes', class: 'text-emerald-400', prefix: '+' },
    { key: 'removed_routes', label: 'Removed routes', class: 'text-red-400', prefix: '−' },
  ]
  return defs
    .filter((d) => c[d.key]?.length)
    .map((d) => ({ ...d, items: c[d.key] }))
})

const packageChanges = computed(() => props.drift.changes?.package_changes || [])
const statsDelta = computed(() => Object.entries(props.drift.changes?.stats_delta || {}))

const when = (iso) => (iso ? new Date(iso).toLocaleString() : '?')
</script>

<template>
  <div class="rounded-lg border border-amber-500/30 bg-panel p-4">
    <p class="text-xs text-slate-400">
      Architecture changed between
      <span class="text-slate-300">{{ when(drift.previous_at) }}</span> and
      <span class="text-slate-300">{{ when(drift.latest_at) }}</span>
      <span class="ml-2 text-slate-600">({{ drift.snapshot_count }} snapshots recorded)</span>
    </p>

    <div class="mt-3 grid gap-4 lg:grid-cols-2">
      <div v-for="section in sections" :key="section.key">
        <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
          {{ section.label }} ({{ section.items.length }})
        </p>
        <ul class="mt-1 max-h-40 overflow-auto">
          <li
            v-for="item in section.items"
            :key="item"
            class="py-0.5 font-mono text-xs"
            :class="section.class"
          >
            {{ section.prefix }} {{ item }}
          </li>
        </ul>
      </div>

      <div v-if="packageChanges.length">
        <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
          Package changes ({{ packageChanges.length }})
        </p>
        <ul class="mt-1 max-h-40 overflow-auto">
          <li v-for="(c, i) in packageChanges" :key="i" class="py-0.5 font-mono text-xs text-slate-300">
            {{ c.package }}
            <span v-if="c.project" class="text-slate-600">({{ c.project }})</span>
            <span class="text-slate-500">{{ c.from || 'added' }} &rarr; {{ c.to || 'removed' }}</span>
          </li>
        </ul>
      </div>

      <div v-if="statsDelta.length">
        <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Stats</p>
        <ul class="mt-1">
          <li v-for="[key, d] in statsDelta" :key="key" class="py-0.5 font-mono text-xs text-slate-300">
            {{ key }}: {{ d.from ?? 0 }} &rarr; {{ d.to }}
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>
