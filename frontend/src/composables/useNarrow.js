import { onBeforeUnmount, ref } from 'vue'

// Phone layout switch: true below Tailwind's md breakpoint (48rem), where split
// panes show one at a time. (The bottom tab bar reaches further - touch tablets
// too, see the `tabnav` variant in style.css.)
export const NARROW_QUERY = '(max-width: 47.99rem)'

export function useNarrow() {
  const query = window.matchMedia(NARROW_QUERY)
  const narrow = ref(query.matches)
  const onChange = (event) => (narrow.value = event.matches)
  query.addEventListener('change', onChange)
  onBeforeUnmount(() => query.removeEventListener('change', onChange))
  return narrow
}
