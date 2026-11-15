<script setup>
import { computed, ref, watch } from 'vue'
import { marked } from 'marked'
import { useArtifactTheme } from '../composables/useArtifactTheme'

// Local copy of an artifact, rendered in a sandboxed iframe. HTML is served
// by the backend under a CSP sandbox (opaque origin: scripts run, but can't
// reach Watchtower's API or storage); markdown is rendered here into a
// script-less srcdoc frame, so nothing in it can execute. Light/dark: HTML
// comes from the themed route (the server forces the theme), markdown gets
// matching styles here.
const props = defineProps({
  artifact: { type: Object, required: true },
})
const theme = useArtifactTheme()

const markdownDoc = ref(null)
const loadError = ref(null)

const isMarkdown = computed(() => /\.(md|markdown)$/i.test(props.artifact.content_file || ''))
const contentUrl = computed(() =>
  props.artifact.content_file
    ? `/api/artifacts/${props.artifact.id}/content/${encodeURI(props.artifact.content_file)}`
    : null
)
const pageUrl = computed(() => {
  if (!props.artifact.content_file) return null
  if (theme.value === 'auto') return contentUrl.value
  return `/api/artifacts/${props.artifact.id}/view/${theme.value}/${encodeURI(props.artifact.content_file)}`
})
// the frame's own background, seen while a page loads and around short ones
const FRAME_BG = { light: 'bg-white', dark: 'bg-[#1a1a19]', auto: 'bg-panel' }

const MD_VARS = {
  light: '--bg:#ffffff;--fg:#1e293b;--head:#0f172a;--code:#f1f5f9;--rule:#cbd5e1;--muted:#475569;--link:#c2544a;color-scheme:light',
  dark: '--bg:#1a1a19;--fg:#e7e5e4;--head:#fafaf9;--code:#262624;--rule:#3f3f3c;--muted:#a8a29e;--link:#ff8a7e;color-scheme:dark',
}
const mdStyle = (mode) => {
  const vars =
    mode === 'auto'
      ? `:root{${MD_VARS.light}} @media (prefers-color-scheme: dark){:root{${MD_VARS.dark}}}`
      : `:root{${MD_VARS[mode]}}`
  return `${vars}
  body { font: 14px/1.6 "Inter", system-ui, sans-serif; background: var(--bg); color: var(--fg); max-width: 860px; margin: 0 auto; padding: 28px 32px; }
  h1, h2, h3 { line-height: 1.25; color: var(--head); } h1 { font-size: 1.7em; } h2 { margin-top: 1.8em; }
  pre { background: var(--code); padding: 12px; border-radius: 6px; overflow: auto; }
  code { font: 12.5px ui-monospace, "Cascadia Code", monospace; } table { border-collapse: collapse; }
  th, td { border: 1px solid var(--rule); padding: 5px 9px; text-align: left; } a { color: var(--link); }
  blockquote { border-left: 3px solid var(--rule); margin-left: 0; padding-left: 14px; color: var(--muted); }`
}
const markdownHtml = ref(null)

watch(
  () => [props.artifact.id, props.artifact.content_file, props.artifact.updated_at],
  async () => {
    markdownHtml.value = null
    loadError.value = null
    if (!isMarkdown.value || !contentUrl.value) return
    try {
      const response = await fetch(contentUrl.value)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      markdownHtml.value = marked.parse(await response.text())
    } catch (err) {
      loadError.value = err.message
    }
  },
  { immediate: true }
)
watch(
  [markdownHtml, theme],
  ([html, mode]) => {
    markdownDoc.value = html === null
      ? null
      : `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>${mdStyle(mode)}</style></head><body>${html}</body></html>`
  },
  { immediate: true }
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-edge" :class="FRAME_BG[theme]">
    <p v-if="loadError" class="p-4 text-xs text-red-500">Could not load the local copy: {{ loadError }}</p>
    <iframe
      v-else-if="isMarkdown && markdownDoc"
      :key="`md-${artifact.id}-${artifact.updated_at}-${theme}`"
      :srcdoc="markdownDoc"
      sandbox="allow-popups allow-popups-to-escape-sandbox"
      class="h-full w-full flex-1 border-0"
      title="Artifact preview"
    />
    <iframe
      v-else-if="!isMarkdown && contentUrl"
      :key="`html-${artifact.id}-${artifact.updated_at}-${theme}`"
      :src="pageUrl"
      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals"
      class="h-full w-full flex-1 border-0"
      title="Artifact preview"
    />
  </div>
</template>
