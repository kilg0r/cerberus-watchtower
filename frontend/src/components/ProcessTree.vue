<script setup>
// One OS process and what it started, as an indented tree: the shell a
// tool call runs in, then dotnet -> vstest -> testhost and so on.
import { computed } from 'vue'
import { categoryOf } from '../utils/processCategories'
import { megabytes } from '../utils/time'

defineOptions({ name: 'ProcessTree' })

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
  // the root shell's command is usually the call's own detail line already
  hideCmd: { type: Boolean, default: false },
})

const category = computed(() => categoryOf(props.node.category))
const busy = computed(() => props.node.cpu >= 1)
</script>

<template>
  <div>
    <div
      class="flex items-center gap-2 py-px font-mono text-[10.5px] leading-4"
      :style="{ paddingLeft: depth * 14 + 'px' }"
      :title="node.cmd || node.name"
    >
      <span class="shrink-0 text-slate-600">└</span>
      <span
        class="h-1.5 w-1.5 shrink-0 rounded-full"
        :class="busy ? 'animate-pulse' : ''"
        :style="{ backgroundColor: category.color }"
      />
      <span class="shrink-0 text-slate-300 max-md:min-w-0 max-md:shrink max-md:truncate">{{ node.name }}</span>
      <span class="shrink-0 text-slate-600 max-md:hidden">{{ node.pid }}</span>
      <span class="min-w-0 flex-1 truncate text-slate-500">{{ hideCmd ? '' : node.cmd }}</span>
      <span class="w-12 shrink-0 text-right tabular-nums" :class="busy ? 'text-slate-200' : 'text-slate-600'">
        {{ node.cpu.toFixed(1) }}%
      </span>
      <span class="w-14 shrink-0 text-right tabular-nums text-slate-500">{{ megabytes(node.mem_mb) }}</span>
    </div>
    <ProcessTree v-for="child in node.children" :key="child.pid" :node="child" :depth="depth + 1" />
  </div>
</template>
