import { ref } from 'vue'

// Drag-to-resize for a panel's edge (drawers, overlays, message boxes).
// Returns a pixel size persisted to localStorage and pointer handlers to bind
// on the grip element. edge: which side of the panel the grip sits on -
// 'left' / 'top' grow the panel as you drag left / up, 'right' / 'bottom' as
// you drag right / down.
export function useEdgeResize(storageKey, { initial, min, max, edge = 'left' }) {
  const KEY = `watchtower:size:${storageKey}`
  const stored = Number(localStorage.getItem(KEY))
  const size = ref(stored >= min && stored <= max ? stored : initial)
  const dragging = ref(false)
  const vertical = edge === 'top' || edge === 'bottom'
  const grows = edge === 'left' || edge === 'top' ? -1 : 1
  let start = 0
  let startSize = 0

  function onPointerDown(event) {
    dragging.value = true
    start = vertical ? event.clientY : event.clientX
    startSize = size.value
    event.target.setPointerCapture(event.pointerId)
  }

  function onPointerMove(event) {
    if (!dragging.value) return
    const delta = ((vertical ? event.clientY : event.clientX) - start) * grows
    size.value = Math.min(max, Math.max(min, startSize + delta))
  }

  function onPointerUp() {
    if (!dragging.value) return
    dragging.value = false
    localStorage.setItem(KEY, String(Math.round(size.value)))
  }

  function reset() {
    size.value = initial
    localStorage.setItem(KEY, String(initial))
  }

  return { size, dragging, onPointerDown, onPointerMove, onPointerUp, reset }
}
