<script setup>
// One running Claude process, drawn like a container: status + resources in
// the header, a bar showing the mix of work it's running, then one lane per
// agent (main, subagents, forks, workflow runs), its background tasks and
// the MCP servers it keeps alive.
import { computed } from 'vue'
import { CATEGORIES, categoryOf, colorOf } from '../utils/processCategories'
import { duration, megabytes, relative } from '../utils/time'
import ProcessOp from './ProcessOp.vue'
import Sparkline from './Sparkline.vue'

const props = defineProps({
  session: { type: Object, required: true },
  now: { type: Number, required: true },
  fetchedAt: { type: Number, required: true },
  cpuHistory: { type: Array, default: () => [] },
  memHistory: { type: Array, default: () => [] },
  filter: { type: String, default: null },
  popout: { type: Boolean, default: false },
})

const STATES = {
  working: { led: 'animate-pulse bg-emerald-400', badge: null, border: 'border-edge' },
  waiting: {
    led: 'animate-pulse bg-amber-400',
    badge: 'border-amber-500/50 bg-amber-500/10 text-amber-300',
    text: '! needs you',
    border: 'border-amber-500/40',
  },
  shell: { led: 'bg-cyan-400', badge: 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300', text: 'shell', border: 'border-edge' },
  background: {
    led: 'animate-pulse bg-violet-400',
    badge: 'border-violet-500/40 bg-violet-500/10 text-violet-300',
    text: 'background',
    border: 'border-edge',
  },
  idle: { led: 'bg-slate-600', badge: null, border: 'border-edge' },
}
const state = computed(() => STATES[props.session.state] || STATES.idle)

const since = computed(() => Math.max(0, (props.now - props.fetchedAt) / 1000))
const elapsedOf = (op) => (op.elapsed_s == null ? null : op.elapsed_s + since.value)
const uptime = computed(() => duration((props.now - new Date(props.session.started_at).getTime()) / 1000))

const lanes = computed(() => props.session.lanes)
const mainLane = computed(() => lanes.value.find((l) => l.kind === 'main'))
const agentLanes = computed(() => lanes.value.filter((l) => l.kind === 'subagent' || l.kind === 'fork'))

// workflow runs: the background Workflow task names the run, its agents fill it
const workflowGroups = computed(() => {
  const groups = new Map()
  for (const bg of props.session.background) {
    if (bg.tool === 'Workflow') {
      const key = bg.workflow_run || bg.id
      groups.set(key, { key, task: bg, agents: [] })
    }
  }
  for (const lane of lanes.value.filter((l) => l.kind === 'workflow')) {
    const key = lane.workflow_run
    if (!groups.has(key)) groups.set(key, { key, task: null, agents: [] })
    groups.get(key).agents.push(lane)
  }
  return [...groups.values()]
})
const background = computed(() => props.session.background.filter((b) => b.tool !== 'Workflow'))

// every unit of work, for the mix bar: calls, tasks, and agents themselves
const work = computed(() => {
  const items = [...lanes.value.flatMap((l) => l.ops), ...props.session.background]
  return [...items.map((o) => o.category), ...lanes.value.filter((l) => l.kind !== 'main').map(() => 'agent')]
})
const mix = computed(() => {
  const counts = {}
  for (const key of work.value) counts[key] = (counts[key] || 0) + 1
  return [...CATEGORIES, categoryOf('waiting')]
    .filter((c) => counts[c.key])
    .map((c) => ({ ...c, count: counts[c.key] }))
})
const matches = computed(() => !props.filter || work.value.includes(props.filter))
const dimOp = (op) => !!props.filter && op.category !== props.filter
const dimLane = (lane) => !!props.filter && props.filter !== 'agent' && !lane.ops.some((o) => o.category === props.filter)

const LANE_KINDS = { subagent: 'Subagent', fork: 'Fork', workflow: 'Workflow' }
const laneLabel = (lane) => (lane.kind === 'subagent' ? lane.agent_type || 'Subagent' : LANE_KINDS[lane.kind])
const agentColor = colorOf('agent')
const servicesMem = computed(() => props.session.services.reduce((sum, s) => sum + s.mem_mb, 0))
const mainIdleText = computed(() => {
  const s = props.session.state
  if (s === 'working') return 'thinking - no tool running'
  if (s === 'waiting') return 'waiting on a permission prompt'
  return null
})
const phaseCounts = (agents) => {
  const counts = {}
  for (const a of agents) counts[a.phase || 'agent'] = (counts[a.phase || 'agent'] || 0) + 1
  return Object.entries(counts)
}
</script>

<template>
  <article
    class="flex min-w-0 flex-col rounded-lg border bg-panel transition-opacity"
    :class="[state.border, matches ? '' : 'opacity-35']"
  >
    <!-- header: identity, state, resources -->
    <header class="flex items-start gap-3 border-b border-edge/70 px-4 py-2.5">
      <span class="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" :class="state.led" />
      <div class="min-w-0 flex-1">
        <div class="flex min-w-0 items-baseline gap-2">
          <h3 class="truncate text-sm font-semibold text-white">{{ session.project }}</h3>
          <span class="shrink-0 font-mono text-[10px] text-slate-500" :title="`pid ${session.pid}`">{{ session.name }}</span>
          <span
            v-if="state.badge"
            class="shrink-0 rounded-full border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide"
            :class="state.badge"
          >
            {{ state.text }}
          </span>
          <span v-if="session.kind && session.kind !== 'interactive'" class="shrink-0 text-[10px] text-slate-500">
            {{ session.kind }}
          </span>
        </div>
        <p class="mt-0.5 truncate text-xs text-slate-400" :title="session.title || session.last_prompt">
          {{ session.title || session.last_prompt || session.cwd }}
        </p>
      </div>
      <div class="shrink-0 text-right font-mono text-[10px] leading-4 text-slate-500">
        <div class="flex items-center justify-end gap-1.5">
          <Sparkline :values="cpuHistory" />
          <span title="CPU share of the whole machine, Claude + its tool processes">
            CPU <span class="tabular-nums text-slate-200">{{ session.cpu.toFixed(1) }}%</span>
          </span>
        </div>
        <div class="flex items-center justify-end gap-1.5">
          <Sparkline :values="memHistory" :zero="false" :floor="64" />
          <span title="Resident memory of Claude + its tool processes (MCP servers are listed below)">
            MEM <span class="text-slate-200">{{ megabytes(session.mem_mb) }}</span>
          </span>
        </div>
        <div>up {{ uptime }}</div>
      </div>
    </header>

    <!-- mix of work: one segment per unit, grouped by kind -->
    <div v-if="mix.length" class="flex items-center gap-3 px-4 pt-2">
      <div class="flex h-1.5 min-w-0 flex-1 gap-[2px]" role="img" :aria-label="mix.map((m) => `${m.count} ${m.label}`).join(', ')">
        <span
          v-for="m in mix"
          :key="m.key"
          class="h-full rounded-[2px]"
          :style="{ flexGrow: m.count, backgroundColor: m.color }"
          :title="`${m.count} ${m.label}`"
        />
      </div>
      <span class="shrink-0 text-[10px] text-slate-500">
        <template v-for="(m, i) in mix" :key="m.key">{{ i ? ' · ' : '' }}{{ m.count }} {{ m.label.toLowerCase() }}</template>
      </span>
    </div>

    <div class="flex flex-col gap-2 p-3">
      <!-- main agent -->
      <section v-if="mainLane" class="rounded-md border border-edge/70 bg-navy/40">
        <div class="flex items-center gap-2 px-3 py-1.5 text-[10px]">
          <span class="font-semibold uppercase tracking-widest text-slate-400">Main</span>
          <span v-if="session.status_since && session.state !== 'idle'" class="text-slate-500">
            {{ session.status }} since {{ relative(session.status_since) }}
          </span>
          <span class="ml-auto text-slate-500">{{ mainLane.tool_total.toLocaleString() }} calls</span>
        </div>
        <ProcessOp
          v-for="op in mainLane.ops"
          :key="op.id"
          :op="op"
          :elapsed="elapsedOf(op)"
          :dim="dimOp(op)"
        />
        <p v-if="!mainLane.ops.length && mainIdleText" class="px-3 pb-2 text-[11px] italic text-slate-500">
          {{ mainIdleText }}
        </p>
        <p v-if="!session.transcript" class="px-3 pb-2 text-[11px] text-slate-500">
          same session as another open process - its calls are shown there
        </p>
      </section>

      <!-- subagents and forks -->
      <section
        v-for="lane in agentLanes"
        :key="lane.id"
        class="rounded-md border border-edge/70 border-l-2 bg-navy/40 transition-opacity"
        :class="dimLane(lane) ? 'opacity-40' : ''"
        :style="{ borderLeftColor: agentColor }"
      >
        <div class="flex items-center gap-2 px-3 py-1.5 text-[10px]">
          <span class="inline-flex shrink-0 items-center gap-1.5 font-semibold uppercase tracking-widest text-slate-300">
            <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: agentColor }" />{{ laneLabel(lane) }}
          </span>
          <span class="min-w-0 flex-1 truncate text-xs text-slate-200" :title="lane.label">{{ lane.label }}</span>
          <span v-if="lane.background" class="shrink-0 rounded border border-edge px-1 text-[9px] text-slate-400">bg</span>
          <span class="shrink-0 text-slate-500">{{ lane.tool_total }} calls · {{ relative(lane.last_activity) }}</span>
        </div>
        <ProcessOp
          v-for="op in lane.ops"
          :key="op.id"
          :op="op"
          :elapsed="elapsedOf(op)"
          :dim="dimOp(op)"
        />
        <p v-if="!lane.ops.length" class="px-3 pb-2 text-[11px] italic text-slate-500">thinking - no tool running</p>
      </section>

      <!-- workflow runs -->
      <section
        v-for="group in workflowGroups"
        :key="group.key"
        class="rounded-md border border-edge/70 border-l-2 bg-navy/40"
        :style="{ borderLeftColor: agentColor }"
      >
        <div class="flex items-center gap-2 px-3 py-1.5 text-[10px]">
          <span class="inline-flex shrink-0 items-center gap-1.5 font-semibold uppercase tracking-widest text-slate-300">
            <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: agentColor }" />Workflow
          </span>
          <span class="min-w-0 flex-1 truncate text-xs text-slate-200" :title="group.task?.detail || group.key">
            {{ group.task?.label || group.key }}
          </span>
          <span class="shrink-0 text-slate-500">
            <template v-for="([phase, n], i) in phaseCounts(group.agents)" :key="phase">{{ i ? ' · ' : '' }}{{ n }} {{ phase }}</template>
            <template v-if="!group.agents.length">between phases</template>
          </span>
          <span v-if="group.task" class="w-16 shrink-0 text-right font-mono text-[11px] tabular-nums text-slate-400">
            {{ duration(elapsedOf(group.task)) }}
          </span>
        </div>
        <div v-for="agent in group.agents" :key="agent.id" class="border-t border-edge/40">
          <div class="flex items-center gap-2 px-3 pt-1 text-[10.5px]">
            <span v-if="agent.phase" class="shrink-0 rounded border border-edge px-1 text-[9px] text-slate-400">{{ agent.phase }}</span>
            <span class="min-w-0 flex-1 truncate font-mono text-slate-300">{{ agent.label }}</span>
            <span class="shrink-0 text-[10px] text-slate-500">{{ agent.tool_total }} calls</span>
          </div>
          <ProcessOp
            v-for="op in agent.ops"
            :key="op.id"
            :op="op"
            :elapsed="elapsedOf(op)"
            :dim="dimOp(op)"
          />
          <p v-if="!agent.ops.length" class="px-3 pb-1 text-[10.5px] italic text-slate-500">thinking</p>
        </div>
      </section>

      <!-- background tasks -->
      <section v-if="background.length" class="rounded-md border border-dashed border-edge bg-navy/30">
        <div class="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
          Background · {{ background.length }}
        </div>
        <ProcessOp
          v-for="op in background"
          :key="op.id"
          :op="op"
          :elapsed="elapsedOf(op)"
          :dim="dimOp(op)"
        />
      </section>
    </div>

    <!-- services + identity -->
    <footer class="mt-auto flex flex-wrap items-center gap-1.5 border-t border-edge/70 px-4 py-2 text-[10px] text-slate-500">
      <span
        v-for="svc in session.services"
        :key="svc.pid"
        class="rounded border border-edge bg-panel-2 px-1.5 py-px font-mono"
        :title="`MCP server / helper - pid ${svc.pid}, ${megabytes(svc.mem_mb)} with its children`"
      >
        {{ svc.label }} <span class="text-slate-600">{{ megabytes(svc.mem_mb) }}</span>
      </span>
      <span v-if="session.services.length" class="text-slate-600">({{ megabytes(servicesMem) }} in MCP servers)</span>
      <span class="ml-auto font-mono text-slate-600" :title="session.session_id">
        {{ session.session_id.slice(0, 8) }} · v{{ session.version }}
      </span>
      <RouterLink
        v-if="!popout"
        :to="{ path: '/activity', query: { session: session.session_id } }"
        class="text-coral hover:underline"
      >
        Activity →
      </RouterLink>
    </footer>
  </article>
</template>
