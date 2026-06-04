<script setup>
import { ref } from 'vue'
import { apiFetch } from '../composables/useApi'
import DiffStatBadge from './DiffStatBadge.vue'
import DiffView from './DiffView.vue'

const props = defineProps({
  file: { type: Object, required: true },
  repoId: { type: String, required: true },
})

const expanded = ref(false)
const diff = ref(null)
const loading = ref(false)
const error = ref(null)

const statusStyles = {
  modified: { letter: 'M', class: 'text-amber-400' },
  added: { letter: 'A', class: 'text-emerald-400' },
  untracked: { letter: 'U', class: 'text-emerald-400' },
  deleted: { letter: 'D', class: 'text-red-400' },
  renamed: { letter: 'R', class: 'text-sky-400' },
}

async function toggle() {
  expanded.value = !expanded.value
  if (!expanded.value) return
  // Refetch on every expand so the diff never goes stale between scans.
  loading.value = true
  error.value = null
  try {
    const res = await apiFetch(
      `/api/review-queue/${props.repoId}/diff?path=${encodeURIComponent(props.file.path)}`
    )
    diff.value = res.diff
  } catch (err) {
    diff.value = null
    error.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div>
    <button
      class="flex w-full items-center gap-3 rounded px-1 py-1 text-left transition-colors hover:bg-panel-2/60"
      @click="toggle"
    >
      <svg
        class="h-3 w-3 shrink-0 text-slate-500 transition-transform"
        :class="{ 'rotate-90': expanded }"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        viewBox="0 0 24 24"
      >
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
      </svg>
      <span
        class="w-4 shrink-0 text-center font-mono text-xs font-semibold"
        :class="(statusStyles[file.status] || statusStyles.modified).class"
        :title="file.status"
      >
        {{ (statusStyles[file.status] || statusStyles.modified).letter }}
      </span>
      <span class="min-w-0 flex-1 truncate font-mono text-xs text-slate-300" :title="file.path">
        {{ file.path }}
      </span>
      <DiffStatBadge :additions="file.additions" :deletions="file.deletions" />
    </button>

    <div v-if="expanded" class="mb-2 ml-7 mt-1">
      <p v-if="loading" class="py-2 text-xs text-slate-500">Loading diff&hellip;</p>
      <p v-else-if="error" class="py-2 text-xs text-red-400">{{ error }}</p>
      <DiffView v-else-if="diff" :diff="diff" />
    </div>
  </div>
</template>
