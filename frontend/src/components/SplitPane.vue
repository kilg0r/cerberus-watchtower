<script setup>
import { computed, ref } from 'vue'

// Two-pane resizable layout with a drag handle. The ratio is a fraction of
// the first pane, persisted to localStorage per storageKey, so every split
// remembers its size across sessions. Double-click the handle to reset.
const props = defineProps({
  direction: { type: String, default: 'horizontal' }, // horizontal = side by side
  storageKey: { type: String, required: true },
  initial: { type: Number, default: 0.5 },
  min: { type: Number, default: 0.12 },
  max: { type: Number, default: 0.88 },
})

const KEY = `watchtower:split:${props.storageKey}`
const stored = Number(localStorage.getItem(KEY))
const ratio = ref(stored >= props.min && stored <= props.max ? stored : props.initial)
const container = ref(null)
const dragging = ref(false)

const isHorizontal = computed(() => props.direction === 'horizontal')

function onPointerDown(event) {
  dragging.value = true
  event.target.setPointerCapture(event.pointerId)
}

function onPointerMove(event) {
  if (!dragging.value || !container.value) return
  const rect = container.value.getBoundingClientRect()
  const fraction = isHorizontal.value
    ? (event.clientX - rect.left) / rect.width
    : (event.clientY - rect.top) / rect.height
  ratio.value = Math.min(props.max, Math.max(props.min, fraction))
}

function onPointerUp() {
  if (!dragging.value) return
  dragging.value = false
  localStorage.setItem(KEY, String(ratio.value))
}

function reset() {
  ratio.value = props.initial
  localStorage.setItem(KEY, String(ratio.value))
}
</script>

<template>
  <div
    ref="container"
    class="flex min-h-0 min-w-0"
    :class="[
      isHorizontal ? 'flex-row' : 'flex-col',
      dragging ? (isHorizontal ? 'cursor-col-resize select-none' : 'cursor-row-resize select-none') : '',
    ]"
  >
    <div
      class="min-h-0 min-w-0"
      :style="{ flex: `0 1 calc(${ratio * 100}% - 3px)` }"
    >
      <slot name="first" />
    </div>
    <div
      class="group flex shrink-0 items-center justify-center"
      :class="isHorizontal ? 'w-1.5 cursor-col-resize px-px' : 'h-1.5 cursor-row-resize py-px'"
      title="drag to resize - double-click to reset"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @dblclick="reset"
    >
      <div
        class="rounded-full transition-colors"
        :class="
          isHorizontal
            ? dragging
              ? 'h-full w-0.5 bg-coral'
              : 'h-full w-px bg-edge group-hover:w-0.5 group-hover:bg-coral/60'
            : dragging
              ? 'h-0.5 w-full bg-coral'
              : 'h-px w-full bg-edge group-hover:h-0.5 group-hover:bg-coral/60'
        "
      />
    </div>
    <div class="min-h-0 min-w-0 flex-1">
      <slot name="second" />
    </div>
  </div>
</template>
