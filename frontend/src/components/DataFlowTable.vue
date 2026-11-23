<script setup>
import { computed, ref } from 'vue'

// A row click traces that endpoint's flow in the map (route -> handler ->
// what it calls); "Inspect" opens the call-flow drawer.
const props = defineProps({
  endpoints: { type: Array, required: true },
  heightClass: { type: String, default: 'max-h-[480px]' },
  // `${verb} ${route}` of the endpoint the map is narrowed to
  activeKey: { type: String, default: null },
})
defineEmits(['inspect', 'trace'])
const keyOf = (e) => `${e.verb} ${e.route}`

const filter = ref('')

const VERB_COLORS = {
  GET: 'text-sky-400 border-sky-400/40',
  POST: 'text-emerald-400 border-emerald-400/40',
  PUT: 'text-amber-400 border-amber-400/40',
  PATCH: 'text-amber-400 border-amber-400/40',
  DELETE: 'text-red-400 border-red-400/40',
}

const filtered = computed(() => {
  const term = filter.value.toLowerCase()
  if (!term) return props.endpoints
  return props.endpoints.filter((e) =>
    [e.route, e.controller, e.request, e.handler, e.handler_project]
      .filter(Boolean)
      .some((field) => field.toLowerCase().includes(term))
  )
})
</script>

<template>
  <div class="flex min-h-0 flex-col">
    <div class="mb-3 flex shrink-0 items-center gap-3">
      <input
        v-model="filter"
        type="text"
        placeholder="Filter by route, request, handler&hellip;"
        class="w-72 rounded-md border border-edge bg-panel-2 px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-500 focus:border-coral/60 focus:outline-none"
      />
      <span class="text-xs text-slate-500">{{ filtered.length }} / {{ endpoints.length }} endpoints &middot; click a row to trace its flow in the map</span>
    </div>

    <div class="overflow-auto rounded-lg border border-edge" :class="heightClass">
      <table class="w-full text-left text-xs">
        <thead class="sticky top-0 bg-panel-2">
          <tr class="text-slate-400">
            <th class="px-3 py-2 font-medium">Endpoint</th>
            <th class="px-3 py-2 font-medium">Request</th>
            <th class="px-3 py-2 font-medium">Handler</th>
            <th class="px-3 py-2 font-medium">Project</th>
            <th class="w-16 px-3 py-2" />
          </tr>
        </thead>
        <tbody class="divide-y divide-edge/50">
          <tr
            v-for="(e, i) in filtered"
            :key="i"
            class="group cursor-pointer transition-colors"
            :class="activeKey === keyOf(e) ? 'bg-coral/10 shadow-[inset_2px_0_0_#ff6b5e]' : 'bg-panel hover:bg-panel-2/60'"
            :title="e.handler ? 'trace this flow in the map (click again to step back)' : 'no handler indexed - nothing to trace past the route'"
            @click="$emit('trace', e)"
          >
            <td class="px-3 py-1.5">
              <span
                class="mr-2 inline-block w-12 rounded border px-1 text-center font-mono text-[10px]"
                :class="VERB_COLORS[e.verb] || 'text-slate-400 border-edge'"
              >
                {{ e.verb }}
              </span>
              <span class="font-mono text-slate-300">{{ e.route }}</span>
            </td>
            <td class="px-3 py-1.5 font-mono text-slate-400">{{ e.request || '-' }}</td>
            <td class="px-3 py-1.5 font-mono" :class="e.handler ? 'text-slate-400' : 'text-amber-400'">
              {{ e.handler || (e.request ? 'no handler found' : '-') }}
            </td>
            <td class="px-3 py-1.5 text-slate-500">{{ e.handler_project || '-' }}</td>
            <td class="px-2 py-1 text-right">
              <button
                class="rounded border border-edge px-1.5 py-0.5 text-[10px] text-slate-500 opacity-0 transition hover:border-coral/40 hover:text-coral group-hover:opacity-100"
                :class="activeKey === keyOf(e) ? 'opacity-100' : ''"
                title="open the call-flow drawer"
                @click.stop="$emit('inspect', e)"
              >
                Inspect
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
