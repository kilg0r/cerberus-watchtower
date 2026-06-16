<script setup>
import { computed, ref, watch } from 'vue'
import { apiFetch } from '../composables/useApi'
import { provideArchContext } from '../composables/useArchContext'
import ArchGraph from './ArchGraph.vue'
import DataFlowTable from './DataFlowTable.vue'
import DiffStatBadge from './DiffStatBadge.vue'
import DriftPanel from './DriftPanel.vue'
import FlowInspector from './FlowInspector.vue'
import GitFileRow from './GitFileRow.vue'

// Combined review of a commit bundle: a Changes tab (combined diff) and an
// Architecture tab (structural delta + the same call-graph drill-through as the
// Architecture view, computed from the bundle's head tree).
const props = defineProps({
  repoId: { type: String, required: true },
  mode: { type: String, required: true }, // 'range' | 'set'
  base: { type: String, default: null },
  head: { type: String, default: null },
  shas: { type: Array, default: () => [] },
})

const tab = ref('changes')

// ---- bundle file list + combined-diff URLs ----
const bundle = ref(null)
const loadingBundle = ref(false)
const bundleError = ref(null)

const baseQuery = computed(() => {
  const p = new URLSearchParams({ mode: props.mode })
  if (props.mode === 'range') {
    p.set('base', props.base || '')
    p.set('head', props.head || '')
  } else {
    p.set('shas', props.shas.join(','))
  }
  return p.toString()
})

const diffUrl = (path) =>
  `/api/git/${props.repoId}/bundle/diff?${baseQuery.value}&path=${encodeURIComponent(path)}`

async function loadBundle() {
  loadingBundle.value = true
  bundleError.value = null
  bundle.value = null
  try {
    bundle.value = await apiFetch(`/api/git/${props.repoId}/bundle?${baseQuery.value}`)
  } catch (err) {
    bundleError.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    loadingBundle.value = false
  }
}

// ---- AI summary of the combined diff ----
const summary = ref(null)
const summarizing = ref(false)
const summaryError = ref(null)

async function summarize() {
  if (summary.value) {
    summary.value = null
    return
  }
  summarizing.value = true
  summaryError.value = null
  try {
    summary.value = await apiFetch(`/api/git/${props.repoId}/bundle/summarize?${baseQuery.value}`, {
      method: 'POST',
    })
  } catch (err) {
    summaryError.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    summarizing.value = false
  }
}

// ---- architectural delta (lazy: it may scan two trees the first time) ----
const delta = ref(null)
const headArch = ref(null)
const loadingDelta = ref(false)
const deltaError = ref(null)
const deltaLoaded = ref(false)

provideArchContext(headArch) // drives FlowInspector / FlowNode / MethodNode

async function loadDelta(force = false) {
  if (deltaLoaded.value && !force) return
  loadingDelta.value = true
  deltaError.value = null
  try {
    const data = await apiFetch(`/api/git/${props.repoId}/bundle/arch-delta?${baseQuery.value}`)
    delta.value = data
    headArch.value = data.head_arch || null
    deltaLoaded.value = true
  } catch (err) {
    deltaError.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    loadingDelta.value = false
  }
}

// reset + reload whenever the bundle spec changes
watch(
  baseQuery,
  () => {
    summary.value = null
    delta.value = null
    headArch.value = null
    deltaLoaded.value = false
    resetFocus()
    loadBundle()
    if (tab.value === 'architecture') loadDelta(true)
  },
  { immediate: true }
)

watch(tab, (t) => {
  if (t === 'architecture' && !deltaLoaded.value) loadDelta()
})

// ---- graph drill-through (mirrors ArchitectureView's focus mechanic) ----
const stack = computed(() => headArch.value?.stack)
const hideTests = ref(true)
const focusId = ref(null)
const focusDepth = ref(1)
const selectedNode = ref(null)
const inspected = ref(null)
const DEPTHS = [
  { label: 'Direct', value: 1 },
  { label: '2 levels', value: 2 },
  { label: 'All', value: 99 },
]
const LEGENDS = {
  vue: [
    { label: 'view', color: '#38bdf8' },
    { label: 'component', color: '#a78bfa' },
    { label: 'store', color: '#34d399' },
  ],
  python: [
    { label: 'package', color: '#34d399' },
    { label: 'module', color: '#38bdf8' },
  ],
}

const addedNodeIds = computed(() => delta.value?.changes?.added_projects || [])

const baseNodes = computed(() => {
  if (!headArch.value?.graph) return []
  return stack.value === 'dotnet' && hideTests.value
    ? headArch.value.graph.nodes.filter((n) => !n.is_test)
    : headArch.value.graph.nodes
})

const focusIds = computed(() => {
  if (!focusId.value || !headArch.value?.graph) return null
  const edges = headArch.value.graph.edges
  const allowed = new Set(baseNodes.value.map((n) => n.id))
  const keep = new Set([focusId.value])
  for (const direction of ['down', 'up']) {
    let frontier = new Set([focusId.value])
    for (let hop = 0; hop < focusDepth.value && frontier.size; hop++) {
      const next = new Set()
      for (const edge of edges) {
        const [from, to] =
          direction === 'down' ? [edge.source, edge.target] : [edge.target, edge.source]
        if (frontier.has(from) && allowed.has(to) && !keep.has(to)) {
          keep.add(to)
          next.add(to)
        }
      }
      frontier = next
    }
  }
  return keep
})

const visibleNodes = computed(() =>
  focusIds.value ? baseNodes.value.filter((n) => focusIds.value.has(n.id)) : baseNodes.value
)
const visibleEdges = computed(() => {
  if (!headArch.value?.graph) return []
  const ids = new Set(visibleNodes.value.map((n) => n.id))
  return headArch.value.graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
})
const nodeRefs = computed(() => {
  if (!selectedNode.value || !headArch.value?.graph) return { uses: [], usedBy: [] }
  const id = selectedNode.value.id
  return {
    uses: headArch.value.graph.edges.filter((e) => e.source === id).map((e) => e.target),
    usedBy: headArch.value.graph.edges.filter((e) => e.target === id).map((e) => e.source),
  }
})
const focusEndpoints = computed(() => {
  if (stack.value !== 'dotnet' || !headArch.value?.endpoints) return []
  if (!focusId.value) return headArch.value.endpoints
  return headArch.value.endpoints.filter(
    (e) => e.handler_project === focusId.value || e.project === focusId.value
  )
})

const driftLike = computed(() => ({
  changes: delta.value?.changes || null,
  previous_at: null,
  latest_at: null,
  snapshot_count: 0,
}))
const hasDeltaChanges = computed(() =>
  Object.values(delta.value?.changes || {}).some((v) => (Array.isArray(v) ? v.length : v && Object.keys(v).length))
)

function setFocus(id) {
  focusId.value = id
  selectedNode.value = null
}
function resetFocus() {
  focusId.value = null
  selectedNode.value = null
  inspected.value = null
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <!-- tabs -->
    <div class="mb-3 flex shrink-0 items-center gap-1 border-b border-edge">
      <button
        v-for="t in ['changes', 'architecture']"
        :key="t"
        class="-mb-px border-b-2 px-3 py-1.5 text-xs font-medium capitalize transition-colors"
        :class="tab === t ? 'border-coral text-coral' : 'border-transparent text-slate-400 hover:text-slate-200'"
        @click="tab = t"
      >
        {{ t }}
      </button>
      <span v-if="bundle" class="ml-auto pr-1 text-[11px] text-slate-500">
        {{ bundle.files.length }} files
        <DiffStatBadge
          class="ml-1 !text-[11px]"
          :additions="bundle.additions"
          :deletions="bundle.deletions"
        />
      </span>
    </div>

    <!-- ============ Changes ============ -->
    <div v-if="tab === 'changes'" class="flex min-h-0 flex-1 flex-col">
      <div class="mb-2 flex shrink-0 items-center gap-3">
        <button
          class="inline-flex items-center gap-1.5 text-xs text-coral hover:underline disabled:opacity-50"
          :disabled="summarizing"
          @click="summarize"
        >
          <svg v-if="summarizing" class="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v3a5 5 0 00-5 5H4z" />
          </svg>
          {{ summarizing ? 'Summarizing…' : 'AI review summary' }}
          <template v-if="!summarizing">{{ summary ? '▾' : '▸' }}</template>
        </button>
      </div>

      <p v-if="summaryError" class="mb-2 text-xs text-red-400">{{ summaryError }}</p>
      <div v-if="summary" class="mb-3 shrink-0 rounded-md border border-edge bg-navy/60 p-3">
        <div class="mb-1.5 flex items-center gap-2">
          <span class="text-[10px] font-semibold uppercase tracking-wide text-slate-400">Bundle summary</span>
          <span v-if="summary.cached" class="rounded-full border border-edge px-1.5 py-0.5 text-[9px] uppercase text-slate-500">cached</span>
        </div>
        <p class="whitespace-pre-line text-xs leading-relaxed text-slate-300">{{ summary.summary }}</p>
      </div>

      <p v-if="loadingBundle" class="text-xs text-slate-500">Loading bundle&hellip;</p>
      <p v-else-if="bundleError" class="text-xs text-red-400">{{ bundleError }}</p>
      <div v-else-if="bundle" class="min-h-0 flex-1 overflow-auto pr-1">
        <p class="mb-2 text-[11px] text-slate-500">
          {{ bundle.commits.length }} commit{{ bundle.commits.length === 1 ? '' : 's' }} ·
          {{ mode === 'range' ? 'combined tree diff' : 'patch union of selected commits' }}
        </p>
        <p v-if="!bundle.files.length" class="py-6 text-center text-xs text-slate-500">No file changes</p>
        <GitFileRow v-for="f in bundle.files" :key="f.path" :file="f" :diff-url="diffUrl(f.path)" />
      </div>
    </div>

    <!-- ============ Architecture ============ -->
    <div v-else class="flex min-h-0 flex-1 flex-col">
      <p v-if="loadingDelta" class="text-xs text-slate-500">
        Scanning architecture at the bundle endpoints (first look may take a few seconds)&hellip;
      </p>
      <p v-else-if="deltaError" class="text-xs text-red-400">{{ deltaError }}</p>
      <p v-else-if="delta && delta.unsupported" class="text-xs text-slate-500">
        No analyzable architecture graph for this stack at the bundle head.
      </p>
      <div v-else-if="delta" class="flex min-h-0 flex-1 flex-col gap-3">
        <p v-if="!delta.base_available" class="shrink-0 text-[11px] text-amber-400">
          No parent tree to compare against — showing the head architecture (everything reads as added).
        </p>

        <DriftPanel
          v-if="hasDeltaChanges"
          class="max-h-[28vh] shrink-0 overflow-auto"
          :drift="driftLike"
        />
        <p v-else class="shrink-0 text-[11px] text-slate-500">No structural architecture changes in this bundle.</p>

        <!-- graph + drill, mirroring the Architecture view -->
        <div v-if="headArch?.graph?.nodes?.length" class="flex min-h-0 flex-1 flex-col gap-2">
          <div class="flex shrink-0 flex-wrap items-center gap-2">
            <template v-if="focusId">
              <span class="text-xs text-slate-500">depth:</span>
              <button
                v-for="d in DEPTHS"
                :key="d.value"
                class="rounded-full border px-2.5 py-0.5 text-[11px] transition-colors"
                :class="focusDepth === d.value ? 'border-coral/60 text-coral' : 'border-edge text-slate-400 hover:text-slate-200'"
                @click="focusDepth = d.value"
              >
                {{ d.label }}
              </button>
              <button class="text-[11px] text-coral hover:underline" @click="setFocus(null)">clear focus</button>
              <span class="text-[11px] text-slate-500">around <span class="font-mono">{{ focusId }}</span></span>
            </template>
            <label v-if="stack === 'dotnet'" class="ml-auto flex cursor-pointer items-center gap-2 text-[11px] text-slate-400">
              <input v-model="hideTests" type="checkbox" class="accent-[#ff6b5e]" /> hide tests
            </label>
          </div>

          <div class="relative min-h-0 flex-1">
            <ArchGraph
              :nodes="visibleNodes"
              :edges="visibleEdges"
              :mode="focusId ? 'focus' : 'overview'"
              :focus-id="focusId"
              :highlight="addedNodeIds"
              :legend="LEGENDS[stack] || null"
              height-class="h-full"
              @select="selectedNode = $event"
              @focus="setFocus($event)"
            />
            <aside
              v-if="selectedNode"
              class="absolute right-3 top-3 z-10 max-h-[calc(100%-24px)] w-72 overflow-auto rounded-lg border border-edge bg-panel/95 p-4 backdrop-blur"
            >
              <h3 class="break-all font-mono text-sm font-semibold text-white">{{ selectedNode.label }}</h3>
              <p class="mt-1 break-all text-[11px] text-slate-500">{{ selectedNode.id }}</p>
              <button
                v-if="selectedNode.id !== focusId"
                class="mt-2 w-full rounded-md border border-coral/40 px-2 py-1 text-xs text-coral hover:bg-coral/10"
                @click="setFocus(selectedNode.id)"
              >
                Focus &rarr;
              </button>
              <div v-if="nodeRefs.uses.length" class="mt-3">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Uses ({{ nodeRefs.uses.length }})</p>
                <div class="mt-1 flex flex-wrap gap-1">
                  <span v-for="r in nodeRefs.uses" :key="r" class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">{{ r }}</span>
                </div>
              </div>
              <div v-if="nodeRefs.usedBy.length" class="mt-3">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Used by ({{ nodeRefs.usedBy.length }})</p>
                <div class="mt-1 flex flex-wrap gap-1">
                  <span v-for="r in nodeRefs.usedBy" :key="r" class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">{{ r }}</span>
                </div>
              </div>
            </aside>
          </div>

          <!-- dotnet: endpoint → request → handler → method-level call drill -->
          <section v-if="stack === 'dotnet' && focusEndpoints.length" class="flex max-h-[30vh] min-h-0 shrink-0 flex-col">
            <h3 class="mb-2 shrink-0 text-[10px] font-semibold uppercase tracking-widest text-slate-500">
              Data flows {{ focusId ? `through ${focusId}` : '' }}
            </h3>
            <DataFlowTable
              class="min-h-0 flex-1"
              :endpoints="focusEndpoints"
              height-class="min-h-0 flex-1"
              @inspect="inspected = $event"
            />
          </section>
        </div>
      </div>

      <FlowInspector v-if="inspected" :endpoint="inspected" @close="inspected = null" />
    </div>
  </div>
</template>
