<script setup>
import { computed, ref, watch } from 'vue'
import { marked } from 'marked'

// Local copy of an artifact, rendered in a sandboxed iframe. HTML is served
// by the backend under a CSP sandbox (opaque origin: scripts run, but can't
// reach Watchtower's API or storage); markdown is rendered here into a
// script-less srcdoc frame, so nothing in it can execute.
const props = defineProps({
  artifact: { type: Object, required: true },
})

const markdownDoc = ref(null)
const loadError = ref(null)

const isMarkdown = computed(() => /\.(md|markdown)$/i.test(props.artifact.content_file || ''))
const contentUrl = computed(() =>
  props.artifact.content_file
    ? `/api/artifacts/${props.artifact.id}/content/${encodeURI(props.artifact.content_file)}`
    : null
)

const MD_STYLE = `
  body { font: 14px/1.6 "Inter", system-ui, sans-serif; color: #1e293b; max-width: 860px; margin: 0 auto; padding: 28px 32px; }
  h1, h2, h3 { line-height: 1.25; color: #0f172a; } h1 { font-size: 1.7em; } h2 { margin-top: 1.8em; }
  pre { background: #f1f5f9; padding: 12px; border-radius: 6px; overflow: auto; }
  code { font: 12.5px ui-monospace, "Cascadia Code", monospace; } table { border-collapse: collapse; }
  th, td { border: 1px solid #cbd5e1; padding: 5px 9px; text-align: left; } a { color: #c2544a; }
  blockquote { border-left: 3px solid #cbd5e1; margin-left: 0; padding-left: 14px; color: #475569; }
`

watch(
  () => [props.artifact.id, props.artifact.content_file, props.artifact.updated_at],
  async () => {
    markdownDoc.value = null
    loadError.value = null
    if (!isMarkdown.value || !contentUrl.value) return
    try {
      const response = await fetch(contentUrl.value)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const html = marked.parse(await response.text())
      markdownDoc.value = `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><style>${MD_STYLE}</style></head><body>${html}</body></html>`
    } catch (err) {
      loadError.value = err.message
    }
  },
  { immediate: true }
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-edge bg-white">
    <p v-if="loadError" class="p-4 text-xs text-red-500">Could not load the local copy: {{ loadError }}</p>
    <iframe
      v-else-if="isMarkdown && markdownDoc"
      :key="`md-${artifact.id}-${artifact.updated_at}`"
      :srcdoc="markdownDoc"
      sandbox="allow-popups allow-popups-to-escape-sandbox"
      class="h-full w-full flex-1 border-0"
      title="Artifact preview"
    />
    <iframe
      v-else-if="!isMarkdown && contentUrl"
      :key="`html-${artifact.id}-${artifact.updated_at}`"
      :src="contentUrl"
      sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals"
      class="h-full w-full flex-1 border-0"
      title="Artifact preview"
    />
  </div>
</template>
