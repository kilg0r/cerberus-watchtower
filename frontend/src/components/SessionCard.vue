<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  session: { type: Object, required: true },
})

const showFiles = ref(false)

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

const shortName = (path) => path.split(/[\\/]/).pop()
</script>

<template>
  <article class="rounded-lg border border-edge bg-panel p-4">
    <header class="flex items-center gap-2.5">
      <span
        class="h-2 w-2 shrink-0 rounded-full"
        :class="session.active ? 'animate-pulse bg-emerald-400' : 'bg-slate-600'"
      />
      <h3 class="text-sm font-semibold text-white">{{ session.project }}</h3>
      <span class="font-mono text-[10px] text-slate-600">{{ session.session_id.slice(0, 8) }}</span>
      <span class="ml-auto text-xs text-slate-500">
        {{ session.active ? 'active now' : relative(session.last_activity) }} &middot; {{ duration }}
      </span>
    </header>

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

    <div v-if="session.files_edited?.length" class="mt-3">
      <button
        class="text-xs text-coral hover:underline"
        @click="showFiles = !showFiles"
      >
        {{ session.files_edited.length }} file{{ session.files_edited.length === 1 ? '' : 's' }} edited
        {{ showFiles ? '▾' : '▸' }}
      </button>
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
    </div>
  </article>
</template>
