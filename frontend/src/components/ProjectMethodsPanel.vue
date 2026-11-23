<script setup>
import { computed, inject, ref, watch } from 'vue'
import { baseName, projectTree } from '../utils/archScope'
import MethodNode from './MethodNode.vue'

// Focus-mode companion: one project's code as namespace -> file -> type ->
// methods -> what they call, and the reverse direction - methods of this
// project invoked from other projects. Clicking a name narrows the 3D map to
// it (the Architecture view's scope); the chevron only expands.
const props = defineProps({
  projectId: { type: String, required: true },
})

const ctx = inject('archCtx')
const archScope = inject('archScope', null)
const tab = ref('code')
const open = ref({})
const openTargets = ref({})
const filter = ref('')

watch(
  () => props.projectId,
  () => {
    open.value = {}
    openTargets.value = {}
    filter.value = ''
  }
)

const tree = computed(() => projectTree(ctx.value, props.projectId))
const needle = computed(() => filter.value.trim().toLowerCase())
const shown = computed(() => {
  const term = needle.value
  if (!term) return tree.value
  return tree.value
    .map((ns) => {
      const nsHit = ns.namespace.toLowerCase().includes(term)
      const files = ns.files
        .map((f) => ({
          ...f,
          types:
            nsHit || f.path.toLowerCase().includes(term)
              ? f.types
              : f.types.filter((t) => t.name.toLowerCase().includes(term) || t.methods.some((m) => m.name.toLowerCase().includes(term))),
        }))
        .filter((f) => f.types.length)
      return { ...ns, files, typeCount: files.reduce((n, f) => n + f.types.length, 0) }
    })
    .filter((ns) => ns.files.length)
})
const counts = computed(() => {
  let files = 0
  let types = 0
  let methods = 0
  for (const ns of tree.value) {
    files += ns.files.length
    for (const f of ns.files) {
      types += f.types.length
      for (const t of f.types) methods += t.methods.length
    }
  }
  return { namespaces: tree.value.length, files, types, methods }
})

// while filtering everything that matched is open
const isOpen = (key) => !!needle.value || !!open.value[key]
const toggle = (key) => (open.value[key] = !open.value[key])

const active = computed(() => archScope?.active.value || null)
function narrow(kind, key, label, extra = {}) {
  archScope?.set({ kind, key, label, ...extra })
  open.value[`${kind}:${key}`] = true
}
const rowClass = (id) =>
  active.value === id ? 'bg-coral/10 shadow-[inset_2px_0_0_#ff6b5e]' : 'hover:bg-panel-2/60'

const shortNamespace = (ns) =>
  ns === props.projectId ? '(project root)' : ns.startsWith(`${props.projectId}.`) ? ns.slice(props.projectId.length + 1) : ns
const shortFile = (path) => (path.startsWith(`${props.projectId}/`) ? path.slice(props.projectId.length + 1) : path)
const KIND_BADGE = {
  class: ['C', 'border-sky-400/40 text-sky-400'],
  interface: ['I', 'border-violet-400/40 text-violet-400'],
  record: ['R', 'border-emerald-400/40 text-emerald-400'],
}
const badge = (kind) => KIND_BADGE[kind] || KIND_BADGE.class

// methods of this project called from classes in other projects
const incoming = computed(() => {
  const rows = []
  for (const [key, callers] of Object.entries(ctx.value.callersOf)) {
    const dot = key.indexOf('.')
    const type = key.slice(0, dot)
    const info = ctx.value.types[type]
    if (info?.project !== props.projectId) continue
    // interface row is redundant when an implementation lives in this project
    if (
      info.kind === 'interface' &&
      (ctx.value.implementations[type] || []).some((impl) => ctx.value.types[impl]?.project === props.projectId)
    )
      continue
    const external = callers.filter((c) => ctx.value.types[c.class]?.project !== props.projectId)
    if (!external.length) continue
    if (needle.value && !key.toLowerCase().includes(needle.value)) continue
    rows.push({ key, type, method: key.slice(dot + 1), callers: external })
  }
  return rows.sort((a, b) => a.key.localeCompare(b.key))
})
</script>

<template>
  <section class="flex min-h-0 flex-col rounded-lg border border-edge bg-panel">
    <header class="flex shrink-0 items-center gap-2 border-b border-edge px-4 py-2">
      <button
        class="rounded-full border px-3 py-1 text-xs transition-colors"
        :class="tab === 'code' ? 'border-coral/60 text-coral' : 'border-edge text-slate-400 hover:text-slate-200'"
        :title="`${counts.namespaces} namespaces · ${counts.files} files · ${counts.types} types · ${counts.methods} methods`"
        @click="tab = 'code'"
      >
        Namespaces ({{ counts.namespaces }})
      </button>
      <button
        class="rounded-full border px-3 py-1 text-xs transition-colors"
        :class="tab === 'incoming' ? 'border-coral/60 text-coral' : 'border-edge text-slate-400 hover:text-slate-200'"
        @click="tab = 'incoming'"
      >
        Called from outside ({{ incoming.length }})
      </button>
      <input
        v-model="filter"
        placeholder="filter&hellip;"
        class="ml-auto w-40 rounded-md border border-edge bg-panel-2 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-coral/60 focus:outline-none"
      />
    </header>
    <p v-if="archScope" class="shrink-0 border-b border-edge/50 px-4 py-1 text-[10px] text-slate-500">
      click a name to narrow the 3D map to it (again to step back) &middot; the arrow expands
    </p>

    <div class="min-h-0 flex-1 overflow-auto px-2 py-2">
      <!-- namespace -> file -> type -> methods -> call tree -->
      <template v-if="tab === 'code'">
        <div v-for="ns in shown" :key="ns.namespace">
          <div class="flex items-center rounded" :class="rowClass(`namespace:${ns.namespace}`)">
            <button class="chevron" :aria-label="`expand ${ns.namespace}`" @click="toggle(`namespace:${ns.namespace}`)">
              <svg :class="{ 'rotate-90': isOpen(`namespace:${ns.namespace}`) }" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </button>
            <button
              class="flex min-w-0 flex-1 items-center gap-2 py-1 pr-2 text-left"
              :title="`${ns.namespace} - narrow the map to this namespace`"
              @click="narrow('namespace', ns.namespace, ns.namespace, { project: projectId })"
            >
              <span class="badge border-teal-400/40 text-teal-300">N</span>
              <span class="min-w-0 truncate font-mono text-xs text-slate-200">{{ shortNamespace(ns.namespace) }}</span>
              <span class="ml-auto shrink-0 text-[10px] text-slate-500">{{ ns.files.length }} files · {{ ns.typeCount }} types</span>
            </button>
          </div>

          <div v-if="isOpen(`namespace:${ns.namespace}`)" class="ml-3 border-l border-edge/60 pl-1">
            <div v-for="file in ns.files" :key="file.path">
              <div class="flex items-center rounded" :class="rowClass(`file:${file.path}`)">
                <button class="chevron" :aria-label="`expand ${file.path}`" @click="toggle(`file:${file.path}`)">
                  <svg :class="{ 'rotate-90': isOpen(`file:${file.path}`) }" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                </button>
                <button
                  class="flex min-w-0 flex-1 items-center gap-2 py-1 pr-2 text-left"
                  :title="`${file.path} - narrow the map to this file`"
                  @click="narrow('file', file.path, baseName(file.path))"
                >
                  <span class="badge border-slate-400/40 text-slate-300">F</span>
                  <span class="min-w-0 truncate font-mono text-xs">
                    <span class="text-slate-500">{{ shortFile(file.path).slice(0, -baseName(file.path).length) }}</span><span class="text-slate-200">{{ baseName(file.path) }}</span>
                  </span>
                  <span class="ml-auto shrink-0 text-[10px] text-slate-500">{{ file.types.length }} type{{ file.types.length === 1 ? '' : 's' }}</span>
                </button>
              </div>

              <div v-if="isOpen(`file:${file.path}`)" class="ml-3 border-l border-edge/60 pl-1">
                <div v-for="type in file.types" :key="type.name">
                  <div class="flex items-center rounded" :class="rowClass(`class:${type.name}`)">
                    <button
                      class="chevron"
                      :disabled="!type.methods.length"
                      :aria-label="`expand ${type.name}`"
                      @click="toggle(`class:${type.name}`)"
                    >
                      <svg v-if="type.methods.length" :class="{ 'rotate-90': isOpen(`class:${type.name}`) }" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                    <button
                      class="flex min-w-0 flex-1 items-center gap-2 py-1 pr-2 text-left"
                      :title="`${type.kind} ${type.name}, line ${type.line} - narrow the map to this ${type.kind}`"
                      @click="narrow('class', type.name, type.name)"
                    >
                      <span class="badge" :class="badge(type.kind)[1]">{{ badge(type.kind)[0] }}</span>
                      <span class="min-w-0 truncate font-mono text-xs text-slate-200">{{ type.name }}</span>
                      <span class="ml-auto shrink-0 text-[10px] text-slate-500">
                        {{ type.methods.length ? `${type.methods.length} method${type.methods.length === 1 ? '' : 's'}` : 'no methods' }}
                      </span>
                    </button>
                  </div>
                  <div v-if="type.methods.length && isOpen(`class:${type.name}`)" class="ml-3 border-l border-edge/60 pl-1">
                    <MethodNode
                      v-for="method in type.methods"
                      :key="method.name"
                      :type-name="type.name"
                      :method-name="method.name"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <p v-if="!shown.length" class="px-1.5 py-2 text-xs text-slate-500">
          {{ needle ? 'nothing matches' : 'no indexed types in this project' }}
        </p>
      </template>

      <!-- inward: who calls into this project, traversable through each caller -->
      <template v-else>
        <div v-for="row in incoming" :key="row.key">
          <div class="flex items-center rounded" :class="rowClass(`method:${row.key}`)">
            <button class="chevron" :aria-label="`expand ${row.key}`" @click="openTargets[row.key] = !openTargets[row.key]">
              <svg :class="{ 'rotate-90': openTargets[row.key] }" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </button>
            <button
              class="flex min-w-0 flex-1 items-center gap-2 py-1 pr-2 text-left"
              title="narrow the map to this method and its callers"
              @click="narrow('method', row.key, `${row.key}()`)"
            >
              <span class="min-w-0 truncate font-mono text-xs">
                <span class="text-slate-500">{{ row.type }}.</span><span class="text-slate-200">{{ row.method }}()</span>
              </span>
              <span class="ml-auto shrink-0 text-[10px] text-slate-500">&larr; {{ row.callers.length }} caller{{ row.callers.length === 1 ? '' : 's' }}</span>
            </button>
          </div>
          <div v-if="openTargets[row.key]" class="ml-4 border-l border-edge/60 pl-2">
            <MethodNode
              v-for="(caller, i) in row.callers"
              :key="i"
              :type-name="caller.class"
              :method-name="caller.method"
            />
          </div>
        </div>
        <p v-if="!incoming.length" class="px-1.5 py-2 text-xs text-slate-500">
          nothing outside this project calls into it (or callers aren't indexed)
        </p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.chevron {
  display: flex;
  width: 1.25rem;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  align-self: stretch;
  color: #64748b;
}
.chevron:hover:not(:disabled) {
  color: #e2e8f0;
}
.chevron svg {
  width: 0.75rem;
  height: 0.75rem;
  transition: transform 0.15s;
}
.badge {
  width: 1rem;
  flex-shrink: 0;
  border-width: 1px;
  border-radius: 0.25rem;
  text-align: center;
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  line-height: 1rem;
}
</style>
