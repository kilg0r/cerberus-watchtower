import { onBeforeUnmount, ref } from 'vue'

// Phone layout switch: true below Tailwind's md breakpoint (48rem), the width
// at which the sidebar turns into the bottom tab bar (App.vue).
export const NARROW_QUERY = '(max-width: 47.99rem)'

export function useNarrow() {
  const query = window.matchMedia(NARROW_QUERY)
  const narrow = ref(query.matches)
  const onChange = (event) => (narrow.value = event.matches)
  query.addEventListener('change', onChange)
  onBeforeUnmount(() => query.removeEventListener('change', onChange))
  return narrow
}
