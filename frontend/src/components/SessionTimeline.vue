<script setup>
// Every live session on one shared clock: what it has been doing over the
// last hour (hook events, replaced by recorded states wherever this page was
// watching) and how much memory it holds - idle sessions included, since an
// idle session still pins its claude process and MCP servers in RAM.
import { computed, ref } from 'vue'
import { useElementWidth } from '../composables/useElementWidth'
import { ACTIVITY, MEMORY_PARTS } from '../utils/processCategories'
import { duration, megabytes } from '../utils/time'

const props = defineProps({
  sessions: { type: Array, required: true },
  timeline: { type: Object, default: null },
  now: { type: Number, required: true },
  activeIds: { type: Set, required: true },
})

const STRIP_H = 14
const SORTS = { activity: 'Activity', memory: 'Memory' }
const sort = ref('activity')

const memoryOf = (s) => {
  const mcp = s.services.reduce((n, x) => n + x.mem_mb, 0)
  return { cli: s.claude_mem_mb, mcp, tools: Math.max(0, s.mem_mb - s.claude_mem_mb), total: s.mem_mb + mcp }
}
const rows = computed(() => {
  const list = props.sessions.map((s) => ({ s, mem: memoryOf(s), codes: props.timeline?.sessions?.[s.session_id] || '' }))
  if (sort.value === 'memory') list.sort((a, b) => b.mem.total - a.mem.total)
  return list
})
const memMax = computed(() => Math.max(1, ...rows.value.map((r) => r.mem.total)))
const totalMem = computed(() => rows.value.reduce((n, r) => n + r.mem.total, 0))

// the strip column has the same width on every row (one grid template)
const axis = ref(null)
const stripW = useElementWidth(axis)
const binCount = computed(() => Math.max(1, rows.value[0]?.codes.length || 120))
const binW = computed(() => stripW.value / binCount.value)

function runsOf(codes) {
  const runs = []
  for (let i = 0; i < codes.length; i++) {
    const code = codes[i]
    if (code === '.') continue
    const last = runs.at(-1)
    if (last && last.code === code && last.end === i) last.end = i + 1
    else runs.push({ code, start: i, end: i + 1 })
  }
  // a 2px surface gap where two different states touch
  return runs.map((r, i) => {
    const next = runs[i + 1]
    const touching = next && next.start === r.end
    const x = r.start * binW.value
    const w = Math.max(1, (r.end - r.start) * binW.value - (touching ? 2 : 0))
    const kind = ACTIVITY[r.code] || ACTIVITY.w
    return { ...r, x, w, color: kind.color, tall: kind.tall }
  })
}
const busyShare = (codes) => (codes ? [...codes].filter((c) => c !== '.').length / codes.length : 0)

const windowMin = computed(() => Math.round(((props.timeline?.bin_s || 30) * binCount.value) / 60))
const ticks = computed(() => [0, 0.25, 0.5, 0.75, 1].map((f) => ({ f, text: f === 1 ? 'now' : `-${Math.round(windowMin.value * (1 - f))}m` })))

const STATE_TEXT = { working: 'working', waiting: 'needs you', shell: 'shell', background: 'background' }
const LED = {
  working: 'bg-emerald-400',
  waiting: 'bg-amber-400',
  shell: 'bg-cyan-400',
  background: 'bg-violet-400',
  idle: 'bg-slate-600',
}
const statusText = (s) =>
  STATE_TEXT[s.state] ||
  `idle ${duration((props.now - new Date(s.status_since || s.last_activity || s.started_at).getTime()) / 1000)}`

// one tooltip for the whole table
const tip = ref(null)
const host = ref(null)
const clock = (ms) => new Date(ms).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
function place(event) {
  const rect = host.value.getBoundingClientRect()
  const left = event.clientX - rect.left
  return { left, top: event.clientY - rect.top, right: left > rect.width - 240 ? rect.width - left : null }
}
function onStrip(event, row) {
  const rect = event.currentTarget.getBoundingClientRect()
  const i = Math.min(binCount.value - 1, Math.max(0, Math.floor((event.clientX - rect.left) / binW.value)))
  const code = row.codes[i] || '.'
  const start = (props.timeline?.start || 0) + i * (props.timeline?.bin_s || 30) * 1000
  tip.value = {
    ...place(event),
    title: `${row.s.project} · ${row.s.name || row.s.pid}`,
    lines: [
      { color: ACTIVITY[code]?.color, value: ACTIVITY[code]?.label || 'Idle', label: `${clock(start)} - ${clock(start + (props.timeline?.bin_s || 30) * 1000)}` },
      { value: `${Math.round(busyShare(row.codes) * 100)}%`, label: `busy over the last ${windowMin.value}m` },
    ],
  }
}
function onMemory(event, row) {
  tip.value = {
    ...place(event),
    title: `${row.s.project} · ${megabytes(row.mem.total)}`,
    lines: MEMORY_PARTS.slice(0, 3).map((p) => ({ color: p.color, value: megabytes(row.mem[p.key]), label: p.label })),
  }
}
function memorySegments(mem) {
  const parts = MEMORY_PARTS.slice(0, 3).filter((p) => mem[p.key] > 0)
  return parts.map((p, i) => ({ ...p, pct: (mem[p.key] / memMax.value) * 100, last: i === parts.length - 1 }))
}

function jump(row) {
  if (!props.activeIds.has(row.s.pid)) return
  document.getElementById(`proc-${row.s.pid}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <section ref="host" class="relative">
    <div class="mb-2 flex flex-wrap items-center gap-x-4 gap-y-2">
      <h2 class="text-xs font-semibold uppercase tracking-widest text-slate-400">
        Sessions · {{ sessions.length }}
        <span class="font-normal normal-case tracking-normal text-slate-500">· {{ megabytes(totalMem) }} held</span>
      </h2>
      <div class="flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
        <span v-for="(kind, code) in ACTIVITY" :key="code" class="inline-flex items-center gap-1.5" :title="kind.hint">
          <span
            class="w-3 rounded-[2px]"
            :class="kind.tall ? 'h-3' : 'h-2'"
            :style="{ backgroundColor: kind.color }"
          />
          <template v-if="kind.tall">! </template>{{ kind.label }}
        </span>
        <span class="text-slate-600">|</span>
        <span v-for="p in MEMORY_PARTS.slice(0, 3)" :key="p.key" class="inline-flex items-center gap-1.5" :title="p.hint">
          <span class="h-2 w-2.5 rounded-[2px]" :style="{ backgroundColor: p.color }" />{{ p.label }}
        </span>
      </div>
      <div class="ml-auto flex overflow-hidden rounded-md border border-edge text-[11px]">
        <button
          v-for="(label, key) in SORTS"
          :key="key"
          class="px-2.5 py-1 transition-colors"
          :class="sort === key ? 'bg-panel-2 text-white' : 'text-slate-500 hover:text-slate-300'"
          @click="sort = key"
        >
          {{ label }}
        </button>
      </div>
    </div>

    <div class="rounded-lg border border-edge bg-panel">
      <!-- time axis over the strip column -->
      <div class="session-grid border-b border-edge/70 px-3 py-1 text-[10px] text-slate-500">
        <span class="col-span-4 max-md:col-span-2">Session</span>
        <div ref="axis" class="relative h-3.5">
          <span
            v-for="tk in ticks"
            :key="tk.f"
            class="absolute top-0 tabular-nums"
            :style="{ left: `${tk.f * 100}%`, transform: `translateX(-${tk.f * 100}%)` }"
          >{{ tk.text }}</span>
        </div>
        <span>Memory</span>
        <span class="max-md:hidden" />
        <span class="text-right max-md:hidden">Status</span>
      </div>

      <div
        v-for="row in rows"
        :key="row.s.pid"
        class="session-grid items-center px-3 py-[3px] text-xs transition-colors hover:bg-panel-2/70"
        :class="activeIds.has(row.s.pid) ? 'cursor-pointer' : ''"
        :title="activeIds.has(row.s.pid) ? 'Show its card' : ''"
        @click="jump(row)"
      >
        <span class="h-1.5 w-1.5 rounded-full" :class="LED[row.s.state] || LED.idle" />
        <span class="truncate font-medium text-slate-200">{{ row.s.project }}</span>
        <span class="truncate font-mono text-[10px] text-slate-500 max-md:hidden" :title="`pid ${row.s.pid}`">{{ row.s.name || row.s.pid }}</span>
        <span class="truncate text-slate-500 max-md:hidden" :title="row.s.title || row.s.last_prompt">{{ row.s.title || row.s.last_prompt }}</span>

        <svg
          :width="stripW"
          :height="STRIP_H"
          class="block cursor-crosshair"
          @pointermove="onStrip($event, row)"
          @pointerleave="tip = null"
        >
          <line :x1="0" :x2="stripW" :y1="STRIP_H / 2" :y2="STRIP_H / 2" stroke="#1e3050" stroke-width="1" />
          <line
            v-for="tk in ticks.slice(1, -1)"
            :key="tk.f"
            :x1="Math.round(tk.f * stripW) + 0.5"
            :x2="Math.round(tk.f * stripW) + 0.5"
            :y1="0"
            :y2="STRIP_H"
            stroke="#1e3050"
            stroke-width="1"
          />
          <rect
            v-for="run in runsOf(row.codes)"
            :key="run.start"
            :x="run.x"
            :y="run.tall ? 0 : 3"
            :width="run.w"
            :height="run.tall ? STRIP_H : STRIP_H - 6"
            rx="2"
            :fill="run.color"
          />
        </svg>

        <div class="flex h-2 min-w-0 gap-[2px] max-md:hidden" @pointermove="onMemory($event, row)" @pointerleave="tip = null">
          <span
            v-for="seg in memorySegments(row.mem)"
            :key="seg.key"
            class="h-full"
            :class="seg.last ? 'rounded-r-[3px]' : ''"
            :style="{ width: `${seg.pct}%`, backgroundColor: seg.color }"
          />
        </div>
        <span class="text-right font-mono text-[10.5px] tabular-nums text-slate-300">{{ megabytes(row.mem.total) }}</span>
        <span
          class="truncate text-right text-[11px] max-md:hidden"
          :class="row.s.state === 'waiting' ? 'font-medium text-amber-300' : row.s.state === 'idle' ? 'text-slate-500' : 'text-slate-300'"
        >
          <template v-if="row.s.state === 'waiting'">! </template>{{ statusText(row.s) }}
        </span>
      </div>
    </div>

    <div
      v-if="tip"
      class="pointer-events-none absolute z-20 min-w-40 rounded-md border border-edge bg-navy/95 px-2.5 py-1.5 text-[11px] shadow-lg"
      :style="tip.right == null ? { left: `${tip.left + 14}px`, top: `${tip.top + 14}px` } : { right: `${tip.right + 14}px`, top: `${tip.top + 14}px` }"
    >
      <div class="mb-1 truncate text-[10px] text-slate-500">{{ tip.title }}</div>
      <div v-for="(line, i) in tip.lines" :key="i" class="flex items-center gap-2">
        <span v-if="line.color" class="h-2 w-2 shrink-0 rounded-[2px]" :style="{ backgroundColor: line.color }" />
        <span class="font-semibold tabular-nums text-white">{{ line.value }}</span>
        <span class="text-slate-400">{{ line.label }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.session-grid {
  display: grid;
  grid-template-columns: 10px minmax(0, 9rem) minmax(0, 7rem) minmax(6rem, 1fr) minmax(16rem, 2.4fr) 8rem 4.5rem 6rem;
  column-gap: 0.75rem;
}
/* phones: LED, project, activity strip, memory total - the name, title,
   memory bar and status columns are hidden (the LED carries the state) */
@media (max-width: 47.99rem) {
  .session-grid {
    grid-template-columns: 10px minmax(0, 1fr) minmax(6rem, 1.5fr) 3.5rem;
  }
}
</style>
