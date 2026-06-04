import { onMounted, onUnmounted } from 'vue'

export async function apiFetch(path, options = {}) {
  let response
  try {
    response = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    throw new Error('backend-unreachable')
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (body.detail) detail = body.detail
    } catch {
      // non-JSON error body; keep the status text
    }
    throw new Error(detail)
  }
  return response.json()
}

/**
 * Run `fn` immediately and then every `intervalMs`, pausing while the
 * document is hidden and refreshing as soon as it becomes visible again.
 */
export function usePolling(fn, intervalMs) {
  let timer = null

  const start = () => {
    if (timer === null) {
      fn()
      timer = setInterval(fn, intervalMs)
    }
  }
  const stop = () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }
  const onVisibility = () => (document.hidden ? stop() : start())

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    start()
  })
  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisibility)
    stop()
  })
}
