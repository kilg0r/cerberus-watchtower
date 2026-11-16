import { onBeforeUnmount, onMounted, ref } from 'vue'

// The live content width of an element - charts redraw at their real size.
export function useElementWidth(target) {
  const width = ref(0)
  let observer = null
  onMounted(() => {
    width.value = target.value?.clientWidth || 0
    observer = new ResizeObserver(([entry]) => {
      width.value = entry.contentRect.width
    })
    if (target.value) observer.observe(target.value)
  })
  onBeforeUnmount(() => observer?.disconnect())
  return width
}
