<script setup>
// A tiny single-series trend line (CPU over the last polls). One series, so
// no legend: the label beside it names it.
import { computed } from 'vue'

const props = defineProps({
  values: { type: Array, default: () => [] },
  width: { type: Number, default: 56 },
  height: { type: Number, default: 14 },
  // the y-scale never shrinks below this, so idle noise stays flat
  floor: { type: Number, default: 5 },
})

const points = computed(() => {
  const v = props.values
  if (v.length < 2) return ''
  const max = Math.max(props.floor, ...v)
  const step = props.width / (v.length - 1)
  return v
    .map((value, i) => `${(i * step).toFixed(1)},${(props.height - 1 - (value / max) * (props.height - 2)).toFixed(1)}`)
    .join(' ')
})
</script>

<template>
  <svg :width="width" :height="height" class="inline-block align-middle" aria-hidden="true">
    <line :x1="0" :x2="width" :y1="height - 0.5" :y2="height - 0.5" stroke="#1e3050" stroke-width="1" />
    <polyline v-if="points" :points="points" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-linejoin="round" />
  </svg>
</template>
