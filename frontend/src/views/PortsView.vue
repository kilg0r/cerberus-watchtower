<script setup>
import { computed, ref } from 'vue'
import { apiFetch, usePolling } from '../composables/useApi'

const data = ref(null)
const backendDown = ref(false)
const filter = ref('')
const exposedOnly = ref(false)
const showUdp = ref(false)

async function refresh() {
  try {
    data.value = await apiFetch('/api/ports')
    backendDown.value = false
  } catch (err) {
    if (err.message === 'backend-unreachable') backendDown.value = true
  }
}
usePolling(refresh, 10_000)

const listeners = computed(() => {
  if (!data.value) return []
  const term = filter.value.toLowerCase()
  return data.value.listeners.filter((l) => {
    if (!showUdp.value && l.proto === 'udp') return false
    if (exposedOnly.value && !l.exposed) return false
    if (!term) return true
    return [String(l.port), l.process, l.label, l.cmdline, l.address]
      .filter(Boolean)
      .some((field) => field.toLowerCase().includes(term))
  })
})

const talkers = computed(() => data.value?.connections || [])

function uptime(seconds) {
  if (seconds == null) return ''
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`
  return `${Math.round(seconds / 86400)}d`
}
</script>

<template>
  <div class="flex h-full flex-col">
    <header class="mb-6 flex shrink-0 items-end justify-between">
      <div>
        <h1 class="text-xl font-semibold text-white">Ports</h1>
        <p class="mt-1 text-sm text-slate-500">Live socket activity on this machine</p>
      </div>
      <p v-if="data" class="text-xs text-slate-500">
        refreshed {{ new Date(data.scanned_at).toLocaleTimeString() }} &middot; 10s poll
      </p>
    </header>

    <div
      v-if="backendDown"
      class="mb-6 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300"
    >
      Backend not running on :8765
    </div>

    <div v-if="data === null && !backendDown" class="space-y-3">
      <div v-for="i in 3" :key="i" class="h-24 animate-pulse rounded-lg border border-edge bg-panel/50" />
    </div>

    <div v-else-if="data" class="flex min-h-0 flex-1 flex-col gap-6">
      <!-- stats -->
      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <span class="rounded-full border border-edge bg-panel px-3 py-1 text-xs text-slate-300">
          {{ data.stats.tcp_listeners }} <span class="text-slate-500">tcp listening</span>
        </span>
        <span class="rounded-full border border-edge bg-panel px-3 py-1 text-xs text-slate-300">
          {{ data.stats.udp_bound }} <span class="text-slate-500">udp bound</span>
        </span>
        <span
          class="rounded-full border px-3 py-1 text-xs"
          :class="data.stats.lan_exposed ? 'border-amber-500/50 text-amber-400' : 'border-edge text-slate-300'"
        >
          {{ data.stats.lan_exposed }} <span class="opacity-70">LAN-exposed</span>
        </span>
        <span class="rounded-full border border-edge bg-panel px-3 py-1 text-xs text-slate-300">
          {{ data.stats.established }} <span class="text-slate-500">established</span>
        </span>
        <span class="rounded-full border border-edge bg-panel px-3 py-1 text-xs text-slate-300">
          {{ data.stats.external_hosts }} <span class="text-slate-500">external hosts</span>
        </span>
      </div>

      <div class="grid min-h-0 flex-1 gap-6 xl:grid-cols-[1fr_420px]">
        <!-- listeners -->
        <section class="flex min-h-0 flex-col">
          <div class="mb-3 flex shrink-0 flex-wrap items-center gap-3">
            <h2 class="text-xs font-semibold uppercase tracking-widest text-slate-500">Listening</h2>
            <input
              v-model="filter"
              type="text"
              placeholder="Filter by port, process, label&hellip;"
              class="w-64 rounded-md border border-edge bg-panel-2 px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-500 focus:border-coral/60 focus:outline-none"
            />
            <label class="flex cursor-pointer items-center gap-1.5 text-xs text-slate-400">
              <input v-model="exposedOnly" type="checkbox" class="accent-[#ff6b5e]" /> exposed only
            </label>
            <label class="flex cursor-pointer items-center gap-1.5 text-xs text-slate-400">
              <input v-model="showUdp" type="checkbox" class="accent-[#ff6b5e]" /> include UDP
            </label>
            <span class="text-xs text-slate-600">{{ listeners.length }} shown</span>
          </div>

          <div class="min-h-0 flex-1 overflow-auto rounded-lg border border-edge">
            <table class="w-full text-left text-xs">
              <thead class="sticky top-0 bg-panel-2">
                <tr class="text-slate-400">
                  <th class="px-3 py-2 font-medium">Port</th>
                  <th class="px-3 py-2 font-medium">Bind</th>
                  <th class="px-3 py-2 font-medium">Process</th>
                  <th class="px-3 py-2 font-medium">Service</th>
                  <th class="px-3 py-2 text-right font-medium">Up / Mem</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-edge/50">
                <tr v-for="(l, i) in listeners" :key="i" class="bg-panel hover:bg-panel-2/60">
                  <td class="px-3 py-1.5 font-mono">
                    <span class="text-slate-200">{{ l.port }}</span>
                    <span class="ml-1 text-[10px] text-slate-600">{{ l.proto }}</span>
                  </td>
                  <td class="px-3 py-1.5">
                    <span
                      class="rounded border px-1.5 py-0.5 font-mono text-[10px]"
                      :class="l.exposed ? 'border-amber-500/50 text-amber-400' : 'border-edge text-slate-500'"
                    >
                      {{ l.exposed ? 'LAN' : 'local' }}
                    </span>
                    <span class="ml-1.5 font-mono text-[10px] text-slate-600">{{ l.address }}</span>
                  </td>
                  <td class="max-w-72 px-3 py-1.5">
                    <span class="font-mono text-slate-300">{{ l.process }}</span>
                    <span v-if="l.pid" class="ml-1 text-[10px] text-slate-600">{{ l.pid }}</span>
                    <p v-if="l.cmdline" class="truncate font-mono text-[10px] text-slate-600" :title="l.cmdline">
                      {{ l.cmdline }}
                    </p>
                  </td>
                  <td class="px-3 py-1.5">
                    <span v-if="l.label" class="rounded-full border border-coral/30 px-2 py-0.5 text-[10px] text-coral">
                      {{ l.label }}
                    </span>
                  </td>
                  <td class="px-3 py-1.5 text-right font-mono text-[10px] text-slate-500">
                    {{ uptime(l.uptime_s) }}<template v-if="l.rss_mb"> &middot; {{ l.rss_mb }}MB</template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- active connections -->
        <section class="flex min-h-0 flex-col">
          <h2 class="mb-3 shrink-0 text-xs font-semibold uppercase tracking-widest text-slate-500">
            Active connections - by process &amp; remote
          </h2>
          <div class="min-h-0 flex-1 space-y-1.5 overflow-auto rounded-lg border border-edge bg-panel p-3">
            <div
              v-for="(t, i) in talkers"
              :key="i"
              class="flex items-baseline gap-2 border-b border-edge/40 py-1.5 text-xs last:border-0"
            >
              <span class="min-w-0 flex-1 truncate font-mono text-slate-300">{{ t.process }}</span>
              <span class="font-mono text-slate-500">{{ t.remote_ip }}</span>
              <span
                class="rounded border px-1 font-mono text-[10px]"
                :class="t.external ? 'border-sky-400/40 text-sky-400' : 'border-edge text-slate-600'"
              >
                {{ t.external ? 'ext' : 'lan' }}
              </span>
              <span class="w-10 text-right font-mono text-[10px] text-slate-500">&times;{{ t.count }}</span>
            </div>
            <p v-if="!talkers.length" class="py-4 text-center text-xs text-slate-500">no established connections</p>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
