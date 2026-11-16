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
  // false: scale between the window's min and max (memory moves slowly, so a
  // zero-based line would always read flat); `floor` is then the minimum range
  zero: { type: Boolean, default: true },
})

const points = computed(() => {
  const v = props.values
  if (v.length < 2) return ''
  const high = Math.max(...v)
  const low = props.zero ? 0 : Math.min(...v)
  const range = props.zero ? Math.max(props.floor, high) : Math.max(props.floor, high - low)
  const bottom = props.zero ? 0 : (high + low) / 2 - range / 2
  const step = props.width / (v.length - 1)
  return v
    .map((value, i) => `${(i * step).toFixed(1)},${(props.height - 1 - ((value - bottom) / range) * (props.height - 2)).toFixed(1)}`)
    .join(' ')
})
</script>

<template>
  <svg :width="width" :height="height" class="inline-block align-middle" aria-hidden="true">
    <line :x1="0" :x2="width" :y1="height - 0.5" :y2="height - 0.5" stroke="#1e3050" stroke-width="1" />
    <polyline v-if="points" :points="points" fill="none" stroke="#94a3b8" stroke-width="1.5" stroke-linejoin="round" />
  </svg>
</template>
