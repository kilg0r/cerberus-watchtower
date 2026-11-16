<script setup>
// Where the machine's RAM goes right now: one tall segmented bar (Claude,
// its MCP servers, its tool processes, everything else, free), then the MCP
// servers summed across every session - each session starts its own copies.
import { computed, ref } from 'vue'
import { useElementWidth } from '../composables/useElementWidth'
import { MEMORY_PARTS } from '../utils/processCategories'
import { megabytes } from '../utils/time'

const props = defineProps({
  host: { type: Object, required: true },
  totals: { type: Object, required: true },
  sessions: { type: Array, required: true },
})

const GAP = 2
const bar = ref(null)
const width = useElementWidth(bar)

const totalMb = computed(() => props.host.mem_total_gb * 1024)
const usedMb = computed(() => props.host.mem_used_gb * 1024)
const claudeMb = computed(() => props.totals.cli_mb + props.totals.mcp_mb + props.totals.tools_mb)

const parts = computed(() => {
  const mb = {
    cli: props.totals.cli_mb,
    mcp: props.totals.mcp_mb,
    tools: props.totals.tools_mb,
    // resident sizes overlap a little (shared pages), so clamp rather than go negative
    other: Math.max(0, usedMb.value - claudeMb.value),
  }
  const free = Math.max(0, totalMb.value - Math.max(usedMb.value, claudeMb.value))
  const all = [
    ...MEMORY_PARTS.map((p) => ({ ...p, mb: mb[p.key] })),
    { key: 'free', label: 'Free', color: null, hint: 'available', mb: free },
  ].filter((p) => p.mb > 0)
  const room = Math.max(0, width.value - GAP * (all.length - 1))
  const sum = all.reduce((n, p) => n + p.mb, 0) || 1
  let x = 0
  return all.map((p) => {
    const px = (p.mb / sum) * room
    const text = megabytes(p.mb)
    const part = { ...p, x, px, share: p.mb / totalMb.value, text, printed: px >= text.length * 6.6 + 14 }
    x += px + GAP
    return part
  })
})

const mcp = computed(() => {
  const byLabel = new Map()
  for (const s of props.sessions) {
    for (const svc of s.services) {
      const entry = byLabel.get(svc.label) || { label: svc.label, count: 0, mb: 0 }
      entry.count += 1
      entry.mb += svc.mem_mb
      byLabel.set(svc.label, entry)
    }
  }
  return [...byLabel.values()].sort((a, b) => b.mb - a.mb)
})
const mcpShown = computed(() => mcp.value.slice(0, 4))
const mcpMax = computed(() => Math.max(1, ...mcp.value.map((m) => m.mb)))
const mcpRest = computed(() => mcp.value.slice(4).reduce((n, m) => n + m.mb, 0))

const hover = ref(null)
const percent = (share) => `${Math.round(share * 100)}%`
const claudeShare = computed(() => (usedMb.value ? claudeMb.value / usedMb.value : 0))
</script>

<template>
  <section class="rounded-lg border border-edge bg-panel px-4 py-3">
    <div class="mb-2 flex items-baseline justify-between gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-widest text-slate-400">Where the RAM goes</h3>
      <span class="text-[11px] text-slate-500">{{ percent(host.mem_percent / 100) }} in use</span>
    </div>

    <div class="mb-2 flex items-baseline gap-2">
      <span class="text-2xl font-semibold text-white">{{ host.mem_used_gb.toFixed(1) }}</span>
      <span class="text-sm text-slate-400">of {{ host.mem_total_gb.toFixed(1) }} GB</span>
      <span class="ml-auto text-right text-[11px] text-slate-400">
        Claude + servers + tools <span class="font-semibold text-slate-200">{{ megabytes(claudeMb) }}</span>
        <span class="text-slate-500"> · {{ percent(claudeShare) }} of what's in use</span>
      </span>
    </div>

    <!-- tall segmented bar: one block per part -->
    <div
      ref="bar"
      class="relative flex h-7"
      :style="{ gap: `${GAP}px` }"
      role="img"
      :aria-label="parts.map((p) => `${p.label} ${p.text} (${percent(p.share)})`).join(', ')"
    >
      <div
        v-for="p in parts"
        :key="p.key"
        class="flex h-full items-center overflow-visible rounded px-2 text-[11px] font-semibold tabular-nums outline-none transition-[filter] focus-visible:ring-1 focus-visible:ring-white/70"
        :class="[p.color ? 'text-white' : 'bg-panel-2 font-normal text-slate-400', hover?.key === p.key ? 'brightness-125' : '']"
        :style="{ width: `${p.px}px`, flex: 'none', backgroundColor: p.color || undefined }"
        tabindex="0"
        @pointerenter="hover = p"
        @pointerleave="hover = null"
        @focus="hover = p"
        @blur="hover = null"
      >
        <span v-if="p.printed" class="whitespace-nowrap">{{ p.text }}</span>
      </div>
      <div
        v-if="hover"
        class="pointer-events-none absolute bottom-full z-10 mb-1.5 rounded-md border border-edge bg-navy/95 px-2.5 py-1.5 text-[11px] shadow-lg"
        :style="{ left: `${Math.max(0, Math.min(hover.x, width - 200))}px` }"
      >
        <div><span class="font-semibold text-white">{{ hover.text }}</span> <span class="text-slate-400">{{ hover.label }}</span></div>
        <div class="text-[10px] text-slate-500">{{ percent(hover.share) }} of {{ host.mem_total_gb.toFixed(1) }} GB · {{ hover.hint }}</div>
      </div>
    </div>

    <!-- legend: names in block order; amounts only where the block had no room -->
    <div class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
      <span v-for="p in parts" :key="p.key" class="inline-flex items-center gap-1.5" :title="p.hint">
        <span
          class="h-2.5 w-2.5 rounded-[3px]"
          :class="p.color ? '' : 'border border-edge bg-panel-2'"
          :style="p.color ? { backgroundColor: p.color } : {}"
        />
        {{ p.label }}<span v-if="!p.printed" class="tabular-nums text-slate-300">{{ p.text }}</span>
      </span>
    </div>

    <!-- MCP servers across sessions -->
    <div v-if="mcp.length" class="mt-3 border-t border-edge/70 pt-2">
      <div class="mb-1 text-[10px] font-semibold uppercase tracking-widest text-slate-500">MCP servers &amp; helpers · all sessions</div>
      <div
        v-for="m in mcpShown"
        :key="m.label"
        class="grid items-center gap-2 py-0.5 text-[11px] [grid-template-columns:minmax(0,9rem)_1fr_4.5rem]"
        :title="`${m.count} running ${m.label} instance${m.count === 1 ? '' : 's'}, ${megabytes(m.mb / m.count)} each on average`"
      >
        <span class="truncate text-slate-300">
          {{ m.label }} <span class="text-slate-500">×{{ m.count }}</span>
        </span>
        <span class="h-2 min-w-0">
          <span
            class="block h-full rounded-r"
            :style="{ width: `${Math.max(1, (m.mb / mcpMax) * 100)}%`, backgroundColor: MEMORY_PARTS[1].color }"
          />
        </span>
        <span class="text-right tabular-nums text-slate-300">{{ megabytes(m.mb) }}</span>
      </div>
      <div v-if="mcpRest" class="pt-0.5 text-[10px] text-slate-500">+ {{ mcp.length - 4 }} more · {{ megabytes(mcpRest) }}</div>
    </div>
  </section>
</template>
