<script setup>
import { computed, inject, ref } from 'vue'

const props = defineProps({
  typeName: { type: String, required: true },
  fieldLabel: { type: String, default: null },
  trail: { type: Array, default: () => [] }, // ancestor chain, guards cycles
  autoExpand: { type: Boolean, default: false },
})

const ctx = inject('archCtx')
const expanded = ref(props.autoExpand)

// dep types that are plumbing, not flow - hidden from the tree
const INFRA =
  /^(ILogger|IMapper|IMediator|ISender|IPublisher|IConfiguration|IOptions|IServiceProvider|IServiceScope|IHttpContext|IMemoryCache|IDistributedCache|IWebHost|IHostEnv|IHostApplication|TimeProvider|CancellationToken|IDbContextFactory|IStringLocalizer|JsonSerializerOptions)/

const info = computed(() => ctx.value.types[props.typeName])
const isCycle = computed(() => props.trail.includes(props.typeName))

const implementations = computed(() =>
  info.value?.kind === 'interface' ? ctx.value.implementations[props.typeName] || [] : []
)

// for a concrete class: services actually used + injected deps + nested dispatches
function classChildren(className) {
  const uses = (ctx.value.classServiceUses[className] || []).map((u) => ({
    kind: 'dep',
    type: u.type,
    field: u.field,
  }))
  const seen = new Set(uses.map((u) => u.type))
  const ctorDeps = (ctx.value.classDeps[className] || [])
    .filter((d) => !INFRA.test(d.name) && ctx.value.types[d.name] && !seen.has(d.name))
    .map((d) => ({ kind: 'dep', type: d.name, field: null }))
  const sends = (ctx.value.classSends[className] || []).map((request) => ({
    kind: 'send',
    request,
    handler: ctx.value.handlerByRequest[request]?.handler || null,
  }))
  return [...uses, ...ctorDeps, ...sends]
}

const children = computed(() => {
  if (isCycle.value) return []
  if (info.value?.kind === 'interface') {
    return implementations.value.map((impl) => ({ kind: 'impl', type: impl }))
  }
  return classChildren(props.typeName)
})

const KIND_BADGES = {
  interface: { letter: 'I', class: 'text-violet-400 border-violet-400/40' },
  class: { letter: 'C', class: 'text-sky-400 border-sky-400/40' },
  record: { letter: 'R', class: 'text-emerald-400 border-emerald-400/40' },
}
const badge = computed(() => KIND_BADGES[info.value?.kind] || KIND_BADGES.class)
</script>

<template>
  <div>
    <button
      class="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-panel-2/60"
      :disabled="!children.length"
      @click="expanded = !expanded"
    >
      <svg
        v-if="children.length"
        class="h-3 w-3 shrink-0 text-slate-500 transition-transform"
        :class="{ 'rotate-90': expanded }"
        fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"
      >
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
      </svg>
      <span v-else class="w-3 shrink-0" />
      <span
        class="w-4 shrink-0 rounded border text-center font-mono text-[10px]"
        :class="badge.class"
      >{{ badge.letter }}</span>
      <span v-if="fieldLabel" class="font-mono text-xs text-slate-500">{{ fieldLabel }} :</span>
      <span class="font-mono text-xs text-slate-200">{{ typeName }}</span>
      <span v-if="info" class="text-[10px] text-slate-500">{{ info.project }}</span>
      <span v-if="isCycle" class="text-[10px] text-amber-400" title="already shown above">&circlearrowright; cycle</span>
    </button>

    <div v-if="expanded && children.length" class="ml-4 border-l border-edge/60 pl-2">
      <template v-for="(child, i) in children" :key="i">
        <!-- nested MediatR dispatch: request -> its handler -->
        <div v-if="child.kind === 'send'" class="py-0.5">
          <div class="flex items-center gap-2 px-1.5 text-xs">
            <span class="rounded border border-coral/40 px-1 font-mono text-[10px] text-coral">send</span>
            <span class="font-mono text-slate-300">{{ child.request }}</span>
          </div>
          <FlowNode
            v-if="child.handler"
            class="ml-4"
            :type-name="child.handler"
            :trail="[...trail, typeName]"
          />
        </div>
        <FlowNode
          v-else
          :type-name="child.type"
          :field-label="child.field"
          :trail="[...trail, typeName]"
        />
      </template>
    </div>
  </div>
</template>
