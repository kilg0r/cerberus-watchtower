<script setup>
import { ref } from 'vue'
import { apiFetch } from '../composables/useApi'
import FileChangeRow from './FileChangeRow.vue'
import DiffStatBadge from './DiffStatBadge.vue'

const props = defineProps({
  entry: { type: Object, required: true },
})

const summary = ref(null)
const summarizing = ref(false)
const summaryError = ref(null)

async function summarize() {
  summarizing.value = true
  summaryError.value = null
  try {
    summary.value = await apiFetch(`/api/review-queue/${props.entry.repo_id}/summarize`, {
      method: 'POST',
    })
  } catch (err) {
    summaryError.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    summarizing.value = false
  }
}
</script>

<template>
  <article class="rounded-lg border border-edge bg-panel p-5">
    <header class="flex flex-wrap items-center gap-3">
      <h3 class="text-sm font-semibold text-white">{{ entry.name }}</h3>
      <span class="rounded-full border border-edge bg-panel-2 px-2.5 py-0.5 font-mono text-xs text-slate-300">
        {{ entry.branch }}
      </span>
      <span v-if="entry.ahead > 0" class="font-mono text-xs text-emerald-400" title="commits ahead of upstream">
        &uarr;{{ entry.ahead }}
      </span>
      <span v-if="entry.behind > 0" class="font-mono text-xs text-amber-400" title="commits behind upstream">
        &darr;{{ entry.behind }}
      </span>
      <div class="ml-auto">
        <DiffStatBadge :additions="entry.total_additions" :deletions="entry.total_deletions" />
      </div>
    </header>

    <p v-if="entry.last_commit" class="mt-1.5 truncate text-xs text-slate-500">
      <span class="font-mono">{{ entry.last_commit.hash }}</span>
      <span class="mx-1.5">&middot;</span>
      {{ entry.last_commit.message }}
    </p>

    <div class="mt-4 divide-y divide-edge/50 border-t border-edge/50 pt-2">
      <FileChangeRow
        v-for="file in entry.files"
        :key="file.path"
        :file="file"
        :repo-id="entry.repo_id"
      />
    </div>

    <footer class="mt-4">
      <button
        class="inline-flex items-center gap-2 rounded-md border border-coral/40 px-3 py-1.5 text-xs font-medium text-coral transition-colors hover:bg-coral/10 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="summarizing"
        @click="summarize"
      >
        <svg v-if="summarizing" class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" />
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z" />
        </svg>
        {{ summarizing ? 'Summarizing&hellip;' : 'Summarize changes' }}
      </button>

      <p v-if="summaryError" class="mt-3 text-xs text-red-400">{{ summaryError }}</p>

      <div v-if="summary" class="mt-3 rounded-md border border-edge bg-navy/60 p-4">
        <div class="mb-2 flex items-center gap-2">
          <span class="text-xs font-semibold uppercase tracking-wide text-slate-400">Change summary</span>
          <span
            v-if="summary.cached"
            class="rounded-full border border-edge px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-500"
          >
            cached
          </span>
        </div>
        <p class="whitespace-pre-line text-sm leading-relaxed text-slate-300">{{ summary.summary }}</p>
      </div>
    </footer>
  </article>
</template>
