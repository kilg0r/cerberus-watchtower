<script setup>
// A small line chart of the recent past. One y-scale (never two), lines break
// where nobody was sampling (samples only exist while this page is open), and
// a crosshair snaps to the nearest sample and reads out every series.
import { computed, ref } from 'vue'
import { useElementWidth } from '../composables/useElementWidth'
import { duration } from '../utils/time'

const props = defineProps({
  title: { type: String, required: true },
  t: { type: Array, default: () => [] }, // sample times, ms
  // [{ key, label, color, values, wash }] - values align with t
  series: { type: Array, required: true },
  max: { type: Number, required: true },
  format: { type: Function, required: true },
  tick: { type: Function, default: null },
  now: { type: Number, required: true },
  height: { type: Number, default: 136 },
})

const MIN_SPAN = 5 * 60e3
const MAX_SPAN = 60 * 60e3
const GAP = 20e3 // a longer silence breaks the line
const PAD = { left: 38, right: 104, top: 10, bottom: 20 }
const SURFACE = '#111f33'

const box = ref(null)
const width = useElementWidth(box)
const plotW = computed(() => Math.max(0, width.value - PAD.left - PAD.right))
const plotH = computed(() => props.height - PAD.top - PAD.bottom)
const base = computed(() => PAD.top + plotH.value)

const domain = computed(() => {
  const end = props.now
  const first = props.t.length ? props.t[0] : end
  return [end - Math.min(MAX_SPAN, Math.max(MIN_SPAN, end - first)), end]
})
const x = (ms) => PAD.left + ((ms - domain.value[0]) / (domain.value[1] - domain.value[0])) * plotW.value
const y = (v) => base.value - (Math.min(Math.max(v, 0), props.max) / props.max) * plotH.value

// indices of samples inside the window
const visible = computed(() => {
  const out = []
  for (let i = 0; i < props.t.length; i++) if (props.t[i] >= domain.value[0]) out.push(i)
  return out
})

const drawn = computed(() =>
  props.series.map((s) => {
    const segments = []
    let current = null
    let prev = null
    for (const i of visible.value) {
      const v = s.values[i]
      if (v == null) {
        current = null
        continue
      }
      if (current === null || props.t[i] - prev > GAP) segments.push((current = []))
      current.push([x(props.t[i]), y(v)])
      prev = props.t[i]
    }
    const pts = (seg) => seg.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join('L')
    const line = segments.map((seg) => `M${pts(seg)}`).join('')
    const area = s.wash
      ? segments
          .filter((seg) => seg.length > 1)
          .map((seg) => `M${seg[0][0].toFixed(1)},${base.value}L${pts(seg)}L${seg.at(-1)[0].toFixed(1)},${base.value}Z`)
          .join('')
      : ''
    const lastIndex = visible.value.findLast((i) => s.values[i] != null)
    const last = lastIndex == null ? null : { x: x(props.t[lastIndex]), y: y(s.values[lastIndex]), v: s.values[lastIndex] }
    return { ...s, line, area, last }
  })
)

// direct labels at the line ends, pushed apart (with a leader) when they'd collide
const endLabels = computed(() => {
  const labels = drawn.value
    .filter((s) => s.last)
    .map((s) => ({ key: s.key, color: s.color, text: `${s.label} ${props.format(s.last.v)}`, x: s.last.x, at: s.last.y, y: s.last.y }))
    .sort((a, b) => a.y - b.y)
  for (let i = 1; i < labels.length; i++) {
    const overlap = 12 - (labels[i].y - labels[i - 1].y)
    if (overlap > 0) {
      labels[i - 1].y -= overlap / 2
      labels[i].y += overlap / 2
    }
  }
  for (const label of labels) label.y = Math.min(Math.max(label.y, PAD.top + 4), base.value)
  return labels
})

const yTicks = computed(() => [0, props.max / 2, props.max].map((v) => ({ v, y: y(v), text: (props.tick || props.format)(v) })))
// whole-minute ticks counting back from now, about three of them
const STEPS = [1, 2, 5, 10, 15, 20, 30]
const xTicks = computed(() => {
  const [start, end] = domain.value
  const minutes = (end - start) / 60e3
  const step = STEPS.find((s) => minutes / s <= 3.2) || 30
  const ticks = []
  for (let k = 0; k * step <= minutes + 1e-6; k++) {
    const ms = end - k * step * 60e3
    ticks.push({ x: x(ms), text: k ? `-${k * step}m` : 'now', anchor: k ? 'middle' : 'end' })
  }
  const first = ticks.at(-1)
  if (ticks.length > 1 && first.x - PAD.left < 14) first.anchor = 'start'
  return ticks
})

// hover / keyboard crosshair
const hover = ref(null) // index into t
function nearest(ms) {
  let best = null
  for (const i of visible.value) if (best === null || Math.abs(props.t[i] - ms) < Math.abs(props.t[best] - ms)) best = i
  return best
}
function onMove(event) {
  const rect = box.value.getBoundingClientRect()
  const px = event.clientX - rect.left
  if (px < PAD.left - 6 || px > PAD.left + plotW.value + 6) return (hover.value = null)
  const ms = domain.value[0] + ((px - PAD.left) / plotW.value) * (domain.value[1] - domain.value[0])
  hover.value = nearest(ms)
}
function onKey(event) {
  const list = visible.value
  if (!list.length) return
  const at = hover.value == null ? list.length - 1 : list.indexOf(hover.value)
  if (event.key === 'ArrowLeft') hover.value = list[Math.max(0, at - 1)]
  else if (event.key === 'ArrowRight') hover.value = list[Math.min(list.length - 1, at + 1)]
  else return
  event.preventDefault()
}
const readout = computed(() => {
  const i = hover.value
  if (i == null) return null
  const px = x(props.t[i])
  return {
    x: px,
    flip: px > width.value - 190,
    time: new Date(props.t[i]).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' }),
    ago: duration((props.now - props.t[i]) / 1000),
    rows: props.series.map((s) => ({ key: s.key, label: s.label, color: s.color, value: s.values[i], y: s.values[i] == null ? null : y(s.values[i]) })),
  }
})
const summary = computed(() =>
  `${props.title}: ` + drawn.value.filter((s) => s.last).map((s) => `${s.label} ${props.format(s.last.v)}`).join(', ')
)
</script>

<template>
  <div class="flex min-w-0 flex-col">
    <div class="mb-1 flex items-baseline justify-between gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-widest text-slate-400">{{ title }}</h3>
      <div class="flex items-center gap-3 text-[11px] text-slate-400">
        <span v-for="s in series" :key="s.key" class="inline-flex items-center gap-1.5">
          <span class="h-0.5 w-3 rounded-full" :style="{ backgroundColor: s.color }" />{{ s.label }}
        </span>
      </div>
    </div>
    <div
      ref="box"
      class="relative outline-none focus-visible:ring-1 focus-visible:ring-slate-500/60"
      :style="{ height: `${height}px` }"
      tabindex="0"
      role="img"
      :aria-label="summary"
      @pointermove="onMove"
      @pointerleave="hover = null"
      @focus="hover = visible.at(-1) ?? null"
      @blur="hover = null"
      @keydown="onKey"
    >
      <svg v-if="width" :width="width" :height="height" class="block">
        <line
          v-for="tk in yTicks"
          :key="tk.v"
          :x1="PAD.left"
          :x2="PAD.left + plotW"
          :y1="tk.y + 0.5"
          :y2="tk.y + 0.5"
          stroke="#1e3050"
          stroke-width="1"
        />
        <text
          v-for="tk in yTicks"
          :key="`l${tk.v}`"
          :x="PAD.left - 6"
          :y="tk.y + 3.5"
          text-anchor="end"
          class="fill-slate-500 text-[10px] tabular-nums"
        >{{ tk.text }}</text>
        <text
          v-for="tk in xTicks"
          :key="tk.text"
          :x="tk.x"
          :y="height - 5"
          :text-anchor="tk.anchor"
          class="fill-slate-500 text-[10px] tabular-nums"
        >{{ tk.text }}</text>

        <template v-for="s in drawn" :key="s.key">
          <path v-if="s.area" :d="s.area" :fill="s.color" fill-opacity="0.1" />
          <path :d="s.line" fill="none" :stroke="s.color" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        </template>

        <!-- line ends: dot + direct label -->
        <template v-for="s in drawn" :key="`e${s.key}`">
          <circle v-if="s.last && hover == null" :cx="s.last.x" :cy="s.last.y" r="4" :fill="s.color" :stroke="SURFACE" stroke-width="2" />
        </template>
        <template v-for="label in endLabels" :key="`t${label.key}`">
          <line
            v-if="Math.abs(label.y - label.at) > 2"
            :x1="label.x + 5"
            :y1="label.at"
            :x2="label.x + 9"
            :y2="label.y - 3.5"
            stroke="#475569"
            stroke-width="1"
          />
          <text :x="label.x + 10" :y="label.y" class="fill-slate-300 text-[11px] tabular-nums">{{ label.text }}</text>
        </template>

        <!-- crosshair -->
        <template v-if="readout">
          <line :x1="readout.x" :x2="readout.x" :y1="PAD.top" :y2="base" stroke="#64748b" stroke-width="1" />
          <template v-for="row in readout.rows" :key="`h${row.key}`">
            <circle v-if="row.y != null" :cx="readout.x" :cy="row.y" r="4" :fill="row.color" :stroke="SURFACE" stroke-width="2" />
          </template>
        </template>
      </svg>

      <p
        v-if="width && !visible.length"
        class="absolute inset-0 flex items-center justify-center text-[11px] text-slate-500"
        :style="{ paddingLeft: `${PAD.left}px`, paddingRight: `${PAD.right}px` }"
      >
        samples build up while this page is open
      </p>

      <div
        v-if="readout"
        class="pointer-events-none absolute top-1 z-10 min-w-36 rounded-md border border-edge bg-navy/95 px-2.5 py-1.5 text-[11px] shadow-lg"
        :style="readout.flip ? { right: `${width - readout.x + 10}px` } : { left: `${readout.x + 10}px` }"
      >
        <div class="mb-1 text-[10px] text-slate-500">{{ readout.time }} · {{ readout.ago }} ago</div>
        <div v-for="row in readout.rows" :key="row.key" class="flex items-center gap-2">
          <span class="h-0.5 w-3 shrink-0 rounded-full" :style="{ backgroundColor: row.color }" />
          <span class="font-semibold tabular-nums text-white">{{ row.value == null ? '-' : format(row.value) }}</span>
          <span class="text-slate-400">{{ row.label }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
