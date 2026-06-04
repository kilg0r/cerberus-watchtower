import { ref } from 'vue'

// Drag-to-resize for a panel's edge (drawers, overlays). Returns a pixel size
// persisted to localStorage and pointer handlers to bind on the grip element.
// edge: which side of the panel the grip sits on ('left' grows the panel as
// you drag left, 'right' grows it as you drag right).
export function useEdgeResize(storageKey, { initial, min, max, edge = 'left' }) {
  const KEY = `watchtower:size:${storageKey}`
  const stored = Number(localStorage.getItem(KEY))
  const size = ref(stored >= min && stored <= max ? stored : initial)
  const dragging = ref(false)
  let startX = 0
  let startSize = 0

  function onPointerDown(event) {
    dragging.value = true
    startX = event.clientX
    startSize = size.value
    event.target.setPointerCapture(event.pointerId)
  }

  function onPointerMove(event) {
    if (!dragging.value) return
    const delta = edge === 'left' ? startX - event.clientX : event.clientX - startX
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
