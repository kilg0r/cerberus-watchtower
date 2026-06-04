<script setup>
import FlowNode from './FlowNode.vue'

defineProps({
  endpoint: { type: Object, required: true },
})
defineEmits(['close'])
</script>

<template>
  <div class="fixed inset-0 z-40 flex justify-end">
    <div class="absolute inset-0 bg-navy/70 backdrop-blur-sm" @click="$emit('close')" />
    <aside class="relative z-50 flex h-full w-[680px] flex-col border-l border-edge bg-panel shadow-2xl">
      <header class="flex items-start justify-between border-b border-edge px-5 py-4">
        <div class="min-w-0">
          <p class="text-[10px] font-semibold uppercase tracking-widest text-slate-500">Call flow</p>
          <p class="mt-1 break-all font-mono text-sm text-white">
            <span class="text-coral">{{ endpoint.verb }}</span> {{ endpoint.route }}
          </p>
          <p class="mt-1 text-xs text-slate-500">
            {{ endpoint.controller }} <span class="text-slate-600">({{ endpoint.project }})</span>
          </p>
        </div>
        <button
          class="ml-4 rounded-md border border-edge px-2 py-1 text-xs text-slate-400 hover:border-coral/40 hover:text-coral"
          @click="$emit('close')"
        >
          esc
        </button>
      </header>

      <div class="min-h-0 flex-1 overflow-auto px-5 py-4">
        <!-- chain: request -> handler -->
        <div class="mb-3 flex items-center gap-2 text-xs">
          <span class="rounded border border-coral/40 px-1.5 py-0.5 font-mono text-[10px] text-coral">request</span>
          <span class="font-mono text-slate-200">{{ endpoint.request || 'none detected' }}</span>
        </div>

        <div v-if="endpoint.handler">
          <p class="mb-1 text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Handler &rarr; services - expand any node to keep drilling
          </p>
          <FlowNode :type-name="endpoint.handler" :auto-expand="true" />
        </div>
        <p v-else-if="endpoint.request" class="text-xs text-amber-400">
          No handler found for {{ endpoint.request }} - possible dead dispatch or unconventional registration.
        </p>
        <p v-else class="text-xs text-slate-500">
          This endpoint doesn't dispatch a MediatR request - logic lives in the controller action itself.
        </p>
      </div>
    </aside>
  </div>
</template>
