<script setup>
defineProps({
  additions: { type: Number, default: 0 },
  deletions: { type: Number, default: 0 },
})
</script>

<template>
  <span class="inline-flex items-center gap-1.5 font-mono text-xs">
    <span class="text-emerald-400">+{{ additions }}</span>
    <span class="text-red-400">&minus;{{ deletions }}</span>
  </span>
</template>
