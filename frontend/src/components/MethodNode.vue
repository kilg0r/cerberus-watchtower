<script setup>
import { computed, inject, ref } from 'vue'

// Recursive method-level call tree: Type.Method -> the methods it calls.
// Interface methods fan out to implementations; MediatR sends hop to the
// handler's Handle method. Trail entries are "Type.method" keys (FlowNode's
// plain type-name entries never collide with them).
const props = defineProps({
  typeName: { type: String, required: true },
  methodName: { type: String, required: true },
  via: { type: String, default: null },
  trail: { type: Array, default: () => [] },
  autoExpand: { type: Boolean, default: false },
})

const ctx = inject('archCtx')
const expanded = ref(props.autoExpand)

const key = computed(() => `${props.typeName}.${props.methodName}`)
const isCycle = computed(() => props.trail.includes(key.value))
const typeInfo = computed(() => ctx.value.types[props.typeName])
const isInterface = computed(() => typeInfo.value?.kind === 'interface')

const methodInfo = computed(() =>
  (ctx.value.classMethods[props.typeName] || []).find((m) => m.name === props.methodName)
)

// interface method -> implementations that declare it
const implTargets = computed(() => {
  if (!isInterface.value) return []
  return (ctx.value.implementations[props.typeName] || []).filter((impl) =>
    (ctx.value.classMethods[impl] || []).some((m) => m.name === props.methodName)
  )
})

const children = computed(() => {
  if (isCycle.value) return []
  if (isInterface.value) return implTargets.value.map((impl) => ({ kind: 'impl', type: impl }))
  const info = methodInfo.value
  if (!info) return []
  return [
    ...info.calls.map((call) => ({ kind: 'call', ...call })),
    ...info.sends.map((request) => ({
      kind: 'send',
      request,
      handler: ctx.value.handlerByRequest[request]?.handler || null,
    })),
  ]
})
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
        :class="isInterface ? 'border-violet-400/40 text-violet-400' : 'border-amber-400/40 text-amber-400'"
      >M</span>
      <span v-if="via" class="font-mono text-xs text-slate-500">{{ via }} :</span>
      <span class="min-w-0 truncate font-mono text-xs">
        <span class="text-slate-500">{{ typeName }}.</span><span class="text-slate-200">{{ methodName }}()</span>
      </span>
      <span v-if="typeInfo" class="shrink-0 text-[10px] text-slate-500">{{ typeInfo.project }}</span>
      <span v-if="isCycle" class="shrink-0 text-[10px] text-amber-400" title="already shown above">&circlearrowright; cycle</span>
    </button>

    <div v-if="expanded && children.length" class="ml-4 border-l border-edge/60 pl-2">
      <template v-for="(child, i) in children" :key="i">
        <!-- interface -> the implementation's same-named method -->
        <MethodNode
          v-if="child.kind === 'impl'"
          :type-name="child.type"
          :method-name="methodName"
          :trail="[...trail, key]"
        />
        <!-- nested MediatR dispatch: request -> its handler's Handle -->
        <div v-else-if="child.kind === 'send'" class="py-0.5">
          <div class="flex items-center gap-2 px-1.5 text-xs">
            <span class="rounded border border-coral/40 px-1 font-mono text-[10px] text-coral">send</span>
            <span class="font-mono text-slate-300">{{ child.request }}</span>
          </div>
          <MethodNode
            v-if="child.handler"
            class="ml-4"
            :type-name="child.handler"
            method-name="Handle"
            :trail="[...trail, key]"
          />
        </div>
        <MethodNode
          v-else
          :type-name="child.type"
          :method-name="child.method"
          :via="child.via"
          :trail="[...trail, key]"
        />
      </template>
    </div>
  </div>
</template>
