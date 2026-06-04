import { onMounted, onUnmounted, ref } from 'vue'

/**
 * Live event feed over SSE. EventSource reconnects automatically;
 * `connected` drives the live/offline indicator.
 */
export function useEventStream(maxEvents = 200) {
  const liveEvents = ref([])
  const connected = ref(false)
  let source = null

  onMounted(() => {
    source = new EventSource('/api/events/stream')
    source.onopen = () => (connected.value = true)
    source.onerror = () => (connected.value = false)
    source.onmessage = (message) => {
      try {
        liveEvents.value.unshift(JSON.parse(message.data))
        if (liveEvents.value.length > maxEvents) liveEvents.value.pop()
      } catch {
        // malformed line; skip
      }
    }
  })
  onUnmounted(() => source && source.close())

  return { liveEvents, connected }
}
