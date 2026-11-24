<script setup>
// Every running Claude Code process as a container: what each one is
// executing right now (tool calls, the OS processes under them, subagents,
// background tasks), color-coded by kind of work. Above them, where the
// machine's RAM and CPU go; below, every session's last hour. Pops out into
// its own window (?popout=1 renders without the sidebar).
import { computed, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { apiFetch, usePolling } from '../composables/useApi'
import { CATEGORIES, MEMORY_PARTS } from '../utils/processCategories'
import { megabytes } from '../utils/time'
import MasonryGrid from '../components/MasonryGrid.vue'
import MemoryBreakdown from '../components/MemoryBreakdown.vue'
import ProcessContainer from '../components/ProcessContainer.vue'
import SessionTimeline from '../components/SessionTimeline.vue'
import TrendChart from '../components/TrendChart.vue'

const POLL_MS = 3000
const SPARK = 40 // per-process samples (2 minutes)
const TREND_MS = 60 * 60e3

const route = useRoute()
const popout = computed(() => route.query.popout === '1')
const data = ref(null)
const error = ref(null)
const fetchedAt = ref(Date.now())
const now = ref(Date.now())
const filter = ref(null)

// history: seeded once from the server (it keeps what any open window
// sampled), then each poll appends its own reading
const trend = ref({ t: [], host_cpu: [], host_mem_gb: [], claude_cpu: [], claude_mem_gb: [] })
const sparks = ref({})
let seeded = false

const ticker = setInterval(() => (now.value = Date.now()), 1000)
onUnmounted(() => clearInterval(ticker))

function append(result) {
  const t = Date.parse(result.generated_at)
  const cut = t - TREND_MS
  const prev = trend.value
  const keep = prev.t.findIndex((x) => x >= cut)
  const from = keep < 0 ? prev.t.length : keep
  const push = (arr, v) => [...arr.slice(from), v]
  trend.value = {
    t: push(prev.t, t),
    host_cpu: push(prev.host_cpu, result.host.cpu),
    host_mem_gb: push(prev.host_mem_gb, result.host.mem_used_gb),
    claude_cpu: push(prev.claude_cpu, result.totals.claude_cpu),
    claude_mem_gb: push(prev.claude_mem_gb, result.totals.claude_mem_gb),
  }
  const next = {}
  for (const s of result.sessions) {
    const old = sparks.value[s.pid] || { cpu: [], mem: [] }
    next[s.pid] = { cpu: [...old.cpu, s.cpu].slice(-SPARK), mem: [...old.mem, s.mem_mb].slice(-SPARK) }
  }
  sparks.value = next
}

async function refresh() {
  try {
    const result = await apiFetch(seeded ? '/api/processes' : '/api/processes?history=1')
    if (result.history) {
      trend.value = result.history.trend
      sparks.value = result.history.sparks
      seeded = true
    } else if (result.sampled !== false) {
      append(result)
    }
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
const activeIds = computed(() => new Set(active.value.map((s) => s.pid)))

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
  }
})
const host = computed(() => data.value?.host)
const totals = computed(() => data.value?.totals)
const memTight = computed(() => (host.value?.mem_percent || 0) >= 90)

const CLAUDE = MEMORY_PARTS[0].color
const HOST = '#94a3b8'
const memorySeries = computed(() => [
  { key: 'host', label: 'In use', color: HOST, values: trend.value.host_mem_gb },
  { key: 'claude', label: 'Claude', color: CLAUDE, values: trend.value.claude_mem_gb, wash: true },
])
const cpuSeries = computed(() => [
  { key: 'host', label: 'Machine', color: HOST, values: trend.value.host_cpu },
  { key: 'claude', label: 'Claude', color: CLAUDE, values: trend.value.claude_cpu, wash: true },
])
const gb = (v) => `${v.toFixed(1)} GB`
const gbTick = (v) => `${Math.round(v)}G`
const pct = (v) => `${v.toFixed(0)}%`

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
</script>

<template>
  <div class="flex h-full flex-col">
    <header class="mb-3 flex shrink-0 flex-wrap items-end justify-between gap-3 md:mb-4">
      <div>
        <h1 class="text-xl font-semibold text-white">Processes</h1>
        <p class="mt-1 text-sm text-slate-500 max-md:hidden">
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
            :title="totals ? `Claude processes, their tools and MCP servers use ${megabytes(totals.claude_mem_gb * 1024)}` : ''"
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

    <div v-if="error" class="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
      {{ error }}
    </div>

    <div class="min-h-0 flex-1 overflow-auto pr-1">
      <div v-if="data === null && !error" class="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(min(460px,100%),1fr))]">
        <div v-for="i in 4" :key="i" class="h-48 animate-pulse rounded-lg border border-edge bg-panel/50" />
      </div>

      <template v-else-if="data">
        <!-- the machine: where RAM goes now, RAM and CPU over the last hour -->
        <section class="mb-2 grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(min(380px,100%),1fr))]">
          <MemoryBreakdown v-if="host && totals" :host="host" :totals="totals" :sessions="sessions" />
          <div class="rounded-lg border border-edge bg-panel px-4 py-3">
            <TrendChart
              title="Memory"
              :t="trend.t"
              :series="memorySeries"
              :max="host?.mem_total_gb || 1"
              :format="gb"
              :tick="gbTick"
              :now="now"
            />
          </div>
          <div class="rounded-lg border border-edge bg-panel px-4 py-3">
            <TrendChart title="CPU" :t="trend.t" :series="cpuSeries" :max="100" :format="pct" :now="now" />
          </div>
        </section>

        <!-- color key, doubling as a filter; stays in view over the cards -->
        <div class="sticky top-0 z-10 -mx-1 mb-2 flex flex-wrap items-center gap-1.5 bg-navy px-1 py-2">
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

        <p
          v-if="!active.length"
          class="rounded-lg border border-dashed border-edge bg-panel/40 px-6 py-10 text-center text-sm text-slate-500"
        >
          Nothing running - {{ sessions.length }} session{{ sessions.length === 1 ? '' : 's' }} open and idle
        </p>
        <MasonryGrid v-else :items="active" :item-key="(s) => s.pid" :min-width="460" :gap="16">
          <template #default="{ item }">
            <ProcessContainer
              :id="`proc-${item.pid}`"
              class="scroll-mt-14"
              :session="item"
              :now="now"
              :fetched-at="fetchedAt"
              :cpu-history="sparks[item.pid]?.cpu || []"
              :mem-history="sparks[item.pid]?.mem || []"
              :filter="filter"
              :popout="popout"
            />
          </template>
        </MasonryGrid>

        <!-- every session, idle ones included: last hour + memory held -->
        <SessionTimeline
          class="mt-6 pb-2"
          :sessions="sessions"
          :timeline="data.timeline"
          :now="now"
          :active-ids="activeIds"
        />
      </template>
    </div>
  </div>
</template>
