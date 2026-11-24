<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiFetch } from '../composables/useApi'
import { useEdgeResize } from '../composables/useEdgeResize'
import { useElementWidth } from '../composables/useElementWidth'
import DiffStatBadge from './DiffStatBadge.vue'
import DiffView from './DiffView.vue'

// History's diff reader, for a commit or a bundle: the changed files in a rail
// grouped by folder, and the picked file's diff filling all the height left -
// one scroller, instead of diff boxes nested inside a scrolling file list.
// j / k (or the arrows) step through the files; diffs are cached and the next
// one is fetched ahead. Below ~640px of its own width, or when folded away,
// the rail gives way to a file picker in the bar.
const props = defineProps({
  files: { type: Array, required: true },
  // path -> URL of that file's unified diff (commit or bundle endpoint)
  diffUrl: { type: Function, required: true },
})

const STATUS = {
  modified: { letter: 'M', class: 'text-amber-400' },
  added: { letter: 'A', class: 'text-emerald-400' },
  untracked: { letter: 'U', class: 'text-emerald-400' },
  deleted: { letter: 'D', class: 'text-red-400' },
  renamed: { letter: 'R', class: 'text-sky-400' },
}
const statusOf = (file) => STATUS[file.status] || STATUS.modified
const baseName = (path) => path.slice(path.lastIndexOf('/') + 1)
const dirName = (path) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')

const root = ref(null)
const railEl = ref(null)
const width = useElementWidth(root)
const roomy = computed(() => width.value >= 640)
const RAIL_KEY = 'watchtower:history:rail'
const railWanted = ref(localStorage.getItem(RAIL_KEY) !== '0')
watch(railWanted, (v) => localStorage.setItem(RAIL_KEY, v ? '1' : '0'))
const railShown = computed(() => roomy.value && railWanted.value)
const rail = useEdgeResize('history-file-rail', { initial: 280, min: 160, max: 720, edge: 'right' })

// folder by folder, then by name (a plain path sort can split a folder in two
// around its subfolders); j / k walk this order too
const ordered = computed(() =>
  [...props.files].sort(
    (a, b) =>
      dirName(a.path).localeCompare(dirName(b.path)) || baseName(a.path).localeCompare(baseName(b.path))
  )
)
const groups = computed(() => {
  const out = []
  ordered.value.forEach((file, index) => {
    const dir = dirName(file.path)
    if (out.at(-1)?.dir !== dir) out.push({ dir, items: [] })
    out.at(-1).items.push({ file, index })
  })
  return out
})

const index = ref(0)
const current = computed(() => ordered.value[index.value] || null)

// path -> { diff } | { error } | { loading: true }
const entries = ref({})
let generation = 0

async function fetchDiff(path) {
  const known = entries.value[path]
  if (known && !known.error) return
  const gen = generation
  entries.value[path] = { loading: true }
  try {
    const res = await apiFetch(props.diffUrl(path))
    if (gen === generation) entries.value[path] = { diff: res.diff || '(no diff content)' }
  } catch (err) {
    if (gen === generation) {
      entries.value[path] = { error: err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message }
    }
  }
}

watch(
  () => props.files,
  () => {
    generation++
    entries.value = {}
    index.value = 0
  }
)

watch(
  current,
  async (file) => {
    if (!file) return
    await fetchDiff(file.path)
    const next = ordered.value[index.value + 1]
    if (next) fetchDiff(next.path)
    await nextTick()
    railEl.value?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' })
  },
  { immediate: true }
)

const entry = computed(() => (current.value ? entries.value[current.value.path] : null))

function step(delta) {
  const target = index.value + delta
  if (target >= 0 && target < props.files.length) index.value = target
}

function onKeydown(event) {
  if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
  if (event.key !== 'j' && event.key !== 'k') return
  const target = event.target
  if (target?.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName)) return
  if (!root.value?.offsetParent) return // hidden (the phone layout's other pane)
  event.preventDefault()
  step(event.key === 'j' ? 1 : -1)
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div ref="root" class="flex min-h-0 flex-col">
    <p v-if="!files.length" class="py-6 text-center text-xs text-slate-500">No file changes</p>

    <template v-else>
      <!-- bar: rail toggle, prev / next, which file -->
      <div class="mb-2 flex shrink-0 items-center gap-1.5 text-xs">
        <button
          v-if="roomy"
          class="rounded p-1 transition-colors hover:bg-panel-2"
          :class="railShown ? 'text-coral' : 'text-slate-400 hover:text-slate-200'"
          :title="railShown ? 'Hide the file list' : 'Show the file list'"
          :aria-pressed="railShown"
          @click="railWanted = !railWanted"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.6" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 5.25h16.5v13.5H3.75zM9 5.25v13.5" />
          </svg>
        </button>
        <div class="flex shrink-0 overflow-hidden rounded border border-edge">
          <button
            class="px-2 py-0.5 text-slate-300 transition-colors hover:bg-panel-2 disabled:opacity-30"
            :disabled="index === 0"
            title="Previous file (k)"
            aria-label="Previous file"
            @click="step(-1)"
          >
            &lsaquo;
          </button>
          <button
            class="border-l border-edge px-2 py-0.5 text-slate-300 transition-colors hover:bg-panel-2 disabled:opacity-30"
            :disabled="index >= files.length - 1"
            title="Next file (j)"
            aria-label="Next file"
            @click="step(1)"
          >
            &rsaquo;
          </button>
        </div>
        <span class="shrink-0 font-mono text-[11px] text-slate-500">{{ index + 1 }}/{{ files.length }}</span>
        <select
          v-if="!railShown"
          v-model.number="index"
          class="min-w-0 flex-1 truncate rounded border border-edge bg-panel px-1.5 py-1 font-mono text-[11px] text-slate-200 focus:border-coral/60 focus:outline-none"
          aria-label="Changed file"
        >
          <option v-for="(file, i) in ordered" :key="file.path" :value="i">{{ file.path }}</option>
        </select>
        <span v-else class="min-w-0 flex-1 truncate font-mono text-[11px]" :title="current.path">
          <span v-if="dirName(current.path)" class="text-slate-500">{{ dirName(current.path) }}/</span><span class="text-slate-100">{{ baseName(current.path) }}</span>
        </span>
        <DiffStatBadge class="shrink-0 !text-[11px]" :additions="current.additions" :deletions="current.deletions" />
      </div>

      <div class="flex min-h-0 flex-1">
        <!-- rail -->
        <template v-if="railShown">
          <nav
            ref="railEl"
            class="shrink-0 overflow-auto rounded-md border border-edge bg-panel/50 py-1"
            :style="{ width: `${rail.size.value}px`, maxWidth: '50%' }"
            aria-label="Changed files"
          >
            <template v-for="group in groups" :key="group.dir">
              <p
                v-if="group.dir"
                class="truncate px-2 pb-0.5 pt-1.5 font-mono text-[10px] text-slate-600 first:pt-0.5"
                :title="group.dir"
              >
                {{ group.dir }}/
              </p>
              <button
                v-for="{ file, index: i } in group.items"
                :key="file.path"
                class="flex w-full items-center gap-2 py-0.5 pr-2 text-left font-mono text-[11px] transition-colors"
                :class="[group.dir ? 'pl-4' : 'pl-2', i === index ? 'bg-panel-2' : 'hover:bg-panel-2/60']"
                :aria-current="i === index"
                :title="file.path"
                @click="index = i"
              >
                <span class="w-3 shrink-0 text-center font-semibold" :class="statusOf(file).class">{{ statusOf(file).letter }}</span>
                <span class="min-w-0 flex-1 truncate" :class="i === index ? 'text-coral' : 'text-slate-300'">
                  {{ baseName(file.path) }}
                </span>
                <DiffStatBadge class="shrink-0 !gap-1 !text-[10px] opacity-80" :additions="file.additions" :deletions="file.deletions" />
              </button>
            </template>
          </nav>
          <div
            class="group flex w-2 shrink-0 cursor-col-resize items-center justify-center"
            title="drag to resize the file list - double-click to reset"
            @pointerdown="rail.onPointerDown"
            @pointermove="rail.onPointerMove"
            @pointerup="rail.onPointerUp"
            @dblclick="rail.reset"
          >
            <div
              class="h-full w-px rounded-full transition-colors"
              :class="rail.dragging.value ? 'bg-coral' : 'bg-transparent group-hover:bg-coral/60'"
            />
          </div>
        </template>

        <!-- the picked file's diff, filling the height -->
        <div class="flex min-w-0 flex-1 flex-col">
          <p v-if="!entry || entry.loading" class="py-2 text-xs text-slate-500">Loading diff&hellip;</p>
          <p v-else-if="entry.error" class="py-2 text-xs text-red-400">
            {{ entry.error }}
            <button class="ml-2 text-slate-400 underline hover:text-slate-200" @click="fetchDiff(current.path)">retry</button>
          </p>
          <DiffView v-else :key="current.path" fill class="min-h-0 flex-1" :diff="entry.diff" :path="current.path" />
        </div>
      </div>
    </template>
  </div>
</template>
