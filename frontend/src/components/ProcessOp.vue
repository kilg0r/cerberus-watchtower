<script setup>
// One running tool call or background task: what kind of work it is (color
// stripe + labelled chip), what it's doing, how long it has run, the OS
// processes under it and - for background tasks - the last lines it wrote.
import { computed, ref } from 'vue'
import { categoryOf } from '../utils/processCategories'
import { duration } from '../utils/time'
import ProcessTree from './ProcessTree.vue'

const props = defineProps({
  op: { type: Object, required: true },
  elapsed: { type: Number, default: null },
  dim: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
})

const expanded = ref(false)
const category = computed(() => categoryOf(props.op.category))
const firstLine = (text) => (text || '').split('\n').find((l) => l.trim()) || ''
const title = computed(() => props.op.label || firstLine(props.op.detail) || props.op.tool)
const detailLine = computed(() => (props.op.label ? firstLine(props.op.detail) : ''))
const multiline = computed(() => (props.op.detail || '').includes('\n') || (props.op.detail || '').length > 140)
const TOOL_NAMES = { process: 'orphan', PowerShell: 'pwsh' }
const ORPHAN_HINT =
  'Still running, but no open tool call or background task owns it - usually left over from a call that timed out or was interrupted'
const toolName = computed(() => {
  const tool = props.op.tool
  if (tool.startsWith('mcp__')) return tool.split('__').slice(1).join(' · ')
  return TOOL_NAMES[tool] || tool
})
const waiting = computed(() => props.op.category === 'waiting')
</script>

<template>
  <div
    class="border-l-[3px] px-3 py-1.5 transition-opacity"
    :class="dim ? 'opacity-30' : ''"
    :style="{ borderColor: category.color }"
  >
    <div class="flex items-center gap-2">
      <span
        class="inline-flex shrink-0 items-center gap-1.5 rounded border border-edge bg-panel-2 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-slate-300"
      >
        <span v-if="waiting" class="text-[10px] leading-none" :style="{ color: category.color }">!</span>
        <span v-else class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: category.color }" />
        {{ category.label }}<span v-if="op.tag" class="font-normal normal-case text-slate-500">· {{ op.tag }}</span>
      </span>
      <span
        class="shrink-0 font-mono text-[10px]"
        :class="op.tool === 'process' ? 'text-amber-300/80' : 'text-slate-500'"
        :title="op.tool === 'process' ? ORPHAN_HINT : op.tool"
      >{{ toolName }}</span>
      <span class="min-w-0 flex-1 truncate text-xs text-slate-200" :title="title">{{ title }}</span>
      <span v-if="op.via" class="max-w-[30%] shrink-0 truncate text-[10px] text-slate-500" :title="`started by the subagent: ${op.via}`">via {{ op.via }}</span>
      <span v-if="op.task_id" class="shrink-0 font-mono text-[9px] text-slate-600" title="background task id">{{ op.task_id }}</span>
      <span class="w-16 shrink-0 text-right font-mono text-[11px] tabular-nums text-slate-400">{{ duration(elapsed) }}</span>
    </div>

    <template v-if="!compact">
      <button
        v-if="detailLine"
        class="mt-0.5 block w-full truncate text-left font-mono text-[10.5px] text-slate-500 hover:text-slate-300"
        :title="multiline ? 'click to show the whole command' : op.detail"
        @click="expanded = !expanded"
      >
        {{ multiline ? (expanded ? '▾ ' : '▸ ') : '' }}{{ detailLine }}
      </button>
      <pre
        v-if="expanded"
        class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-navy/80 px-2 py-1.5 font-mono text-[10.5px] leading-4 text-slate-400"
      >{{ op.detail }}</pre>

      <div v-if="op.processes?.length" class="mt-1">
        <ProcessTree
          v-for="(proc, i) in op.processes"
          :key="proc.pid"
          :node="proc"
          :hide-cmd="i === 0 && !!op.detail && (proc.cmd || '').startsWith(firstLine(op.detail).slice(0, 60))"
        />
      </div>

      <div
        v-if="op.tail?.length"
        class="mt-1 rounded bg-black/30 px-2 py-1 font-mono text-[10px] leading-4 text-slate-400"
        title="latest output"
      >
        <div v-for="(line, i) in op.tail" :key="i" class="truncate">{{ line }}</div>
      </div>
    </template>
  </div>
</template>
