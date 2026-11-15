<script setup>
// Every running Claude Code process as a container: what each one is
// executing right now (tool calls, the OS processes under them, subagents,
// background tasks), color-coded by kind of work. Pops out into its own
// window (?popout=1 renders without the sidebar).
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { apiFetch, usePolling } from '../composables/useApi'
import { CATEGORIES } from '../utils/processCategories'
import { duration, megabytes } from '../utils/time'
import ProcessContainer from '../components/ProcessContainer.vue'

const POLL_MS = 3000
const HISTORY = 40 // cpu samples kept per process (2 minutes)
const IDLE_KEY = 'watchtower:processes:show-idle'

const route = useRoute()
const popout = computed(() => route.query.popout === '1')
const data = ref(null)
const error = ref(null)
const fetchedAt = ref(Date.now())
const now = ref(Date.now())
const filter = ref(null)
const cpuHistory = ref({})
const showIdle = ref(localStorage.getItem(IDLE_KEY) === '1')
watch(showIdle, (value) => localStorage.setItem(IDLE_KEY, value ? '1' : '0'))

const ticker = setInterval(() => (now.value = Date.now()), 1000)
onUnmounted(() => clearInterval(ticker))

async function refresh() {
  try {
    const result = await apiFetch('/api/processes')
    const history = {}
    for (const s of result.sessions) {
      history[s.pid] = [...(cpuHistory.value[s.pid] || []), s.cpu].slice(-HISTORY)
    }
    cpuHistory.value = history
    data.value = result
    fetchedAt.value = Date.now()
    now.value = Date.now()
    error.value = null
  } catch (err) {
    error.value = err.message === 'backend-unreachable' ? 'Backend not running on :8765' : err.message
  }
}
usePolling(refresh, POLL_MS)

const STATE_ORDER = { waiting: 0, working: 1, shell: 2, background: 3, idle: 4 }
const sessions = computed(() =>
  (data.value?.sessions || [])
    .slice()
    .sort(
      (a, b) =>
        (STATE_ORDER[a.state] ?? 5) - (STATE_ORDER[b.state] ?? 5) ||
        (b.last_activity || '').localeCompare(a.last_activity || '')
    )
)
const active = computed(() => sessions.value.filter((s) => s.state !== 'idle'))
const idle = computed(() => sessions.value.filter((s) => s.state === 'idle'))

const workOf = (s) => [
  ...s.lanes.flatMap((l) => l.ops.map((o) => o.category)),
  ...s.background.map((b) => b.category),
  ...s.lanes.filter((l) => l.kind !== 'main').map(() => 'agent'),
]
const counts = computed(() => {
  const out = {}
  for (const s of active.value) for (const key of workOf(s)) out[key] = (out[key] || 0) + 1
  return out
})
const countTree = (node) => 1 + node.children.reduce((n, c) => n + countTree(c), 0)
const stats = computed(() => {
  const s = active.value
  const ops = s.flatMap((x) => [...x.lanes.flatMap((l) => l.ops), ...x.background])
  return {
    live: sessions.value.length,
    working: s.filter((x) => x.state === 'working').length,
    waiting: s.filter((x) => x.state === 'waiting').length,
    agents: s.reduce((n, x) => n + x.lanes.filter((l) => l.kind !== 'main').length, 0),
    processes: ops.reduce((n, o) => n + o.processes.reduce((m, p) => m + countTree(p), 0), 0),
    claudeMem: sessions.value.reduce((n, x) => n + x.mem_mb, 0),
  }
})
const host = computed(() => data.value?.host)
const memTight = computed(() => (host.value?.mem_percent || 0) >= 90)

function toggleFilter(key) {
  filter.value = filter.value === key ? null : key
}

function popOut() {
  window.open(
    `${window.location.origin}/processes?popout=1`,
    'watchtower-processes',
    'popup=yes,width=1480,height=940'
  )
}

const IDLE_FOR = (s) => duration((now.value - new Date(s.status_since || s.last_activity || s.started_at).getTime()) / 1000)
</script>

<template>
  <div class="flex h-full flex-col">
    <header class="mb-4 flex shrink-0 flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-white">Processes</h1>
        <p class="mt-1 text-sm text-slate-500">
          What every running Claude session is executing - calls, processes, agents, background tasks
          <span v-if="data?.indexing" class="ml-2 text-amber-400/80">- indexing transcripts…</span>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <template v-if="data">
          <span class="rounded-full border border-edge bg-panel px-3 py-1 text-slate-300">
            {{ stats.live }} <span class="text-slate-500">live</span>
          </span>
          <span class="rounded-full border border-edge bg-panel px-3 py-1 text-slate-300">
            {{ stats.working }} <span class="text-slate-500">working</span>
          </span>
          <span
            v-if="stats.waiting"
            class="rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 font-medium text-amber-300"
          >
            ! {{ stats.waiting }} need{{ stats.waiting === 1 ? 's' : '' }} you
          </span>
          <span class="rounded-full border border-edge bg-panel px-3 py-1 text-slate-300">
            {{ stats.agents }} <span class="text-slate-500">agents</span>
          </span>
          <span class="rounded-full border border-edge bg-panel px-3 py-1 text-slate-300">
            {{ stats.processes }} <span class="text-slate-500">processes</span>
          </span>
          <span
            v-if="host"
            class="rounded-full border px-3 py-1 font-mono"
            :class="memTight ? 'border-amber-500/40 bg-amber-500/10 text-amber-300' : 'border-edge bg-panel text-slate-300'"
            :title="`Claude processes + their tools use ${megabytes(stats.claudeMem)}`"
          >
            <template v-if="memTight">! </template>RAM {{ host.mem_used_gb }}/{{ host.mem_total_gb }} GB
            <span class="text-slate-500">· CPU {{ host.cpu.toFixed(0) }}%</span>
          </span>
        </template>
        <button
          v-if="!popout"
          class="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-slate-400 transition-colors hover:border-coral/40 hover:text-coral"
          title="Open in its own window"
          @click="popOut"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
          Pop out
        </button>
      </div>
    </header>

    <!-- color key, doubling as a filter -->
    <div class="mb-4 flex shrink-0 flex-wrap items-center gap-1.5">
      <button
        v-for="c in CATEGORIES"
        :key="c.key"
        class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors"
        :class="filter === c.key
          ? 'border-slate-300/60 bg-panel-2 text-white'
          : 'border-edge bg-panel text-slate-400 hover:text-slate-200'"
        :title="`${c.hint} - click to highlight`"
        @click="toggleFilter(c.key)"
      >
        <span class="h-2 w-2 rounded-full" :style="{ backgroundColor: c.color }" />
        {{ c.label }}
        <span class="tabular-nums text-slate-500">{{ counts[c.key] || 0 }}</span>
      </button>
      <button v-if="filter" class="ml-1 text-[11px] text-slate-500 hover:text-coral" @click="filter = null">clear</button>
    </div>

    <div v-if="error" class="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
      {{ error }}
    </div>

    <div class="min-h-0 flex-1 overflow-auto pr-1">
      <div v-if="data === null && !error" class="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(460px,1fr))]">
        <div v-for="i in 4" :key="i" class="h-48 animate-pulse rounded-lg border border-edge bg-panel/50" />
      </div>

      <template v-else-if="data">
        <p
          v-if="!active.length"
          class="rounded-lg border border-dashed border-edge bg-panel/40 px-6 py-10 text-center text-sm text-slate-500"
        >
          Nothing running - {{ sessions.length }} session{{ sessions.length === 1 ? '' : 's' }} open and idle
        </p>
        <div v-else class="grid items-start gap-4 [grid-template-columns:repeat(auto-fill,minmax(460px,1fr))]">
          <ProcessContainer
            v-for="s in active"
            :key="s.pid"
            :session="s"
            :now="now"
            :fetched-at="fetchedAt"
            :cpu-history="cpuHistory[s.pid] || []"
            :filter="filter"
            :popout="popout"
          />
        </div>

        <!-- idle sessions: one line each -->
        <section v-if="idle.length" class="mt-6">
          <button
            class="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-500 hover:text-slate-300"
            @click="showIdle = !showIdle"
          >
            {{ showIdle ? '▾' : '▸' }} Idle · {{ idle.length }}
          </button>
          <div v-if="showIdle" class="grid gap-1.5 [grid-template-columns:repeat(auto-fill,minmax(340px,1fr))]">
            <div
              v-for="s in idle"
              :key="s.pid"
              class="flex items-center gap-2 rounded-md border border-edge bg-panel/70 px-3 py-1.5 text-xs"
              :title="`${s.title || ''}\n${s.cwd} · pid ${s.pid}`"
            >
              <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-600" />
              <span class="shrink-0 font-medium text-slate-300">{{ s.project }}</span>
              <span class="min-w-0 flex-1 truncate text-slate-500">{{ s.title || s.last_prompt }}</span>
              <span class="shrink-0 font-mono text-[10px] text-slate-600">{{ megabytes(s.mem_mb) }} · idle {{ IDLE_FOR(s) }}</span>
            </div>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>
