<script setup>
import { computed, inject, ref, watch } from 'vue'
import MethodNode from './MethodNode.vue'

// Focus-mode companion: the full method surface of one project (classes ->
// methods -> what they call), and the reverse direction - methods of this
// project invoked from other projects, traversable back through each caller.
const props = defineProps({
  projectId: { type: String, required: true },
})

const ctx = inject('archCtx')
const tab = ref('methods')
const openClasses = ref({})
const openTargets = ref({})
const filter = ref('')

watch(
  () => props.projectId,
  () => {
    openClasses.value = {}
    openTargets.value = {}
    filter.value = ''
  }
)

const classes = computed(() => {
  const needle = filter.value.toLowerCase()
  return Object.entries(ctx.value.types)
    .filter(
      ([name, info]) =>
        info.project === props.projectId &&
        info.kind !== 'interface' &&
        ctx.value.classMethods[name]?.length
    )
    .map(([name]) => ({ name, methods: ctx.value.classMethods[name] }))
    .filter(
      (c) =>
        !needle ||
        c.name.toLowerCase().includes(needle) ||
        c.methods.some((m) => m.name.toLowerCase().includes(needle))
    )
    .sort((a, b) => a.name.localeCompare(b.name))
})

const methodCount = computed(() => classes.value.reduce((n, c) => n + c.methods.length, 0))

// methods of this project called from classes in other projects
const incoming = computed(() => {
  const needle = filter.value.toLowerCase()
  const rows = []
  for (const [key, callers] of Object.entries(ctx.value.callersOf)) {
    const dot = key.indexOf('.')
    const type = key.slice(0, dot)
    const info = ctx.value.types[type]
    if (info?.project !== props.projectId) continue
    // interface row is redundant when an implementation lives in this project
    if (
      info.kind === 'interface' &&
      (ctx.value.implementations[type] || []).some(
        (impl) => ctx.value.types[impl]?.project === props.projectId
      )
    )
      continue
    const external = callers.filter(
      (c) => ctx.value.types[c.class]?.project !== props.projectId
    )
    if (!external.length) continue
    if (needle && !key.toLowerCase().includes(needle)) continue
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
        :class="tab === 'methods' ? 'border-coral/60 text-coral' : 'border-edge text-slate-400 hover:text-slate-200'"
        @click="tab = 'methods'"
      >
        Methods ({{ methodCount }})
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
        class="ml-auto w-32 rounded-md border border-edge bg-panel-2 px-2 py-1 text-xs text-slate-200 placeholder:text-slate-600 focus:border-coral/60 focus:outline-none"
      />
    </header>

    <div class="min-h-0 flex-1 overflow-auto px-3 py-2">
      <!-- outward: every class -> its methods -> call tree -->
      <template v-if="tab === 'methods'">
        <div v-for="cls in classes" :key="cls.name">
          <button
            class="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-panel-2/60"
            @click="openClasses[cls.name] = !openClasses[cls.name]"
          >
            <svg
              class="h-3 w-3 shrink-0 text-slate-500 transition-transform"
              :class="{ 'rotate-90': openClasses[cls.name] }"
              fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
            </svg>
            <span class="w-4 shrink-0 rounded border border-sky-400/40 text-center font-mono text-[10px] text-sky-400">C</span>
            <span class="min-w-0 truncate font-mono text-xs text-slate-200">{{ cls.name }}</span>
            <span class="shrink-0 text-[10px] text-slate-500">{{ cls.methods.length }} methods</span>
          </button>
          <div v-if="openClasses[cls.name]" class="ml-4 border-l border-edge/60 pl-2">
            <MethodNode
              v-for="method in cls.methods"
              :key="method.name"
              :type-name="cls.name"
              :method-name="method.name"
            />
          </div>
        </div>
        <p v-if="!classes.length" class="px-1.5 py-2 text-xs text-slate-500">
          no indexed methods in this project
        </p>
      </template>

      <!-- inward: who calls into this project, traversable through each caller -->
      <template v-else>
        <div v-for="row in incoming" :key="row.key">
          <button
            class="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-panel-2/60"
            @click="openTargets[row.key] = !openTargets[row.key]"
          >
            <svg
              class="h-3 w-3 shrink-0 text-slate-500 transition-transform"
              :class="{ 'rotate-90': openTargets[row.key] }"
              fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
            </svg>
            <span class="min-w-0 truncate font-mono text-xs">
              <span class="text-slate-500">{{ row.type }}.</span><span class="text-slate-200">{{ row.method }}()</span>
            </span>
            <span class="shrink-0 text-[10px] text-slate-500">&larr; {{ row.callers.length }} caller{{ row.callers.length === 1 ? '' : 's' }}</span>
          </button>
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
