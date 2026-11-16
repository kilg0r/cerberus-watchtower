<script setup>
// Cards of different heights packed into columns without holes: each card,
// in order, goes under the currently shortest column, so the first cards
// stay top-left. Every card is absolutely positioned inside one list, so a
// card that changes column moves (and keeps its state) instead of remounting.
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { useElementWidth } from '../composables/useElementWidth'

const props = defineProps({
  items: { type: Array, required: true },
  itemKey: { type: Function, required: true },
  minWidth: { type: Number, default: 460 },
  gap: { type: Number, default: 16 },
})

const root = ref(null)
const width = useElementWidth(root)
const heights = reactive({})
const settled = ref(false) // animate moves, but not the first placement

const columns = computed(() => Math.max(1, Math.floor((width.value + props.gap) / (props.minWidth + props.gap))))
const columnWidth = computed(() => Math.max(0, (width.value - props.gap * (columns.value - 1)) / columns.value))

const layout = computed(() => {
  const tops = new Array(columns.value).fill(0)
  const at = {}
  for (const item of props.items) {
    const key = props.itemKey(item)
    let column = 0
    // near-ties go left so a row of equal cards reads in order
    for (let c = 1; c < tops.length; c++) if (tops[c] < tops[column] - 1) column = c
    at[key] = { x: column * (columnWidth.value + props.gap), y: tops[column] }
    tops[column] += (heights[key] ?? 0) + props.gap
  }
  return { at, height: Math.max(0, Math.max(...tops) - props.gap) }
})

const elements = new Map()
const observer = new ResizeObserver((entries) => {
  for (const entry of entries) {
    const key = entry.target.dataset.key
    heights[key] = entry.borderBoxSize?.[0]?.blockSize ?? entry.target.offsetHeight
  }
  if (!settled.value) requestAnimationFrame(() => (settled.value = true))
})
function track(key, el) {
  const previous = elements.get(key)
  if (previous === el) return
  if (previous) observer.unobserve(previous)
  if (el) {
    elements.set(key, el)
    observer.observe(el)
  } else {
    elements.delete(key)
    delete heights[key]
  }
}
onBeforeUnmount(() => observer.disconnect())
</script>

<template>
  <div ref="root" class="relative" :style="{ height: `${layout.height}px` }">
    <div
      v-for="item in items"
      :key="itemKey(item)"
      :ref="(el) => track(String(itemKey(item)), el)"
      :data-key="itemKey(item)"
      class="absolute left-0 top-0"
      :class="settled ? 'transition-transform duration-300 ease-out' : ''"
      :style="{
        width: `${columnWidth}px`,
        transform: `translate(${layout.at[itemKey(item)]?.x ?? 0}px, ${layout.at[itemKey(item)]?.y ?? 0}px)`,
        visibility: heights[itemKey(item)] == null ? 'hidden' : 'visible',
      }"
    >
      <slot :item="item" />
    </div>
  </div>
</template>
