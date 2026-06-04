<script setup>
import { computed } from 'vue'

const props = defineProps({
  diff: { type: String, required: true },
})

function lineClass(line) {
  if (line.startsWith('+++') || line.startsWith('---')) return 'text-slate-400 font-semibold'
  if (line.startsWith('@@')) return 'text-sky-400'
  if (line.startsWith('+')) return 'bg-emerald-500/10 text-emerald-300'
  if (line.startsWith('-')) return 'bg-red-500/10 text-red-300'
  if (line.startsWith('diff ') || line.startsWith('index ')) return 'text-slate-500'
  return 'text-slate-400'
}

const lines = computed(() =>
  props.diff.replace(/\n$/, '').split('\n').map((text, i) => ({ text, key: i }))
)
</script>

<template>
  <div class="max-h-96 overflow-auto rounded-md border border-edge bg-navy/80">
    <pre class="p-3 font-mono text-xs leading-5"><code><span
      v-for="line in lines"
      :key="line.key"
      class="block whitespace-pre px-1"
      :class="lineClass(line.text)"
    >{{ line.text || ' ' }}</span></code></pre>
  </div>
</template>
