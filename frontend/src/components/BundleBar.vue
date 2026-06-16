<script setup>
import { computed } from 'vue'

// Bottom bar summarizing the selected commits and how to combine them.
// `selected` is in newest-first order (the commit list order).
const props = defineProps({
  selected: { type: Array, required: true },
  mode: { type: String, default: 'range' }, // 'range' | 'set'
})
const emit = defineEmits(['update:mode', 'review', 'clear', 'remove'])

const rangeLabel = computed(() => {
  if (props.selected.length < 1) return ''
  const head = props.selected[0]
  const base = props.selected[props.selected.length - 1]
  return `${base.short}..${head.short}`
})
</script>

<template>
  <div
    v-if="selected.length"
    class="flex flex-wrap items-center gap-3 rounded-lg border border-coral/40 bg-panel-2 px-3 py-2"
  >
    <span class="text-xs font-semibold text-slate-200">
      {{ selected.length }} commit{{ selected.length === 1 ? '' : 's' }} selected
    </span>

    <div class="flex items-center gap-1 rounded-md border border-edge p-0.5">
      <button
        v-for="m in ['range', 'set']"
        :key="m"
        class="rounded px-2 py-0.5 text-[11px] capitalize transition-colors"
        :class="mode === m ? 'bg-coral/20 text-coral' : 'text-slate-400 hover:text-slate-200'"
        @click="emit('update:mode', m)"
      >
        {{ m }}
      </button>
    </div>

    <span v-if="mode === 'range'" class="font-mono text-[11px] text-slate-500" :title="rangeLabel">
      {{ rangeLabel }}
    </span>
    <span v-else class="text-[11px] text-slate-500">cherry-picked patch union</span>

    <div class="flex min-w-0 flex-1 flex-wrap gap-1">
      <span
        v-for="c in selected"
        :key="c.sha"
        class="group inline-flex items-center gap-1 rounded-full border border-edge bg-panel px-2 py-0.5 font-mono text-[10px] text-slate-400"
      >
        {{ c.short }}
        <button class="text-slate-600 hover:text-red-400" title="remove" @click="emit('remove', c)">
          &times;
        </button>
      </span>
    </div>

    <button
      class="rounded-md border border-edge px-3 py-1 text-xs text-slate-400 transition-colors hover:border-coral/40 hover:text-coral"
      @click="emit('clear')"
    >
      Clear
    </button>
    <button
      class="rounded-md bg-coral/20 px-3 py-1 text-xs font-medium text-coral transition-colors hover:bg-coral/30"
      @click="emit('review')"
    >
      Review &rarr;
    </button>
  </div>
</template>
