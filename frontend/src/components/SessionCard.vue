<script setup>
import { computed, ref } from 'vue'
import { apiFetch } from '../composables/useApi'

const props = defineProps({
  session: { type: Object, required: true },
})

const showFiles = ref(false)
const summary = ref(null)
const summarizing = ref(false)
const summaryError = ref(null)

async function summarize() {
  if (summary.value) {
    summary.value = null // toggle closed
    return
  }
  summarizing.value = true
  summaryError.value = null
  try {
    summary.value = await apiFetch(`/api/activity/${props.session.session_id}/summarize`, {
      method: 'POST',
    })
  } catch (err) {
    summaryError.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    summarizing.value = false
  }
}

function relative(iso) {
  if (!iso) return '?'
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

const duration = computed(() => {
  const ms =
    new Date(props.session.last_activity).getTime() -
    new Date(props.session.started_at).getTime()
  const minutes = Math.round(ms / 60000)
  return minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`
})

const topTools = computed(() =>
  Object.entries(props.session.tool_counts || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
)

// state-driven presentation; waiting/done are the "needs attention" states
const STATES = {
  working: { dot: 'animate-pulse bg-emerald-400', label: null, badge: null },
  waiting: {
    dot: 'animate-pulse bg-amber-400',
    label: 'waiting',
    badge: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    badgeText: 'needs input',
  },
  done: {
    dot: 'bg-sky-400',
    label: 'finished',
    badge: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
    badgeText: 'finished',
  },
  idle: { dot: 'bg-slate-600', label: null, badge: null },
}
const stateMeta = computed(() => STATES[props.session.state] || STATES.idle)
const needsAttention = computed(() => ['waiting', 'done'].includes(props.session.state))

const shortName = (path) => path.split(/[\\/]/).pop()
</script>

<template>
  <article
    class="rounded-lg border bg-panel p-4"
    :class="needsAttention ? 'border-amber-500/30' : 'border-edge'"
  >
    <header class="flex items-center gap-2.5">
      <span class="h-2 w-2 shrink-0 rounded-full" :class="stateMeta.dot" />
      <h3 class="text-sm font-semibold text-white">{{ session.project }}</h3>
      <span class="font-mono text-[10px] text-slate-600">{{ session.session_id.slice(0, 8) }}</span>
      <span
        v-if="stateMeta.badge"
        class="rounded-full border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide"
        :class="stateMeta.badge"
      >
        {{ stateMeta.badgeText }}
      </span>
      <span class="ml-auto text-xs text-slate-500">
        {{ session.active ? 'active now' : stateMeta.label || relative(session.last_activity) }}
        &middot; {{ duration }}
      </span>
    </header>

    <p
      v-if="session.title"
      class="mt-1.5 truncate text-xs font-medium text-slate-300"
      :title="session.title"
    >
      {{ session.title }}
    </p>

    <p v-if="session.last_prompt" class="mt-2 truncate text-xs italic text-slate-500" :title="session.last_prompt">
      &ldquo;{{ session.last_prompt }}&rdquo;
    </p>

    <div class="mt-3 flex flex-wrap gap-1.5">
      <span
        v-for="[tool, count] in topTools"
        :key="tool"
        class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
      >
        {{ tool }} &times;{{ count }}
      </span>
    </div>

    <div class="mt-3 flex items-center gap-4">
      <button
        v-if="session.files_edited?.length"
        class="text-xs text-coral hover:underline"
        @click="showFiles = !showFiles"
      >
        {{ session.files_edited.length }} file{{ session.files_edited.length === 1 ? '' : 's' }} edited
        {{ showFiles ? '▾' : '▸' }}
      </button>
      <button
        class="inline-flex items-center gap-1.5 text-xs text-coral hover:underline disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="summarizing"
        @click="summarize"
      >
        <svg v-if="summarizing" class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" />
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z" />
        </svg>
        {{ summarizing ? 'Summarizing…' : 'Summary' }}
        <template v-if="!summarizing">{{ summary ? '▾' : '▸' }}</template>
      </button>
    </div>

    <ul v-if="showFiles" class="mt-1.5 space-y-0.5">
      <li
        v-for="file in session.files_edited"
        :key="file"
        class="truncate font-mono text-xs text-slate-400"
        :title="file"
      >
        {{ shortName(file) }}
        <span class="text-slate-600"> - {{ file }}</span>
      </li>
    </ul>

    <p v-if="summaryError" class="mt-2 text-xs text-red-400">{{ summaryError }}</p>

    <div v-if="summary" class="mt-2 rounded-md border border-edge bg-navy/60 p-3">
      <div class="mb-1.5 flex items-center gap-2">
        <span class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Session summary</span>
        <span
          v-if="summary.cached"
          class="rounded-full border border-edge px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-slate-500"
        >
          cached
        </span>
      </div>
      <p class="whitespace-pre-line text-xs leading-relaxed text-slate-300">{{ summary.summary }}</p>
    </div>
  </article>
</template>
