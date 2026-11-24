<script setup>
import { ARTIFACT_THEMES, useArtifactTheme } from '../composables/useArtifactTheme'

// Auto / Light / Dark for artifact previews - one app-wide choice (the
// Artifacts view shows it above the preview and in its full-screen bar).
const theme = useArtifactTheme()
</script>

<template>
  <div class="flex shrink-0 overflow-hidden rounded-md border border-edge" role="radiogroup" aria-label="Preview theme">
    <button
      v-for="(t, i) in ARTIFACT_THEMES"
      :key="t.value"
      role="radio"
      :aria-checked="theme === t.value"
      class="px-2.5 py-1 text-[11px] transition-colors"
      :class="[
        i ? 'border-l border-edge' : '',
        theme === t.value ? 'bg-coral/10 text-coral' : 'text-slate-400 hover:text-slate-200',
      ]"
      :title="t.hint"
      @click="theme = t.value"
    >
      {{ t.label }}
    </button>
  </div>
</template>
