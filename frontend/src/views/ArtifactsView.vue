<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetch, usePolling } from '../composables/useApi'
import { relative, stamp } from '../utils/time'
import ArtifactPreview from '../components/ArtifactPreview.vue'
import SplitPane from '../components/SplitPane.vue'

const route = useRoute()
const router = useRouter()

const items = ref(null)
const indexing = ref(false)
const backendDown = ref(false)
const query = ref('')
const kindFilter = ref('all')
const projectFilter = ref('all')
const detail = ref(null)
const detailError = ref(null)
const tab = ref('preview')

const selectedId = computed(() => route.query.id || null)

async function refresh() {
  try {
    const data = await apiFetch('/api/artifacts')
    items.value = data.artifacts
    indexing.value = data.indexing
    backendDown.value = false
    // keep the open artifact current as new versions land
    const open = data.artifacts.find((a) => a.id === selectedId.value)
    if (open && detail.value && open.updated_at !== detail.value.updated_at) loadDetail(open.id)
  } catch (err) {
    if (err.message === 'backend-unreachable') backendDown.value = true
  }
}
usePolling(refresh, 30_000)

async function loadDetail(id) {
  detailError.value = null
  try {
    detail.value = await apiFetch(`/api/artifacts/${id}`)
  } catch (err) {
    detail.value = null
    detailError.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  }
}

watch(
  selectedId,
  (id) => {
    if (id) loadDetail(id)
    else detail.value = null
  },
  { immediate: true }
)

function select(id) {
  router.replace({ query: { ...route.query, id } })
}

const projects = computed(() => {
  const counts = {}
  for (const item of items.value || []) counts[item.project] = (counts[item.project] || 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
})

const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return (items.value || []).filter((item) => {
    if (kindFilter.value !== 'all' && item.kind !== kindFilter.value) return false
    if (projectFilter.value !== 'all' && item.project !== projectFilter.value) return false
    if (!needle) return true
    return [item.title, item.description, item.project, item.source_path]
      .filter(Boolean)
      .some((text) => text.toLowerCase().includes(needle))
  })
})

const STATUS_NOTES = {
  stale: 'The local copy may be behind the live version - a later edit could not be replayed or a file could not be captured.',
  missing: 'No local copy - the published source file was already gone when Watchtower indexed it.',
}

const canPreview = computed(
  () => detail.value && detail.value.kind === 'artifact' && detail.value.content_file
)
const localUrl = computed(() =>
  canPreview.value ? `/api/artifacts/${detail.value.id}/content/${encodeURI(detail.value.content_file)}` : null
)

watch(canPreview, (can) => {
  if (!can) tab.value = 'history'
})
watch(selectedId, () => {
  tab.value = 'preview'
})

const ACTION_LABELS = { publish: 'published', edit: 'edited', delete: 'deleted' }
const shortSession = (id) => id.slice(0, 8)
</script>

<template>
  <div class="flex h-full flex-col">
    <header class="mb-6 flex shrink-0 items-end justify-between gap-6">
      <div>
        <h1 class="text-xl font-semibold text-white">Artifacts</h1>
        <p class="mt-1 text-sm text-slate-500">
          Pages and docs published from agent sessions
          <span v-if="indexing" class="ml-2 text-amber-400/80">- indexing transcripts…</span>
        </p>
      </div>
      <div class="flex items-center gap-3">
        <div class="flex overflow-hidden rounded-md border border-edge text-xs">
          <button
            v-for="option in [['all', 'All'], ['artifact', 'Artifacts'], ['doc', 'Docs']]"
            :key="option[0]"
            class="px-2.5 py-1.5 transition-colors"
            :class="kindFilter === option[0] ? 'bg-panel-2 text-coral' : 'text-slate-400 hover:text-slate-200'"
            @click="kindFilter = option[0]"
          >
            {{ option[1] }}
          </button>
        </div>
        <select
          v-model="projectFilter"
          class="rounded-md border border-edge bg-panel px-2 py-1.5 text-xs text-slate-300 focus:border-coral/60 focus:outline-none"
        >
          <option value="all">All projects</option>
          <option v-for="[name, count] in projects" :key="name" :value="name">{{ name }} ({{ count }})</option>
        </select>
        <input
          v-model="query"
          type="search"
          placeholder="Search titles, descriptions…"
          class="w-64 rounded-md border border-edge bg-panel px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:border-coral/60 focus:outline-none"
        />
      </div>
    </header>

    <div
      v-if="backendDown"
      class="mb-6 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300"
    >
      Backend not running on :8765
    </div>

    <SplitPane direction="horizontal" storage-key="artifacts" :initial="0.34" class="min-h-0 flex-1">
      <template #first>
        <section class="flex h-full min-h-0 flex-col pr-3">
          <h2 class="mb-3 flex shrink-0 items-baseline justify-between text-xs font-semibold uppercase tracking-widest text-slate-500">
            <span>Published</span>
            <span v-if="items" class="font-mono normal-case tracking-normal text-slate-600">{{ filtered.length }} / {{ items.length }}</span>
          </h2>
          <div v-if="items === null" class="space-y-2">
            <div v-for="i in 5" :key="i" class="h-16 animate-pulse rounded-lg border border-edge bg-panel/50" />
          </div>
          <p
            v-else-if="!filtered.length"
            class="rounded-lg border border-dashed border-edge bg-panel/40 px-6 py-10 text-center text-sm text-slate-500"
          >
            {{ items.length ? 'Nothing matches the filters' : indexing ? 'Indexing session transcripts…' : 'No artifacts published yet' }}
          </p>
          <ul v-else class="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
            <li v-for="item in filtered" :key="item.id">
              <button
                class="w-full rounded-lg border px-3 py-2.5 text-left transition-colors"
                :class="
                  item.id === selectedId
                    ? 'border-coral/50 bg-panel-2'
                    : 'border-edge bg-panel hover:border-slate-600'
                "
                @click="select(item.id)"
              >
                <div class="flex items-center gap-2">
                  <span v-if="item.favicon" class="text-sm leading-none">{{ item.favicon }}</span>
                  <span
                    class="min-w-0 truncate text-sm font-medium"
                    :class="item.deleted ? 'text-slate-500 line-through' : 'text-slate-100'"
                    :title="item.title"
                  >
                    {{ item.title }}
                  </span>
                  <span
                    class="ml-auto shrink-0 rounded border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide"
                    :class="item.kind === 'doc' ? 'border-violet-500/40 text-violet-300' : 'border-sky-500/40 text-sky-300'"
                  >
                    {{ item.kind_label }}
                  </span>
                </div>
                <div class="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                  <span class="truncate">{{ item.project }}</span>
                  <span>&middot;</span>
                  <span class="shrink-0">{{ relative(item.updated_at) }}</span>
                  <span class="ml-auto shrink-0 font-mono text-[10px] text-slate-600">
                    {{ item.op_count }} ver{{ item.op_count === 1 ? '' : 's' }}
                    <template v-if="item.session_count > 1"> &middot; {{ item.session_count }} sessions</template>
                  </span>
                </div>
              </button>
            </li>
          </ul>
        </section>
      </template>

      <template #second>
        <section class="flex h-full min-h-0 flex-col pl-3">
          <div
            v-if="!selectedId"
            class="flex flex-1 items-center justify-center rounded-lg border border-dashed border-edge bg-panel/30 text-sm text-slate-500"
          >
            Select an artifact to preview it and see its history
          </div>
          <p v-else-if="detailError" class="text-sm text-red-400">{{ detailError }}</p>
          <div v-else-if="!detail" class="h-40 animate-pulse rounded-lg border border-edge bg-panel/50" />

          <template v-else>
            <div class="shrink-0 rounded-lg border border-edge bg-panel p-4">
              <div class="flex items-start gap-3">
                <span v-if="detail.favicon" class="text-xl leading-none">{{ detail.favicon }}</span>
                <div class="min-w-0 flex-1">
                  <h2 class="truncate text-base font-semibold text-white" :title="detail.title">{{ detail.title }}</h2>
                  <p v-if="detail.description" class="mt-1 text-xs leading-relaxed text-slate-400">{{ detail.description }}</p>
                </div>
                <div class="flex shrink-0 items-center gap-2">
                  <a
                    v-if="localUrl && !/\.(md|markdown)$/i.test(detail.content_file)"
                    :href="localUrl"
                    target="_blank"
                    rel="noopener"
                    class="rounded-md border border-edge px-2.5 py-1.5 text-xs text-slate-300 hover:border-slate-500"
                    title="Open the local copy in a new window"
                  >
                    Local copy ↗
                  </a>
                  <a
                    v-if="detail.url"
                    :href="detail.url"
                    target="_blank"
                    rel="noopener"
                    class="rounded-md border border-coral/50 bg-coral/10 px-2.5 py-1.5 text-xs font-medium text-coral hover:bg-coral/20"
                  >
                    Open in Claude ↗
                  </a>
                </div>
              </div>

              <dl class="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs xl:grid-cols-4">
                <div>
                  <dt class="text-[10px] uppercase tracking-wide text-slate-600">Project</dt>
                  <dd class="truncate text-slate-300">{{ detail.project }}</dd>
                </div>
                <div>
                  <dt class="text-[10px] uppercase tracking-wide text-slate-600">Updated</dt>
                  <dd class="text-slate-300" :title="detail.updated_at">{{ stamp(detail.updated_at) }}</dd>
                </div>
                <div>
                  <dt class="text-[10px] uppercase tracking-wide text-slate-600">Created</dt>
                  <dd class="text-slate-300" :title="detail.created_at">{{ stamp(detail.created_at) }}</dd>
                </div>
                <div>
                  <dt class="text-[10px] uppercase tracking-wide text-slate-600">Versions / sharing</dt>
                  <dd class="text-slate-300">
                    {{ detail.op_count }}<template v-if="detail.sharing"> &middot; {{ detail.sharing }}</template>
                    <span v-if="detail.deleted" class="ml-1 text-red-400">deleted</span>
                  </dd>
                </div>
              </dl>
              <p v-if="detail.source_path" class="mt-2 truncate font-mono text-[10px] text-slate-600" :title="detail.source_path">
                source: {{ detail.source_path }}
              </p>
              <p
                v-if="STATUS_NOTES[detail.content_status] && detail.kind === 'artifact'"
                class="mt-2 text-[11px] text-amber-400/90"
              >
                {{ STATUS_NOTES[detail.content_status] }}
              </p>
            </div>

            <div class="mb-2 mt-3 flex shrink-0 gap-4 text-xs">
              <button
                v-if="canPreview"
                class="border-b-2 pb-1"
                :class="tab === 'preview' ? 'border-coral text-coral' : 'border-transparent text-slate-400 hover:text-slate-200'"
                @click="tab = 'preview'"
              >
                Preview
              </button>
              <button
                class="border-b-2 pb-1"
                :class="tab === 'history' ? 'border-coral text-coral' : 'border-transparent text-slate-400 hover:text-slate-200'"
                @click="tab = 'history'"
              >
                History ({{ detail.ops.length }})
              </button>
              <span v-if="!canPreview && detail.kind === 'doc'" class="ml-auto text-slate-500">
                Docs live in the docs service - open them in Claude
              </span>
            </div>

            <div class="min-h-0 flex-1">
              <ArtifactPreview v-if="tab === 'preview' && canPreview" :artifact="detail" />
              <ol v-else class="h-full space-y-1 overflow-auto rounded-lg border border-edge bg-panel p-3">
                <li
                  v-for="(op, i) in detail.ops"
                  :key="i"
                  class="flex items-baseline gap-3 border-b border-edge/40 py-1.5 text-xs last:border-0"
                >
                  <span class="w-28 shrink-0 font-mono text-[10px] text-slate-600" :title="op.ts">{{ stamp(op.ts) }}</span>
                  <span class="w-16 shrink-0" :class="op.action === 'delete' ? 'text-red-400' : op.action === 'edit' ? 'text-amber-400' : 'text-emerald-400'">
                    {{ ACTION_LABELS[op.action] || op.action }}
                  </span>
                  <span class="min-w-0 flex-1 truncate text-slate-300" :title="op.label || op.version">
                    {{ op.label || op.version || '' }}
                  </span>
                  <RouterLink
                    :to="{ path: '/activity', query: { session: op.session_id } }"
                    class="shrink-0 truncate text-[10px] text-slate-500 hover:text-coral"
                    :title="op.session_title || op.session_id"
                  >
                    {{ op.session_title ? op.session_title.slice(0, 34) : shortSession(op.session_id) }}
                  </RouterLink>
                </li>
              </ol>
            </div>
          </template>
        </section>
      </template>
    </SplitPane>
  </div>
</template>
