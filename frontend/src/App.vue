<script setup>
const navItems = [
  {
    to: '/review',
    label: 'Review Queue',
    icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  },
  {
    to: '/activity',
    label: 'Activity',
    icon: 'M13 10V3L4 14h7v7l9-11h-7z',
  },
  {
    to: '/architecture',
    label: 'Architecture',
    icon: 'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10',
  },
]
</script>

<template>
  <div class="flex min-h-screen">
    <aside class="fixed inset-y-0 left-0 w-[220px] border-r border-edge bg-panel/60 px-4 py-6">
      <div class="mb-8 px-2">
        <span class="text-lg font-semibold tracking-tight text-white">Watchtower</span>
        <p class="mt-0.5 text-xs text-slate-500">Cerberus Labs</p>
      </div>
      <nav class="space-y-1">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-slate-400 transition-colors hover:bg-panel-2 hover:text-slate-200"
          active-class="bg-panel-2 !text-coral"
        >
          <svg class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" :d="item.icon" />
          </svg>
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>

    <main class="ml-[220px] flex-1 px-8 py-6">
      <div class="mx-auto max-w-[1720px]">
        <RouterView />
      </div>
    </main>
  </div>
</template>
