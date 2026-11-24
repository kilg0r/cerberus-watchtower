<script setup>
import { computed, onMounted, onUnmounted, provide, ref, watch } from 'vue'
import { apiFetch } from '../composables/useApi'
import { provideArchContext } from '../composables/useArchContext'
import { useEdgeResize } from '../composables/useEdgeResize'
import ArchGraph from '../components/ArchGraph.vue'
import ArchGraph3D from '../components/ArchGraph3D.vue'
import DataFlowTable from '../components/DataFlowTable.vue'
import DriftPanel from '../components/DriftPanel.vue'
import FlowInspector from '../components/FlowInspector.vue'
import ProjectMethodsPanel from '../components/ProjectMethodsPanel.vue'
import SplitPane from '../components/SplitPane.vue'
import { folderColorMap, legendFor } from '../utils/archColors'
import { buildScopeGraph, SCOPE_LIMIT, scopeId } from '../utils/archScope'

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
// 3D map by default; the 2D dagre graph stays one click away (and is the
// fallback when WebGL isn't available)
const VIEW_KEY = 'watchtower:arch:view'
const view3d = ref(localStorage.getItem(VIEW_KEY) !== '2d')
const webglFailed = ref(false)
watch(view3d, (on) => {
  localStorage.setItem(VIEW_KEY, on ? '3d' : '2d')
  selectedNode.value = null
})
const show3d = computed(() => view3d.value && !webglFailed.value)

// shared lookup context for FlowNode/MethodNode drilling (dotnet only)
const ctx = provideArchContext(arch)

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

// ---- folder isolation: one solution folder and the libraries it links to ----
const isolated = ref(null)
const isolateDepth = ref(1)
const isolateUsers = ref(false)
const ISOLATE_DEPTHS = [
  { label: 'Direct', value: 1 },
  { label: 'All', value: 99 },
]
const folderKey = (n) => n.folder || (LEGENDS[stack.value] ? n.kind : 'other')
const isolation = computed(() => {
  if (!isolated.value || !arch.value?.graph) return null
  const allowed = new Set(baseNodes.value.map((n) => n.id))
  const roles = new Map()
  for (const n of baseNodes.value) if (folderKey(n) === isolated.value) roles.set(n.id, 'member')
  const edges = arch.value.graph.edges
  let frontier = new Set(roles.keys())
  for (let hop = 0; hop < isolateDepth.value && frontier.size; hop++) {
    const next = new Set()
    for (const e of edges) {
      if (frontier.has(e.source) && allowed.has(e.target) && !roles.has(e.target)) {
        roles.set(e.target, 'linked')
        next.add(e.target)
      }
    }
    frontier = next
  }
  if (isolateUsers.value) {
    for (const e of edges) {
      if (roles.get(e.target) === 'member' && allowed.has(e.source) && !roles.has(e.source)) roles.set(e.source, 'user')
    }
  }
  return roles
})
const isolationCounts = computed(() => {
  const counts = { member: 0, linked: 0, user: 0 }
  for (const role of isolation.value?.values() || []) counts[role]++
  return counts
})

// ---- scope: a namespace, file, class, method or endpoint, drawn below the
// project level (dotnet) - chosen from the lists under the graph ----
const scope = ref(null)
const scopeDepth = ref(2)
const scopeCallers = ref(true)
const SCOPE_DEPTHS = [
  { label: '1', value: 1 },
  { label: '2', value: 2 },
  { label: '3', value: 3 },
  { label: 'All', value: 99 },
]
const scopeGraph = computed(() =>
  scope.value && stack.value === 'dotnet' && arch.value
    ? buildScopeGraph(arch.value, ctx.value, scope.value, { depth: scopeDepth.value, callers: scopeCallers.value })
    : null
)
// phone layout: which split pane shows - the map, or the lists under it
const archPane = ref('first')

function setScope(next) {
  // picking the active scope again steps back out of it
  scope.value = next && scopeId(next) === scopeId(scope.value) ? null : next
  if (scope.value) archPane.value = 'first'
  isolated.value = null
  selectedNode.value = null
  narrative.value = null
  inspected.value = null
}
provide('archScope', { active: computed(() => scopeId(scope.value)), set: setScope })

function traceEndpoint(e) {
  setScope({ kind: 'endpoint', key: `${e.verb} ${e.route}`, label: `${e.verb} ${e.route}`, endpoint: e })
}
const activeEndpoint = computed(() => (scope.value?.kind === 'endpoint' ? scope.value.key : null))

const visibleNodes = computed(() => {
  if (scopeGraph.value) return scopeGraph.value.nodes
  if (isolation.value) {
    return baseNodes.value
      .filter((n) => isolation.value.has(n.id))
      .map((n) => ({ ...n, role: isolation.value.get(n.id), bundle: folderKey(n) }))
  }
  return focusIds.value ? baseNodes.value.filter((n) => focusIds.value.has(n.id)) : baseNodes.value
})
const visibleEdges = computed(() => {
  if (scopeGraph.value) return scopeGraph.value.edges
  if (!arch.value?.graph) return []
  const ids = new Set(visibleNodes.value.map((n) => n.id))
  return arch.value.graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
})
const graphFocusId = computed(() => (scopeGraph.value ? scopeGraph.value.focusId : focusId.value))
// one palette and one legend for the whole solution: a folder keeps its color
// and its legend slot however much of the map is showing
const palette = computed(() => folderColorMap(arch.value?.graph?.nodes || []))
const graphLegend = computed(() => {
  if (LEGENDS[stack.value]) return LEGENDS[stack.value]
  return legendFor(scopeGraph.value ? visibleNodes.value : baseNodes.value, palette.value)
})
const graphMode = computed(() => (focusId.value || scope.value || isolated.value ? 'focus' : 'overview'))
const caption = computed(() => {
  if (scope.value) return `${scope.value.kind} · ${scope.value.label}`
  if (isolated.value) return `isolated · ${isolated.value}`
  return null
})
const scopeNoun = computed(() => (scopeGraph.value?.level === 'method' ? 'methods' : 'classes'))

const focusEndpoints = computed(() => {
  if (!focusId.value || stack.value !== 'dotnet') return []
  return arch.value.endpoints.filter(
    (e) => e.handler_project === focusId.value || e.project === focusId.value
  )
})

const nodeRefs = computed(() => {
  if (!selectedNode.value || !arch.value?.graph) return { uses: [], usedBy: [] }
  const id = selectedNode.value.id
  const scoped = !!scopeGraph.value
  const edges = scoped ? visibleEdges.value : arch.value.graph.edges
  const byId = new Map(visibleNodes.value.map((n) => [n.id, n]))
  const entry = (other, kind) => ({ id: other, label: scoped ? byId.get(other)?.label || other : other, kind })
  return {
    uses: edges.filter((e) => e.source === id).map((e) => entry(e.target, e.kind)),
    usedBy: edges.filter((e) => e.target === id).map((e) => entry(e.source, e.kind)),
  }
})
const scopedNode = computed(() => !!scopeGraph.value && !!selectedNode.value)
const selectedMethods = computed(() =>
  selectedNode.value?.kind !== 'method' && selectedNode.value?.typeName
    ? ctx.value.classMethods[selectedNode.value.typeName] || []
    : []
)

function setFocus(id) {
  focusId.value = id
  isolated.value = null
  scope.value = null
  selectedNode.value = null
  narrative.value = null
}

function isolate(label) {
  isolated.value = isolated.value === label ? null : label
  focusId.value = null
  scope.value = null
  selectedNode.value = null
  narrative.value = null
}

// double-click / Enter / "Narrow to this": in a scoped map, re-scope on the
// node; on the project map, focus the project
function onGraphFocus(id) {
  if (!scopeGraph.value) return setFocus(id)
  const node = visibleNodes.value.find((n) => n.id === id)
  if (!node) return
  if (node.kind === 'endpoint') inspected.value = node.endpoint
  else if (node.kind === 'method') setScope({ kind: 'method', key: `${node.typeName}.${node.methodName}`, label: node.label })
  else if (node.typeName) setScope({ kind: 'class', key: node.typeName, label: node.typeName })
}
function scopeMethod(typeName, method) {
  setScope({ kind: 'method', key: `${typeName}.${method}`, label: `${typeName}.${method}()` })
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
  isolated.value = null
  scope.value = null
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
    else if (scope.value) setScope(null)
    else if (isolated.value) isolate(null)
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
    <header class="mb-3 flex shrink-0 flex-wrap items-end justify-between gap-3 md:mb-4">
      <div>
        <h1 class="text-xl font-semibold text-white">Architecture</h1>
        <p class="mt-1 text-sm text-slate-500">
          <template v-if="focusId || isolated || scope">
            <button class="text-coral hover:underline" @click="setFocus(null)">{{ selectedId }}</button>
            <template v-if="isolated">
              <span class="mx-1.5 text-slate-600">/</span>
              <span class="text-slate-300">{{ isolated }}</span>
              <span class="ml-1 text-xs text-slate-600">folder</span>
            </template>
            <template v-if="focusId">
              <span class="mx-1.5 text-slate-600">/</span>
              <button
                class="font-mono"
                :class="scope ? 'text-coral hover:underline' : 'text-slate-300'"
                :disabled="!scope"
                @click="setScope(null)"
              >{{ focusId }}</button>
            </template>
            <template v-if="scope">
              <span class="mx-1.5 text-slate-600">/</span>
              <span class="mr-1.5 rounded border border-edge px-1 text-[10px] uppercase tracking-wide text-slate-400">{{ scope.kind }}</span>
              <span class="font-mono text-slate-300">{{ scope.label }}</span>
            </template>
            <span class="ml-2 text-xs text-slate-600 max-md:hidden">(esc to step out)</span>
          </template>
          <span v-else class="max-md:hidden">Structure, dependencies, and data flow - per stack</span>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2 max-md:w-full">
        <div
          v-if="!isOverview"
          class="flex overflow-hidden rounded-md border border-edge text-xs"
          :title="webglFailed ? 'WebGL is not available here - showing the 2D graph' : 'Graph view'"
        >
          <button
            class="px-3 py-1.5 transition-colors disabled:opacity-40"
            :class="show3d ? 'bg-coral/10 text-coral' : 'text-slate-400 hover:text-slate-200'"
            :disabled="webglFailed"
            @click="view3d = true"
          >
            3D
          </button>
          <button
            class="border-l border-edge px-3 py-1.5 transition-colors"
            :class="!show3d ? 'bg-coral/10 text-coral' : 'text-slate-400 hover:text-slate-200'"
            @click="view3d = false"
          >
            2D
          </button>
        </div>
        <select
          v-model="selectedId"
          class="rounded-md border border-edge bg-panel-2 px-3 py-1.5 text-xs text-slate-200 focus:border-coral/60 focus:outline-none max-md:min-w-0 max-md:flex-1"
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
          class="rounded-md border border-edge px-3 py-1.5 text-xs text-slate-400 transition-colors hover:border-coral/40 hover:text-coral disabled:opacity-50 max-md:hidden"
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
        <div class="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(min(340px,100%),1fr))]">
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
    <div v-else-if="arch" class="flex min-h-0 flex-1 flex-col gap-2 md:gap-4">
      <!-- shared header: stats + drift + focus controls (one swipeable line on a phone) -->
      <div class="flex shrink-0 flex-wrap items-center gap-2 max-md:flex-nowrap max-md:overflow-x-auto max-md:pb-1 max-md:[&>*]:shrink-0 max-md:[&>*]:whitespace-nowrap">
        <!-- narrowed to a namespace / file / class / method / endpoint -->
        <template v-if="scopeGraph">
          <span class="text-xs text-slate-500">downstream:</span>
          <button
            v-for="depth in SCOPE_DEPTHS"
            :key="depth.value"
            class="rounded-full border px-3 py-1 text-xs transition-colors"
            :class="scopeDepth === depth.value ? 'border-coral/60 text-coral' : 'border-edge text-slate-400 hover:text-slate-200'"
            :title="depth.value === 99 ? `everything reachable (up to ${SCOPE_LIMIT})` : `${depth.value} hop${depth.value === 1 ? '' : 's'} out`"
            @click="scopeDepth = depth.value"
          >
            {{ depth.label }}
          </button>
          <label v-if="scope.kind !== 'endpoint'" class="ml-1 flex cursor-pointer items-center gap-1.5 text-xs text-slate-400">
            <input v-model="scopeCallers" type="checkbox" class="accent-[#ff6b5e]" />
            callers
          </label>
          <span class="text-xs text-slate-500">
            {{ scopeGraph.seeds }} in scope · {{ visibleNodes.length }} {{ scopeNoun }} · {{ visibleEdges.length }} links
          </span>
          <span v-if="scopeGraph.truncated" class="text-xs text-amber-400" :title="`${scopeGraph.truncated} more not drawn`">
            capped at {{ SCOPE_LIMIT }}
          </span>
          <span v-if="scopeGraph.hiddenCallers" class="text-xs text-slate-500">
            +{{ scopeGraph.hiddenCallers }} callers not drawn
          </span>
          <span v-if="!visibleNodes.length" class="text-xs text-amber-400">nothing indexed for this scope</span>
          <button
            class="rounded-full border border-edge px-3 py-1 text-xs text-slate-400 transition-colors hover:border-coral/40 hover:text-coral"
            @click="setScope(null)"
          >
            &times; {{ focusId ? `back to ${focusId}` : 'back to the map' }}
          </button>
        </template>

        <!-- one solution folder and what it links to -->
        <template v-else-if="isolated">
          <span class="text-xs text-slate-500">linked libraries:</span>
          <button
            v-for="depth in ISOLATE_DEPTHS"
            :key="depth.value"
            class="rounded-full border px-3 py-1 text-xs transition-colors"
            :class="isolateDepth === depth.value ? 'border-coral/60 text-coral' : 'border-edge text-slate-400 hover:text-slate-200'"
            @click="isolateDepth = depth.value"
          >
            {{ depth.label }}
          </button>
          <label class="ml-1 flex cursor-pointer items-center gap-1.5 text-xs text-slate-400">
            <input v-model="isolateUsers" type="checkbox" class="accent-[#ff6b5e]" />
            used by
          </label>
          <span class="text-xs text-slate-500">
            {{ isolationCounts.member }} in {{ isolated }} · {{ isolationCounts.linked }} linked<template v-if="isolateUsers"> · {{ isolationCounts.user }} using it</template>
          </span>
          <button
            class="rounded-full border border-edge px-3 py-1 text-xs text-slate-400 transition-colors hover:border-coral/40 hover:text-coral"
            @click="isolate(null)"
          >
            &times; show all
          </button>
        </template>

        <template v-else-if="!focusId">
          <span
            v-for="(value, label) in arch.stats"
            :key="label"
            class="rounded-full border border-edge bg-panel px-3 py-1 text-xs text-slate-300"
          >
            {{ value }} <span class="text-slate-500">{{ label.replace('_', ' ') }}</span>
          </span>
          <span v-if="show3d" class="text-xs text-slate-600 max-md:hidden">click a folder in the map's legend to isolate it</span>
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
        v-model:pane="archPane"
        direction="vertical"
        storage-key="arch-graph"
        :labels="['Map', 'Details']"
        :initial="0.6"
        class="min-h-0 flex-1"
      >
        <template #first>
        <div class="relative h-full min-h-0">
        <ArchGraph3D
          v-if="show3d"
          :nodes="visibleNodes"
          :edges="visibleEdges"
          :mode="graphMode"
          :focus-id="graphFocusId"
          :legend="graphLegend"
          :legend-action="scopeGraph ? 'highlight' : 'isolate'"
          :isolated="isolated"
          :caption="caption"
          :palette="palette"
          :frame-focus="!scopeGraph"
          height-class="h-full"
          @select="selectNode($event)"
          @focus="onGraphFocus($event)"
          @isolate="isolate($event)"
          @unsupported="webglFailed = true"
        />
        <ArchGraph
          v-else
          :nodes="visibleNodes"
          :edges="visibleEdges"
          :mode="graphMode"
          :focus-id="graphFocusId"
          :legend="graphLegend"
          :palette="palette"
          height-class="h-full"
          @select="selectNode($event)"
          @focus="onGraphFocus($event)"
        />
        <aside
          v-if="selectedNode"
          class="absolute right-3 top-3 z-10 flex max-h-[calc(100%-24px)] max-w-[calc(100%-24px)] rounded-lg border border-edge bg-panel/95 backdrop-blur max-md:inset-x-2 max-md:top-auto max-md:bottom-2 max-md:max-h-[60%] max-md:max-w-none max-md:w-auto!"
          :style="{ width: aside.size.value + 'px' }"
        >
          <div
            class="w-1.5 shrink-0 cursor-col-resize rounded-l-lg transition-colors hover:bg-coral/40 max-md:hidden"
            :class="aside.dragging.value ? 'bg-coral/60' : ''"
            title="drag to resize - double-click to reset"
            @pointerdown="aside.onPointerDown"
            @pointermove="aside.onPointerMove"
            @pointerup="aside.onPointerUp"
            @dblclick="aside.reset"
          />
          <div class="relative min-w-0 flex-1 overflow-auto p-4">
          <button
            class="absolute right-2 top-2 rounded p-1 text-slate-500 hover:text-slate-200 md:hidden"
            aria-label="Close"
            @click="selectedNode = null"
          >
            &times;
          </button>
          <h3 class="break-all font-mono text-sm font-semibold text-white">{{ selectedNode.label }}</h3>

          <!-- a class / method / endpoint of a narrowed map -->
          <template v-if="scopedNode">
            <p class="mt-1 break-all text-xs text-slate-500">
              <span class="rounded border border-edge px-1 text-[10px] uppercase tracking-wide text-slate-400">{{ selectedNode.kind === 'method' ? `${selectedNode.typeKind} method` : selectedNode.kind }}</span>
              <template v-if="selectedNode.project"> &middot; {{ selectedNode.project }}</template>
              <template v-if="selectedNode.role === 'uses'"> &middot; reached from the scope</template>
              <template v-else-if="selectedNode.role === 'flow'"> &middot; on the path from the scope</template>
              <template v-else-if="selectedNode.role === 'usedBy'"> &middot; calls into the scope</template>
            </p>
            <p v-if="selectedNode.namespace" class="mt-1 break-all font-mono text-[11px] text-slate-400">{{ selectedNode.namespace }}</p>
            <p v-if="selectedNode.file" class="mt-0.5 break-all font-mono text-[11px] text-slate-500">
              {{ selectedNode.file }}<template v-if="selectedNode.line">:{{ selectedNode.line }}</template>
            </p>
            <div class="mt-2 flex gap-2">
              <button
                v-if="selectedNode.kind !== 'endpoint' && selectedNode.id !== graphFocusId"
                class="flex-1 rounded-md border border-coral/40 px-2 py-1 text-xs text-coral hover:bg-coral/10"
                @click="onGraphFocus(selectedNode.id)"
              >
                Narrow to this &rarr;
              </button>
              <button
                v-if="selectedNode.kind === 'endpoint'"
                class="flex-1 rounded-md border border-coral/40 px-2 py-1 text-xs text-coral hover:bg-coral/10"
                @click="inspected = selectedNode.endpoint"
              >
                Inspect call flow &rarr;
              </button>
            </div>
            <div v-if="selectedMethods.length" class="mt-3">
              <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Methods ({{ selectedMethods.length }})</p>
              <div class="mt-1 flex flex-wrap gap-1">
                <button
                  v-for="m in selectedMethods"
                  :key="m.name"
                  class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-300 hover:border-coral/40 hover:text-coral"
                  :title="`line ${m.line} - narrow the map to this method`"
                  @click="scopeMethod(selectedNode.typeName, m.name)"
                >{{ m.name }}()</button>
              </div>
            </div>
            <div v-for="[title, refs] in [['Reaches', nodeRefs.uses], ['Reached from', nodeRefs.usedBy]]" :key="title">
              <div v-if="refs.length" class="mt-3">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ title }} ({{ refs.length }})</p>
                <div class="mt-1 flex flex-col gap-0.5">
                  <button
                    v-for="r in refs"
                    :key="r.id"
                    class="flex items-center gap-2 rounded px-1 py-0.5 text-left font-mono text-[11px] text-slate-300 hover:bg-panel-2 hover:text-coral"
                    title="narrow the map to this"
                    @click="onGraphFocus(r.id)"
                  >
                    <span class="min-w-0 truncate">{{ r.label }}</span>
                    <span class="ml-auto shrink-0 text-[10px] text-slate-500">{{ r.kind }}</span>
                  </button>
                </div>
              </div>
            </div>
          </template>

          <template v-else>
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
                v-for="r in nodeRefs.uses"
                :key="r.id"
                class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >{{ r.label }}</span>
            </div>
          </div>
          <div v-if="nodeRefs.usedBy.length" class="mt-3">
            <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">Used by ({{ nodeRefs.usedBy.length }})</p>
            <div class="mt-1 flex flex-wrap gap-1">
              <span
                v-for="r in nodeRefs.usedBy"
                :key="r.id"
                class="rounded border border-edge bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >{{ r.label }}</span>
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
          </template>
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
        :labels="['Data flows', 'Methods']"
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
              :active-key="activeEndpoint"
              height-class="min-h-0 flex-1"
              @trace="traceEndpoint($event)"
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
          :active-key="activeEndpoint"
          height-class="min-h-0 flex-1"
          @trace="traceEndpoint($event)"
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
