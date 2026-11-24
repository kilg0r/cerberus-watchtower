<script setup>
// Maximize / restore toggle: History's file reader, the artifact preview.
// The slot is an optional text label beside the icon.
defineProps({
  expanded: { type: Boolean, default: false },
})

// heroicons arrows-pointing-out / arrows-pointing-in
const OUT =
  'M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15'
const IN =
  'M9 9V4.5M9 9H4.5M9 9 3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5 5.25 5.25'
</script>

<template>
  <button
    type="button"
    class="flex shrink-0 items-center gap-1.5 rounded p-1 text-slate-400 transition-colors hover:bg-panel-2 hover:text-slate-200"
  >
    <svg class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" stroke-width="1.6" viewBox="0 0 24 24" aria-hidden="true">
      <path stroke-linecap="round" stroke-linejoin="round" :d="expanded ? IN : OUT" />
    </svg>
    <slot />
  </button>
</template>
