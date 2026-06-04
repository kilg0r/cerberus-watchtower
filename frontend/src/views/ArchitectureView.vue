<script setup>
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import { apiFetch } from '../composables/useApi'
import ArchGraph from '../components/ArchGraph.vue'
import DataFlowTable from '../components/DataFlowTable.vue'
import DriftPanel from '../components/DriftPanel.vue'
import FlowInspector from '../components/FlowInspector.vue'

const SUPPORTED_STACKS = ['dotnet', 'vue']
const DEPTHS = [
  { label: 'Direct', value: 1 },
  { label: '2 levels', value: 2 },
  { label: 'All', value: 99 },
]

const repos = ref([])
const selectedId = ref(null)
const arch = ref(null)
const loading = ref(false)
const error = ref(null)
const hideTests = ref(true)
const selectedNode = ref(null)
const focusId = ref(null)
const focusDepth = ref(1)
const inspected = ref(null)
const drift = ref(null)
const showDrift = ref(false)
const narrative = ref(null)
const narrating = ref(false)
const cache = new Map()

// shared lookup context for FlowNode drilling
provide(
  'archCtx',
  computed(() => ({
    types: arch.value?.types || {},
    classDeps: arch.value?.class_deps || {},
    implementations: arch.value?.implementations || {},
    classSends: arch.value?.class_sends || {},
    classServiceUses: arch.value?.class_service_uses || {},
    handlerByRequest: Object.fromEntries(
      (arch.value?.handlers || []).map((h) => [h.request, h])
    ),
  }))
)

const baseNodes = computed(() => {
  if (!arch.value?.graph) return []
  return hideTests.value
    ? arch.value.graph.nodes.filter((n) => !n.is_test)
    : arch.value.graph.nodes
})

// ---- focus mode: BFS the dependency neighborhood of one project ----
const focusIds = computed(() => {
  if (!focusId.value || !arch.value?.graph) return null
  const edges = arch.value.graph.edges
  const allowed = new Set(baseNodes.value.map((n) => n.id))
  const keep = new Set([focusId.value])
  for (const direction of ['down', 'up']) {
    let frontier = new Set([focusId.value])
    for (let hop = 0; hop < focusDepth.value && frontier.size; hop++) {
      const next = new Set()
      for (const edge of edges) {
        const [from, to] = direction === 'down'
          ? [edge.source, edge.target]
          : [edge.target, edge.source]
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
  if (!arch.value?.graph) return []
  const ids = new Set(visibleNodes.value.map((n) => n.id))
  return arch.value.graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
})

const focusEndpoints = computed(() => {
  if (!focusId.value || !arch.value) return []
  return arch.value.endpoints.filter(
    (e) => e.handler_project === focusId.value || e.project === focusId.value
  )
})

const nodeRefs = computed(() => {
  if (!selectedNode.value || !arch.value?.graph) return { uses: [], usedBy: [] }
  const id = selectedNode.value.id
  return {
    uses: arch.value.graph.edges.filter((e) => e.source === id).map((e) => e.target),
    usedBy: arch.value.graph.edges.filter((e) => e.target === id).map((e) => e.source),
  }
})

function setFocus(id) {
  focusId.value = id
  selectedNode.value = null
  narrative.value = null
}

function selectNode(node) {
  selectedNode.value = node
  narrative.value = null
}

async function explain() {
  if (!selectedNode.value) return
  narrating.value = true
  try {
    narrative.value = await apiFetch(
      `/api/architecture/${selectedId.value}/narrate/${encodeURIComponent(selectedNode.value.id)}`,
      { method: 'POST' }
    )
  } catch (err) {
    narrative.value = { narrative: `Failed: ${err.message}`, cached: false }
  } finally {
    narrating.value = false
  }
}

async function loadDrift() {
  try {
    drift.value = await apiFetch(`/api/architecture/${selectedId.value}/drift`)
  } catch {
    drift.value = null
  }
}

async function load(force = false) {
  if (!selectedId.value) return
  selectedNode.value = null
  focusId.value = null
  inspected.value = null
  narrative.value = null
  showDrift.value = false
  error.value = null
  if (!force && cache.has(selectedId.value)) {
    arch.value = cache.get(selectedId.value)
    loadDrift()
    return
  }
  loading.value = true
  arch.value = null
  try {
    const result = await apiFetch(`/api/architecture/${selectedId.value}`)
    cache.set(selectedId.value, result)
    arch.value = result
    loadDrift()
  } catch (err) {
    error.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    loading.value = false
  }
}

function onKeydown(event) {
  if (event.key === 'Escape') {
    if (inspected.value) inspected.value = null
    else if (focusId.value) setFocus(null)
  }
}

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  try {
    const all = await apiFetch('/api/repos')
    repos.value = all.filter((r) => SUPPORTED_STACKS.includes(r.stack) && r.exists)
    selectedId.value =
      repos.value.find((r) => r.id === 'paytable-dotnet')?.id || repos.value[0]?.id
    await load()
  } catch (err) {
    error.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  }
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div>
    <header class="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-white">Architecture</h1>
        <p class="mt-1 text-sm text-slate-500">
          <template v-if="focusId">
            <button class="text-coral hover:underline" @click="setFocus(null)">{{ arch?.solution || selectedId }}</button>
            <span class="mx-1.5 text-slate-600">/</span>
            <span class="font-mono text-slate-300">{{ focusId }}</span>
            <span class="ml-2 text-xs text-slate-600">(esc to zoom out)</span>
          </template>
          <template v-else>Project structure, dependencies, and data flow</template>
        </p>
      </div>
      <div class="flex items-center gap-3">
        <select
          v-model="selectedId"
          class="rounded-md border border-edge bg-panel-2 px-3 py-1.5 text-xs text-slate-200 focus:border-coral/60 focus:outline-none"
          @change="load()"
        >
          <option v-for="repo in repos" :key="repo.id" :value="repo.id">{{ repo.name }}</option>
        </select>
        <button
          class="rounded-md border border-edge px-3 py-1.5 text-xs text-slate-400 transition-colors hover:border-coral/40 hover:text-coral"
          :disabled="loading"
          @click="load(true)"
        >
          Rescan
        </button>
      </div>
    </header>

    <p v-if="error" class="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
      {{ error }}
    </p>

    <div v-else-if="loading" class="space-y-4">
      <p class="text-xs text-slate-500">Scanning {{ selectedId }}&hellip;</p>
      <div class="h-[60vh] animate-pulse rounded-lg border border-edge bg-panel/50" />
    </div>

    <!-- ============ .NET solution ============ -->
    <div v-else-if="arch && arch.stack === 'dotnet'" class="space-y-4">
      <div class="flex flex-wrap items-center gap-2">
        <template v-if="!focusId">
          <span
            v-for="(value, label) in {
              projects: arch.stats.projects,
              refs: arch.stats.project_refs,
              handlers: arch.stats.handlers,
              endpoints: arch.stats.endpoints,
              packages: arch.stats.packages,
            }"
            :key="label"
            class="rounded-full border border-edge bg-panel px-3 py-1 text-xs text-slate-300"
          >
            {{ value }} <span class="text-slate-500">{{ label }}</span>
          </span>
        </template>
        <template v-else>
          <span class="text-xs text-slate-500">neighborhood depth:</span>
          <button
            v-for="depth in DEPTHS"
            :key="depth.value"
            class="rounded-full border px-3 py-1 text-xs transition-colors"
            :class="focusDepth === depth.value
              ? 'border-coral/60 text-coral'
              : 'border-edge text-slate-400 hover:text-slate-200'"
            @click="focusDepth = depth.value"
          >
            {{ depth.label }}
          </button>
          <span class="text-xs text-slate-500">
            {{ visibleNodes.length }} projects &middot; {{ focusEndpoints.length }} endpoints flow through
            <span class="font-mono">{{ focusId }}</span>
          </span>
        </template>
        <button
          v-if="drift && drift.snapshot_count > 0"
          class="rounded-full border px-3 py-1 text-xs transition-colors"
          :class="drift.changed
            ? 'border-amber-500/50 text-amber-400 hover:bg-amber-500/10'
            : 'border-edge text-slate-500 hover:text-slate-300'"
          @click="showDrift = !showDrift"
        >
          <span v-if="drift.changed" class="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
          {{ drift.changed ? 'changes detected' : 'no drift' }}
        </button>
        <label class="ml-auto flex cursor-pointer items-center gap-2 text-xs text-slate-400">
          <input v-model="hideTests" type="checkbox" class="accent-[#ff6b5e]" />
          hide test projects ({{ arch.stats.test_projects }})
        </label>
      </div>

      <DriftPanel v-if="showDrift && drift" :drift="drift" />

      <!-- screen-wide graph; node detail overlays the graph -->
      <div class="relative">
        <ArchGraph
          :nodes="visibleNodes"
          :edges="visibleEdges"
          :mode="focusId ? 'focus' : 'overview'"
          :focus-id="focusId"
          :height-class="focusId ? 'h-[52vh]' : 'h-[58vh]'"
          @select="selectNode($event)"
          @focus="setFocus($event)"
        />
        <aside
          v-if="selectedNode"
          class="absolute right-3 top-3 z-10 max-h-[calc(100%-24px)] w-72 overflow-auto rounded-lg border border-edge bg-panel/95 p-4 backdrop-blur"
        >
          <h3 class="font-mono text-sm font-semibold text-white">{{ selectedNode.label }}</h3>
          <p class="mt-1 text-xs text-slate-500">
            {{ selectedNode.framework || 'no framework' }} &middot; {{ selectedNode.file_count }} files
            <template v-if="selectedNode.handler_count"> &middot; {{ selectedNode.handler_count }} handlers</template>
          </p>
          <div class="mt-2 flex gap-2">
            <button
              v-if="selectedNode.id !== focusId"
              class="flex-1 rounded-md border border-coral/40 px-2 py-1 text-xs text-coral hover:bg-coral/10"
              @click="setFocus(selectedNode.id)"
            >
              Focus &rarr;
            </button>
            <button
              class="flex-1 rounded-md border border-edge px-2 py-1 text-xs text-slate-300 hover:border-coral/40 hover:text-coral disabled:opacity-50"
              :disabled="narrating"
              @click="explain"
            >
              {{ narrating ? 'Thinking&hellip;' : 'Explain' }}
            </button>
          </div>
          <div v-if="narrative" class="mt-3 rounded-md border border-edge bg-navy/60 p-3">
            <p class="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              Narrative
              <span v-if="narrative.cached" class="rounded-full border border-edge px-1.5 text-[9px] normal-case text-slate-500">cached</span>
            </p>
            <p class="whitespace-pre-line text-xs leading-relaxed text-slate-300">{{ narrative.narrative }}</p>
          </div>
          <div v-if="nodeRefs.uses.length" class="mt-3">
            <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Uses</p>
            <p class="mt-1 font-mono text-xs leading-5 text-slate-400">{{ nodeRefs.uses.join(', ') }}</p>
          </div>
          <div v-if="nodeRefs.usedBy.length" class="mt-3">
            <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Used by</p>
            <p class="mt-1 font-mono text-xs leading-5 text-slate-400">{{ nodeRefs.usedBy.join(', ') }}</p>
          </div>
          <div v-if="selectedNode.packages?.length" class="mt-3">
            <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              Packages ({{ selectedNode.packages.length }})
            </p>
            <ul class="mt-1 max-h-40 overflow-auto font-mono text-xs leading-5 text-slate-400">
              <li v-for="pkg in selectedNode.packages" :key="pkg.name">
                {{ pkg.name }} <span class="text-slate-600">{{ pkg.version }}</span>
              </li>
            </ul>
          </div>
        </aside>
      </div>

      <section>
        <h2 class="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
          <template v-if="focusId">Data flows through <span class="font-mono normal-case">{{ focusId }}</span></template>
          <template v-else>Data flows - endpoint &rarr; request &rarr; handler</template>
        </h2>
        <DataFlowTable
          :endpoints="focusId ? focusEndpoints : arch.endpoints"
          height-class="max-h-[32vh]"
          @inspect="inspected = $event"
        />
      </section>

      <FlowInspector v-if="inspected" :endpoint="inspected" @close="inspected = null" />
    </div>

    <!-- ============ Vue app ============ -->
    <div v-else-if="arch && arch.stack === 'vue'" class="space-y-6">
      <div class="flex flex-wrap items-center gap-2">
        <span
          v-for="(value, label) in arch.stats"
          :key="label"
          class="rounded-full border border-edge bg-panel px-3 py-1 text-xs text-slate-300"
        >
          {{ value }} <span class="text-slate-500">{{ label.replace('_', ' ') }}</span>
        </span>
        <button
          v-if="drift && drift.snapshot_count > 0"
          class="rounded-full border px-3 py-1 text-xs transition-colors"
          :class="drift.changed
            ? 'border-amber-500/50 text-amber-400 hover:bg-amber-500/10'
            : 'border-edge text-slate-500 hover:text-slate-300'"
          @click="showDrift = !showDrift"
        >
          <span v-if="drift.changed" class="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
          {{ drift.changed ? 'changes detected' : 'no drift' }}
        </button>
      </div>

      <DriftPanel v-if="showDrift && drift" :drift="drift" />

      <div class="grid grid-cols-2 gap-4 2xl:grid-cols-4">
        <section class="rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-500">Routes</h2>
          <div class="max-h-[58vh] overflow-auto">
            <div v-for="route in arch.routes" :key="route.path" class="flex gap-3 py-1 font-mono text-xs">
              <span class="min-w-0 flex-1 truncate text-slate-300">{{ route.path }}</span>
              <span class="truncate text-slate-500">{{ route.component || route.name || '-' }}</span>
            </div>
          </div>
        </section>

        <section class="rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-500">API calls</h2>
          <div class="max-h-[58vh] overflow-auto">
            <div v-for="call in arch.api_calls" :key="call.url" class="flex gap-3 py-1 font-mono text-xs">
              <span class="min-w-0 flex-1 truncate text-slate-300">{{ call.url }}</span>
              <span class="truncate text-slate-500">{{ call.file }}</span>
            </div>
            <p v-if="!arch.api_calls.length" class="py-1 text-xs text-slate-500">none detected</p>
          </div>
        </section>

        <section class="rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-500">
            Stores ({{ arch.stores.length }}) &amp; Views ({{ arch.views.length }})
          </h2>
          <div class="max-h-[58vh] overflow-auto">
            <p class="font-mono text-xs leading-5 text-slate-400">{{ arch.stores.join(', ') || 'no stores' }}</p>
            <p class="mt-2 font-mono text-xs leading-5 text-slate-500">{{ arch.views.join(', ') || 'no views' }}</p>
          </div>
        </section>

        <section class="rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-500">Dependencies</h2>
          <div class="max-h-[58vh] overflow-auto">
            <div
              v-for="(version, name) in arch.packages.dependencies"
              :key="name"
              class="flex justify-between py-0.5 font-mono text-xs"
            >
              <span class="text-slate-300">{{ name }}</span>
              <span class="text-slate-500">{{ version }}</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
