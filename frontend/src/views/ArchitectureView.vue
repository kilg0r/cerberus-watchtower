<script setup>
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import { apiFetch } from '../composables/useApi'
import { useEdgeResize } from '../composables/useEdgeResize'
import ArchGraph from '../components/ArchGraph.vue'
import DataFlowTable from '../components/DataFlowTable.vue'
import DriftPanel from '../components/DriftPanel.vue'
import FlowInspector from '../components/FlowInspector.vue'
import ProjectMethodsPanel from '../components/ProjectMethodsPanel.vue'
import SplitPane from '../components/SplitPane.vue'

// node detail overlay on the graph - drag its left edge to widen
const aside = useEdgeResize('arch-aside', { initial: 448, min: 280, max: 880, edge: 'left' })

const ALL = '__all__'
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

const repos = ref([])
const selectedId = ref(null)
const arch = ref(null)
const overview = ref(null)
const overviewDrift = ref({})
const loading = ref(false)
const refreshingList = ref(false)
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

// shared lookup context for FlowNode/MethodNode drilling (dotnet only)
provide(
  'archCtx',
  computed(() => {
    const implementations = arch.value?.implementations || {}
    const classMethods = arch.value?.class_methods || {}
    // reverse method-call index: "Type.method" -> callers. Calls to an
    // interface count for the interface and every implementation, so the
    // "called from outside" view works from either side.
    const callersOf = {}
    for (const [cls, methods] of Object.entries(classMethods)) {
      for (const method of methods) {
        for (const call of method.calls || []) {
          if (call.type === cls) continue // self calls aren't cross-references
          for (const target of [call.type, ...(implementations[call.type] || [])]) {
            const key = `${target}.${call.method}`
            ;(callersOf[key] ||= []).push({ class: cls, method: method.name })
          }
        }
      }
    }
    return {
      types: arch.value?.types || {},
      classDeps: arch.value?.class_deps || {},
      implementations,
      classSends: arch.value?.class_sends || {},
      classServiceUses: arch.value?.class_service_uses || {},
      classMethods,
      callersOf,
      handlerByRequest: Object.fromEntries(
        (arch.value?.handlers || []).map((h) => [h.request, h])
      ),
    }
  })
)

const isOverview = computed(() => selectedId.value === ALL)
const hasGraph = computed(() => Boolean(arch.value?.graph?.nodes?.length))
const stack = computed(() => arch.value?.stack)

const baseNodes = computed(() => {
  if (!arch.value?.graph) return []
  return stack.value === 'dotnet' && hideTests.value
    ? arch.value.graph.nodes.filter((n) => !n.is_test)
    : arch.value.graph.nodes
})

// ---- focus mode: BFS the dependency neighborhood of one node (any stack) ----
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
  if (!focusId.value || stack.value !== 'dotnet') return []
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
  if (!selectedNode.value || stack.value !== 'dotnet') return
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

async function loadOverview(force = false) {
  if (!force && overview.value) return
  loading.value = true
  error.value = null
  try {
    overview.value = await apiFetch('/api/architecture-overview')
    const drifts = await Promise.all(
      overview.value.map((entry) =>
        apiFetch(`/api/architecture/${entry.repo_id}/drift`).catch(() => null)
      )
    )
    overviewDrift.value = Object.fromEntries(
      overview.value.map((entry, i) => [entry.repo_id, drifts[i]])
    )
  } catch (err) {
    error.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    loading.value = false
  }
}

async function load(force = false) {
  selectedNode.value = null
  focusId.value = null
  inspected.value = null
  narrative.value = null
  showDrift.value = false
  error.value = null
  if (!selectedId.value) return
  if (isOverview.value) {
    arch.value = null
    return loadOverview(force)
  }
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

async function refreshRepoList() {
  refreshingList.value = true
  try {
    const all = await apiFetch('/api/repos?refresh=true')
    repos.value = all.filter((r) => r.exists)
    if (selectedId.value !== ALL && !repos.value.some((r) => r.id === selectedId.value)) {
      selectedId.value = ALL
      await load()
    }
  } catch (err) {
    error.value = err.message
  } finally {
    refreshingList.value = false
  }
}

function openRepo(repoId) {
  selectedId.value = repoId
  load()
}

const groupedRepos = computed(() => {
  const groups = new Map()
  for (const repo of repos.value) {
    if (!groups.has(repo.group)) groups.set(repo.group, [])
    groups.get(repo.group).push(repo)
  }
  return [...groups.entries()]
})

const groupedOverview = computed(() => {
  if (!overview.value) return []
  const groups = new Map()
  for (const entry of overview.value) {
    if (!groups.has(entry.group)) groups.set(entry.group, [])
    groups.get(entry.group).push(entry)
  }
  return [...groups.entries()]
})

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
    repos.value = all.filter((r) => r.exists)
    selectedId.value = repos.value.find((r) => r.id === 'paytable-dotnet')?.id || ALL
    await load()
  } catch (err) {
    error.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  }
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="flex h-full flex-col">
    <header class="mb-4 flex shrink-0 flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-white">Architecture</h1>
        <p class="mt-1 text-sm text-slate-500">
          <template v-if="focusId">
            <button class="text-coral hover:underline" @click="setFocus(null)">{{ selectedId }}</button>
            <span class="mx-1.5 text-slate-600">/</span>
            <span class="font-mono text-slate-300">{{ focusId }}</span>
            <span class="ml-2 text-xs text-slate-600">(esc to zoom out)</span>
          </template>
          <template v-else>Structure, dependencies, and data flow - per stack</template>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <select
          v-model="selectedId"
          class="rounded-md border border-edge bg-panel-2 px-3 py-1.5 text-xs text-slate-200 focus:border-coral/60 focus:outline-none"
          @change="load()"
        >
          <option :value="ALL">All repos - portfolio overview</option>
          <optgroup v-for="[group, items] in groupedRepos" :key="group" :label="group">
            <option v-for="repo in items" :key="repo.id" :value="repo.id">
              {{ repo.name }} ({{ repo.stack }})
            </option>
          </optgroup>
        </select>
        <button
          class="rounded-md border border-edge px-3 py-1.5 text-xs text-slate-400 transition-colors hover:border-coral/40 hover:text-coral disabled:opacity-50"
          :disabled="refreshingList"
          title="Re-discover repos under the configured roots"
          @click="refreshRepoList"
        >
          {{ refreshingList ? 'Refreshing&hellip;' : 'Refresh list' }}
        </button>
        <button
          class="rounded-md border border-edge px-3 py-1.5 text-xs text-slate-400 transition-colors hover:border-coral/40 hover:text-coral disabled:opacity-50"
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

    <div v-else-if="loading" class="flex min-h-0 flex-1 flex-col gap-4">
      <p class="text-xs text-slate-500">
        {{ isOverview ? 'Scanning all repos - the .NET solutions take a few seconds&hellip;' : `Scanning ${selectedId}&hellip;` }}
      </p>
      <div class="flex-1 animate-pulse rounded-lg border border-edge bg-panel/50" />
    </div>

    <!-- ============ portfolio overview ============ -->
    <div v-else-if="isOverview && overview" class="min-h-0 flex-1 space-y-8 overflow-auto pr-1">
      <section v-for="[group, entries] in groupedOverview" :key="group">
        <h2 class="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">{{ group }}</h2>
        <div class="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          <button
            v-for="entry in entries"
            :key="entry.repo_id"
            class="rounded-lg border border-edge bg-panel p-4 text-left transition-colors hover:border-coral/40"
            @click="openRepo(entry.repo_id)"
          >
            <div class="flex items-center gap-2">
              <h3 class="text-sm font-semibold text-white">{{ entry.name }}</h3>
              <span class="rounded-full border border-edge px-2 py-0.5 font-mono text-[10px] text-slate-400">
                {{ entry.stack }}
              </span>
              <span
                v-if="overviewDrift[entry.repo_id]?.changed"
                class="ml-auto flex items-center gap-1 text-[10px] text-amber-400"
              >
                <span class="h-1.5 w-1.5 rounded-full bg-amber-400" /> drift
              </span>
            </div>
            <div class="mt-3 flex flex-wrap gap-1.5">
              <span
                v-for="(value, label) in entry.stats"
                :key="label"
                class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >
                {{ value }} {{ label.replace('_', ' ') }}
              </span>
            </div>
          </button>
        </div>
      </section>
    </div>

    <!-- ============ single repo ============ -->
    <div v-else-if="arch" class="flex min-h-0 flex-1 flex-col gap-4">
      <!-- shared header: stats + drift + focus controls -->
      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <template v-if="!focusId">
          <span
            v-for="(value, label) in arch.stats"
            :key="label"
            class="rounded-full border border-edge bg-panel px-3 py-1 text-xs text-slate-300"
          >
            {{ value }} <span class="text-slate-500">{{ label.replace('_', ' ') }}</span>
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
            {{ visibleNodes.length }} nodes around <span class="font-mono">{{ focusId }}</span>
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
        <label
          v-if="stack === 'dotnet'"
          class="ml-auto flex cursor-pointer items-center gap-2 text-xs text-slate-400"
        >
          <input v-model="hideTests" type="checkbox" class="accent-[#ff6b5e]" />
          hide test projects ({{ arch.stats.test_projects }})
        </label>
      </div>

      <DriftPanel v-if="showDrift && drift" class="max-h-[32vh] shrink-0 overflow-auto" :drift="drift" />

      <!-- graph over stack panels - drag the divider to rebalance -->
      <SplitPane
        v-if="hasGraph"
        direction="vertical"
        storage-key="arch-graph"
        :initial="0.6"
        class="min-h-0 flex-1"
      >
        <template #first>
        <div class="relative h-full min-h-0">
        <ArchGraph
          :nodes="visibleNodes"
          :edges="visibleEdges"
          :mode="focusId ? 'focus' : 'overview'"
          :focus-id="focusId"
          :legend="LEGENDS[stack] || null"
          height-class="h-full"
          @select="selectNode($event)"
          @focus="setFocus($event)"
        />
        <aside
          v-if="selectedNode"
          class="absolute right-3 top-3 z-10 flex max-h-[calc(100%-24px)] max-w-[calc(100%-24px)] rounded-lg border border-edge bg-panel/95 backdrop-blur"
          :style="{ width: aside.size.value + 'px' }"
        >
          <div
            class="w-1.5 shrink-0 cursor-col-resize rounded-l-lg transition-colors hover:bg-coral/40"
            :class="aside.dragging.value ? 'bg-coral/60' : ''"
            title="drag to resize - double-click to reset"
            @pointerdown="aside.onPointerDown"
            @pointermove="aside.onPointerMove"
            @pointerup="aside.onPointerUp"
            @dblclick="aside.reset"
          />
          <div class="min-w-0 flex-1 overflow-auto p-4">
          <h3 class="break-all font-mono text-sm font-semibold text-white">{{ selectedNode.label }}</h3>
          <p class="mt-1 break-all text-xs text-slate-500">
            <template v-if="stack === 'dotnet'">
              {{ selectedNode.framework || 'no framework' }} &middot; {{ selectedNode.file_count }} files
              <template v-if="selectedNode.handler_count"> &middot; {{ selectedNode.handler_count }} handlers</template>
            </template>
            <template v-else-if="stack === 'python'">
              {{ selectedNode.id }} &middot; {{ selectedNode.loc }} loc
              <template v-if="selectedNode.classes"> &middot; {{ selectedNode.classes }} classes</template>
            </template>
            <template v-else>{{ selectedNode.kind }} &middot; {{ selectedNode.id }}</template>
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
              v-if="stack === 'dotnet'"
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
            <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Uses ({{ nodeRefs.uses.length }})</p>
            <div class="mt-1 flex flex-wrap gap-1">
              <span
                v-for="ref in nodeRefs.uses"
                :key="ref"
                class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >{{ ref }}</span>
            </div>
          </div>
          <div v-if="nodeRefs.usedBy.length" class="mt-3">
            <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Used by ({{ nodeRefs.usedBy.length }})</p>
            <div class="mt-1 flex flex-wrap gap-1">
              <span
                v-for="ref in nodeRefs.usedBy"
                :key="ref"
                class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >{{ ref }}</span>
            </div>
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
          </div>
        </aside>
        </div>
        </template>
        <template #second>
        <div class="flex h-full min-h-0 flex-col pt-2">

      <!-- dotnet: data flows (+ resizable method surface of the focused project) -->
      <SplitPane
        v-if="stack === 'dotnet' && focusId"
        direction="horizontal"
        storage-key="arch-flows-methods"
        :initial="0.5"
        class="min-h-0 flex-1"
      >
        <template #first>
          <section class="flex h-full min-h-0 flex-col">
            <h2 class="mb-3 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">
              Data flows through <span class="font-mono normal-case">{{ focusId }}</span>
            </h2>
            <DataFlowTable
              class="min-h-0 flex-1"
              :endpoints="focusEndpoints"
              height-class="min-h-0 flex-1"
              @inspect="inspected = $event"
            />
          </section>
        </template>
        <template #second>
          <ProjectMethodsPanel :project-id="focusId" class="h-full" />
        </template>
      </SplitPane>
      <section v-else-if="stack === 'dotnet'" class="flex min-h-0 flex-1 flex-col">
        <h2 class="mb-3 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">
          Data flows - endpoint &rarr; request &rarr; handler
        </h2>
        <DataFlowTable
          class="min-h-0 flex-1"
          :endpoints="arch.endpoints"
          height-class="min-h-0 flex-1"
          @inspect="inspected = $event"
        />
      </section>

      <!-- python: deps / endpoints / configs / entry points -->
      <div v-if="stack === 'python'" class="grid min-h-0 flex-1 grid-cols-2 gap-4 2xl:grid-cols-4">
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">Dependencies</h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-for="(version, name) in arch.dependencies" :key="name" class="flex justify-between py-0.5 font-mono text-xs">
              <span class="text-slate-300">{{ name }}</span>
              <span class="text-slate-500">{{ version || '*' }}</span>
            </div>
            <p v-if="!Object.keys(arch.dependencies).length" class="py-1 text-xs text-slate-500">none declared</p>
          </div>
        </section>
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">Endpoints</h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-for="(e, i) in arch.endpoints" :key="i" class="flex gap-2 py-0.5 font-mono text-xs">
              <span class="w-14 shrink-0 text-coral">{{ e.verb }}</span>
              <span class="min-w-0 flex-1 truncate text-slate-300">{{ e.route }}</span>
              <span class="truncate text-slate-500">{{ e.module }}</span>
            </div>
            <p v-if="!arch.endpoints.length" class="py-1 text-xs text-slate-500">none detected</p>
          </div>
        </section>
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">
            Config files ({{ arch.config_files.length }})
          </h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-for="c in arch.config_files" :key="c.path" class="flex gap-2 py-0.5 font-mono text-xs">
              <span class="min-w-0 flex-1 truncate text-slate-300">{{ c.path }}</span>
              <span class="text-slate-500">{{ c.kind }}</span>
            </div>
            <p v-if="!arch.config_files.length" class="py-1 text-xs text-slate-500">none</p>
          </div>
        </section>
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">Entry points</h2>
          <p class="font-mono text-xs leading-6 text-slate-300">
            <template v-if="arch.entry_points.length">
              <span v-for="ep in arch.entry_points" :key="ep" class="block">python -m {{ ep }}</span>
            </template>
            <span v-else class="text-slate-500">none (library / scripts)</span>
          </p>
        </section>
      </div>

      <!-- vue: routes / api calls / stores / deps -->
      <div v-if="stack === 'vue'" class="grid min-h-0 flex-1 grid-cols-2 gap-4 2xl:grid-cols-4">
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">Routes</h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-for="route in arch.routes" :key="route.path" class="flex gap-3 py-1 font-mono text-xs">
              <span class="min-w-0 flex-1 truncate text-slate-300">{{ route.path }}</span>
              <span class="truncate text-slate-500">{{ route.component || route.name || '-' }}</span>
            </div>
          </div>
        </section>
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">API calls</h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-for="call in arch.api_calls" :key="call.url" class="flex gap-3 py-1 font-mono text-xs">
              <span class="min-w-0 flex-1 truncate text-slate-300">{{ call.url }}</span>
              <span class="truncate text-slate-500">{{ call.file }}</span>
            </div>
            <p v-if="!arch.api_calls.length" class="py-1 text-xs text-slate-500">none detected</p>
          </div>
        </section>
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">
            Stores ({{ arch.stores.length }})
          </h2>
          <p class="min-h-0 flex-1 overflow-auto font-mono text-xs leading-5 text-slate-400">
            {{ arch.stores.join(', ') || 'no stores' }}
          </p>
        </section>
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">Dependencies</h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-for="(version, name) in arch.packages.dependencies" :key="name" class="flex justify-between py-0.5 font-mono text-xs">
              <span class="text-slate-300">{{ name }}</span>
              <span class="text-slate-500">{{ version }}</span>
            </div>
          </div>
        </section>
      </div>

        </div>
        </template>
      </SplitPane>

      <!-- generic inventory (config / terraform / android / web / mixed) - no graph, fills the view -->
      <div v-if="arch.languages" class="grid min-h-0 flex-1 grid-cols-2 gap-4 2xl:grid-cols-3">
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">Languages</h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-for="(count, language) in arch.languages" :key="language" class="flex justify-between py-0.5 font-mono text-xs">
              <span class="text-slate-300">{{ language }}</span>
              <span class="text-slate-500">{{ count }} files</span>
            </div>
          </div>
        </section>
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">Directories</h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <div v-for="dir in arch.directories" :key="dir.name" class="flex gap-3 py-0.5 font-mono text-xs">
              <span class="min-w-0 flex-1 truncate text-slate-300">{{ dir.name }}</span>
              <span class="text-slate-500">{{ dir.files }} files</span>
              <span class="w-20 truncate text-right text-slate-600">{{ dir.main_language || '' }}</span>
            </div>
          </div>
        </section>
        <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel p-4">
          <h2 class="mb-2 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">
            Docs ({{ arch.docs.length }})
          </h2>
          <div class="min-h-0 flex-1 overflow-auto">
            <p v-for="doc in arch.docs" :key="doc" class="truncate py-0.5 font-mono text-xs text-slate-400">{{ doc }}</p>
            <p v-if="!arch.docs.length" class="py-1 text-xs text-slate-500">no markdown files</p>
          </div>
        </section>
      </div>

      <FlowInspector v-if="inspected" :endpoint="inspected" @close="inspected = null" />
    </div>
  </div>
</template>
