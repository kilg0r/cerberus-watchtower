<script setup>
import { computed, ref, watch } from 'vue'
import { apiFetch, usePolling } from '../composables/useApi'
import { useEventStream } from '../composables/useEventStream'
import SessionCard from '../components/SessionCard.vue'
import SplitPane from '../components/SplitPane.vue'

const sessions = ref(null)
const backendDown = ref(false)
const { liveEvents, connected } = useEventStream()

const EVENT_LABELS = {
  SessionStart: { label: 'session started', class: 'text-emerald-400' },
  UserPromptSubmit: { label: 'prompt sent', class: 'text-sky-400' },
  Stop: { label: 'turn finished', class: 'text-sky-400' },
  Notification: { label: 'needs input', class: 'text-amber-400' },
  SubagentStop: { label: 'agent finished', class: 'text-violet-400' },
  PostToolUse: { label: 'edited', class: 'text-amber-400' },
}

const attentionCount = computed(
  () => (sessions.value || []).filter((s) => s.state === 'waiting' || s.state === 'done').length
)

async function refresh() {
  try {
    const data = await apiFetch('/api/activity')
    sessions.value = data.sessions
    // seed the live feed with recent history on first load
    if (!liveEvents.value.length && data.events.length) {
      liveEvents.value = data.events
    }
    backendDown.value = false
  } catch (err) {
    if (err.message === 'backend-unreachable') backendDown.value = true
  }
}

usePolling(refresh, 30_000)

// refresh session list shortly after session lifecycle events arrive
watch(
  () => liveEvents.value[0],
  (event) => {
    if (event && event.event !== 'PostToolUse') setTimeout(refresh, 1500)
  }
)

const timeOf = (iso) => (iso ? new Date(iso).toLocaleTimeString() : '')
const shortName = (path) => (path ? path.split(/[\\/]/).pop() : null)
</script>

<template>
  <div class="flex h-full flex-col">
    <header class="mb-6 flex shrink-0 items-end justify-between">
      <div>
        <h1 class="text-xl font-semibold text-white">Activity</h1>
        <p class="mt-1 text-sm text-slate-500">Agent sessions and live tool activity</p>
      </div>
      <div class="flex items-center gap-4">
        <span
          v-if="attentionCount"
          class="rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-300"
        >
          {{ attentionCount }} need{{ attentionCount === 1 ? 's' : '' }} attention
        </span>
        <p class="flex items-center gap-2 text-xs text-slate-500">
          <span
            class="h-2 w-2 rounded-full"
            :class="connected ? 'animate-pulse bg-emerald-400' : 'bg-red-400'"
          />
          {{ connected ? 'live' : 'stream offline' }}
        </p>
      </div>
    </header>

    <div
      v-if="backendDown"
      class="mb-6 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300"
    >
      Backend not running on :8765
    </div>

    <SplitPane direction="horizontal" storage-key="activity" :initial="0.7" class="min-h-0 flex-1">
      <template #first>
      <!-- sessions -->
      <section class="flex h-full min-h-0 flex-col pr-3">
        <h2 class="mb-3 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">
          Recent sessions
        </h2>
        <div v-if="sessions === null" class="space-y-3">
          <div v-for="i in 3" :key="i" class="h-24 animate-pulse rounded-lg border border-edge bg-panel/50" />
        </div>
        <p v-else-if="!sessions.length" class="rounded-lg border border-dashed border-edge bg-panel/40 px-6 py-10 text-center text-sm text-slate-500">
          No sessions in the last 3 days
        </p>
        <div v-else class="grid min-h-0 flex-1 content-start items-start gap-3 overflow-auto pr-1 2xl:grid-cols-2">
          <SessionCard v-for="session in sessions" :key="session.session_id" :session="session" />
        </div>
      </section>
      </template>

      <template #second>
      <!-- live feed -->
      <section class="flex h-full min-h-0 flex-col pl-3">
        <h2 class="mb-3 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">
          Live feed
        </h2>
        <div class="min-h-0 flex-1 space-y-1 overflow-auto rounded-lg border border-edge bg-panel p-3">
          <p v-if="!liveEvents.length" class="py-6 text-center text-xs text-slate-500">
            Waiting for events - hook activity shows up here in real time
          </p>
          <div
            v-for="(event, i) in liveEvents"
            :key="i"
            class="flex items-baseline gap-2 border-b border-edge/40 py-1.5 text-xs last:border-0"
          >
            <span class="shrink-0 font-mono text-[10px] text-slate-600">{{ timeOf(event.ts) }}</span>
            <span class="shrink-0" :class="(EVENT_LABELS[event.event] || {}).class || 'text-slate-400'">
              {{ (EVENT_LABELS[event.event] || {}).label || event.event }}
            </span>
            <span v-if="shortName(event.file_path)" class="min-w-0 truncate font-mono text-slate-300" :title="event.file_path">
              {{ shortName(event.file_path) }}
            </span>
            <span class="ml-auto shrink-0 text-[10px] text-slate-500">{{ event.project }}</span>
          </div>
        </div>
      </section>
      </template>
    </SplitPane>
  </div>
</template>
