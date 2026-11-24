<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetch, usePolling } from '../composables/useApi'
import { useArtifactTheme } from '../composables/useArtifactTheme'
import { useNarrow } from '../composables/useNarrow'
import { relative, stamp } from '../utils/time'
import ArtifactPreview from '../components/ArtifactPreview.vue'
import ArtifactThemeToggle from '../components/ArtifactThemeToggle.vue'
import ExpandButton from '../components/ExpandButton.vue'
import PinIcon from '../components/PinIcon.vue'
import SplitPane from '../components/SplitPane.vue'

const route = useRoute()
const router = useRouter()
const theme = useArtifactTheme() // preview light/dark, also used for Open in Chrome

const items = ref(null)
const indexing = ref(false)
const backendDown = ref(false)
const query = ref('')
const kindFilter = ref('all')
const projectFilter = ref('all')
const detail = ref(null)
const detailError = ref(null)
const tab = ref('preview')
const browserName = ref(null)

const selectedId = computed(() => route.query.id || null)

async function refresh() {
  try {
    const data = await apiFetch('/api/artifacts')
    items.value = data.artifacts
    indexing.value = data.indexing
    browserName.value = data.browser || null
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

// phone layout: which split pane shows (picking an artifact opens its preview)
const pane = ref(route.query.id ? 'second' : 'first')
const narrow = useNarrow()
// full screen: the preview alone over the whole page, tab bar included. A phone
// opens an artifact this way; elsewhere it's the Full screen button.
const fullscreen = ref(narrow.value && !!route.query.id)

function select(id) {
  router.replace({ query: { ...route.query, id } })
  pane.value = 'second'
  if (narrow.value) fullscreen.value = true
}

// back to the list (phone), or to the detail card under the preview
function exitFullscreen(toList = false) {
  fullscreen.value = false
  if (toList) pane.value = 'first'
}

const projects = computed(() => {
  const counts = {}
  for (const item of items.value || []) counts[item.project] = (counts[item.project] || 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
})

const byNewest = (a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')

// pinned first in pin order (stable - the workdesk doesn't reshuffle as
// artifacts get edited), then the rest newest first. The server sorts the
// same way; sorting here too keeps optimistic pin toggles in place.
const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase()
  const matches = (items.value || []).filter((item) => {
    if (kindFilter.value !== 'all' && item.kind !== kindFilter.value) return false
    if (projectFilter.value !== 'all' && item.project !== projectFilter.value) return false
    if (!needle) return true
    return [item.title, item.description, item.project, item.source_path]
      .filter(Boolean)
      .some((text) => text.toLowerCase().includes(needle))
  })
  const pinned = matches.filter((item) => item.pinned).sort((a, b) => a.pinned_at.localeCompare(b.pinned_at))
  return [...pinned, ...matches.filter((item) => !item.pinned).sort(byNewest)]
})
const pinnedCount = computed(() => (items.value || []).filter((item) => item.pinned).length)

const STATUS_NOTES = {
  stale: 'The local copy may be behind the live version - a later edit could not be replayed or a file could not be captured.',
  missing: 'No local copy - the published source file was already gone when Watchtower indexed it.',
  none: 'No local copy to preview - use the Open buttons above to view it.',
}

const canPreview = computed(
  () => detail.value && detail.value.kind === 'artifact' && detail.value.content_file
)

// (no local copy, a doc: the detail card shows instead)
const fullscreenOn = computed(() => fullscreen.value && !!canPreview.value)

function onKeydown(event) {
  if (event.key === 'Escape' && fullscreenOn.value) exitFullscreen()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

watch(canPreview, (can) => {
  if (!can) tab.value = 'history'
})
watch(selectedId, () => {
  tab.value = 'preview'
})

// ---- workdesk actions: pin, open in the default browser ----

const flash = ref(null)
let flashTimer = null
function showFlash(text, error = false) {
  flash.value = { text, error }
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => (flash.value = null), error ? 6000 : 2500)
}

async function togglePin(item) {
  const pinned = !item.pinned
  const apply = (state) => {
    const listed = (items.value || []).find((entry) => entry.id === item.id)
    for (const target of new Set([item, listed, detail.value?.id === item.id ? detail.value : null])) {
      if (target) Object.assign(target, state)
    }
  }
  const previous = { pinned: item.pinned, pinned_at: item.pinned_at }
  apply({ pinned, pinned_at: pinned ? new Date().toISOString() : null })
  try {
    const result = await apiFetch(`/api/artifacts/${item.id}/pin`, { method: pinned ? 'PUT' : 'DELETE' })
    apply({ pinned: result.pinned, pinned_at: result.pinned_at })
  } catch (err) {
    apply(previous)
    showFlash(`Could not ${pinned ? 'pin' : 'unpin'}: ${err.message}`, true)
  }
}

const browserLabel = computed(() => browserName.value || 'browser')

// the artifact itself as a standalone page when there's a local HTML copy;
// otherwise (docs, markdown, no copy captured) its claude.ai page
// "Open in Chrome" launches the browser on the machine running Watchtower, so
// it only makes sense there - not on a phone reaching it over the LAN
const onThisMachine = ['127.0.0.1', 'localhost', '[::1]'].includes(window.location.hostname)

function openTarget(item) {
  if (item.kind === 'artifact' && /\.html?$/i.test(item.content_file || '')) return 'page'
  return item.url ? 'claude' : null
}

function openTitle(item) {
  const where = browserLabel.value
  if (openTarget(item) === 'page') {
    return item.content_status === 'stale'
      ? `Open the local copy in ${where} (may be behind the live version)`
      : `Open the page in ${where}`
  }
  return `No local page copy - open the claude.ai page in ${where}`
}

async function openInBrowser(item) {
  const target = openTarget(item)
  if (!target) return
  try {
    const result = await apiFetch(`/api/artifacts/${item.id}/open`, {
      method: 'POST',
      body: JSON.stringify({ target, theme: theme.value }),
    })
    showFlash(`Opened ${target === 'page' ? 'page' : 'claude.ai page'} in ${result.browser || 'your browser'}`)
  } catch (err) {
    showFlash(err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message, true)
  }
}

// ---- link an artifact by URL (its publishing transcript is gone) ----

const linkOpen = ref(false)
const linkForm = ref({ url: '', title: '', repo_id: '' })
const linkBusy = ref(false)
const linkError = ref(null)
const linkInput = ref(null)
const repos = ref([])

async function toggleLinkForm() {
  linkOpen.value = !linkOpen.value
  linkError.value = null
  if (!linkOpen.value) return
  nextTick(() => linkInput.value?.focus())
  if (!repos.value.length) {
    try {
      repos.value = (await apiFetch('/api/repos')).sort((a, b) => a.name.localeCompare(b.name))
    } catch {
      // the project picker is optional
    }
  }
}

async function submitLink() {
  const url = linkForm.value.url.trim()
  if (!url || linkBusy.value) return
  linkBusy.value = true
  linkError.value = null
  try {
    const body = { url }
    if (linkForm.value.title.trim()) body.title = linkForm.value.title.trim()
    if (linkForm.value.repo_id) body.repo_id = linkForm.value.repo_id
    const result = await apiFetch('/api/artifacts/link', { method: 'POST', body: JSON.stringify(body) })
    linkForm.value = { url: '', title: '', repo_id: '' }
    linkOpen.value = false
    await refresh()
    select(result.id)
    showFlash(result.created ? 'Artifact linked' : 'Already in the index - details updated')
  } catch (err) {
    linkError.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    linkBusy.value = false
  }
}

const ACTION_LABELS = { publish: 'published', edit: 'edited', delete: 'deleted', link: 'linked' }
const ACTION_COLORS = { delete: 'text-red-400', edit: 'text-amber-400', link: 'text-sky-400' }
const shortSession = (id) => id.slice(0, 8)
</script>

<template>
  <div class="flex h-full flex-col">
    <header class="mb-3 flex shrink-0 flex-wrap items-end justify-between gap-x-6 gap-y-2 md:mb-6">
      <div>
        <h1 class="text-xl font-semibold text-white">Artifacts</h1>
        <p class="mt-1 text-sm text-slate-500 max-md:hidden">
          Pages and docs published from agent sessions
          <span v-if="indexing" class="ml-2 text-amber-400/80">- indexing transcripts…</span>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2 max-md:w-full md:gap-3">
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
          class="rounded-md border border-edge bg-panel px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:border-coral/60 focus:outline-none max-md:order-last max-md:w-full md:w-64"
        />
        <button
          class="rounded-md border px-2.5 py-1.5 text-xs transition-colors"
          :class="linkOpen ? 'border-coral/50 bg-panel-2 text-coral' : 'border-edge text-slate-300 hover:border-slate-500'"
          title="Add an artifact by its claude.ai link"
          :aria-expanded="linkOpen"
          @click="toggleLinkForm"
        >
          + Link artifact
        </button>
      </div>
    </header>

    <form
      v-if="linkOpen"
      class="mb-5 flex shrink-0 flex-wrap items-end gap-3 rounded-lg border border-edge bg-panel p-3"
      @submit.prevent="submitLink"
      @keydown.esc="linkOpen = false"
    >
      <label class="flex min-w-0 flex-1 basis-full flex-col gap-1 text-[10px] md:min-w-72 md:basis-auto uppercase tracking-wide text-slate-500">
        claude.ai link
        <input
          ref="linkInput"
          v-model="linkForm.url"
          required
          placeholder="https://claude.ai/artifact/…"
          class="rounded-md border border-edge bg-navy px-3 py-1.5 font-mono text-xs normal-case tracking-normal text-slate-200 placeholder:text-slate-600 focus:border-coral/60 focus:outline-none"
        />
      </label>
      <label class="flex w-full flex-col gap-1 text-[10px] md:w-64 uppercase tracking-wide text-slate-500">
        Title (optional)
        <input
          v-model="linkForm.title"
          maxlength="300"
          class="rounded-md border border-edge bg-navy px-3 py-1.5 text-xs normal-case tracking-normal text-slate-200 focus:border-coral/60 focus:outline-none"
        />
      </label>
      <label class="flex w-full flex-col gap-1 text-[10px] md:w-56 uppercase tracking-wide text-slate-500">
        Project
        <select
          v-model="linkForm.repo_id"
          class="rounded-md border border-edge bg-navy px-2 py-1.5 text-xs normal-case tracking-normal text-slate-300 focus:border-coral/60 focus:outline-none"
        >
          <option value="">Unassigned</option>
          <option v-for="repo in repos" :key="repo.id" :value="repo.id">{{ repo.name }}</option>
        </select>
      </label>
      <button
        type="submit"
        :disabled="linkBusy || !linkForm.url.trim()"
        class="rounded-md border border-coral/50 bg-coral/10 px-3 py-1.5 text-xs font-medium text-coral hover:bg-coral/20 disabled:opacity-40"
      >
        {{ linkBusy ? 'Linking…' : 'Link' }}
      </button>
      <button type="button" class="px-1 py-1.5 text-xs text-slate-500 hover:text-slate-300" @click="linkOpen = false">
        Cancel
      </button>
      <p class="basis-full text-[11px] text-slate-500">
        For artifacts whose session transcript is gone. Watchtower can't sign in to claude.ai, so a linked artifact
        has no preview, and its title is whatever you enter here.
      </p>
      <p v-if="linkError" class="basis-full text-xs text-red-400">{{ linkError }}</p>
    </form>

    <div
      v-if="backendDown"
      class="mb-6 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300"
    >
      Backend not running on :8765
    </div>

    <SplitPane v-model:pane="pane" direction="horizontal" storage-key="artifacts" :initial="0.34" :labels="['List', 'Preview']" class="min-h-0 flex-1">
      <template #first>
        <section class="flex h-full min-h-0 flex-col md:pr-3">
          <h2 class="mb-3 flex shrink-0 items-baseline justify-between text-xs font-semibold uppercase tracking-widest text-slate-500">
            <span>Published</span>
            <span v-if="items" class="font-mono normal-case tracking-normal text-slate-600">
              <template v-if="pinnedCount">{{ pinnedCount }} pinned &middot; </template>{{ filtered.length }} / {{ items.length }}
            </span>
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
            <template v-for="(item, i) in filtered" :key="item.id">
            <li v-if="i > 0 && !item.pinned && filtered[i - 1].pinned" class="py-1" aria-hidden="true">
              <div class="border-t border-edge/70" />
            </li>
            <li class="group">
              <div
                role="button"
                tabindex="0"
                class="w-full cursor-pointer rounded-lg border px-3 py-2.5 text-left transition-colors focus:outline-none focus-visible:border-coral/60"
                :class="[
                  item.id === selectedId
                    ? 'border-coral/50 bg-panel-2'
                    : 'border-edge bg-panel hover:border-slate-600',
                  item.pinned && item.id !== selectedId ? 'border-l-coral/60' : '',
                ]"
                @click="select(item.id)"
                @keydown.enter.self.prevent="select(item.id)"
                @keydown.space.self.prevent="select(item.id)"
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
                  <span class="ml-auto flex shrink-0 items-center gap-0.5">
                    <button
                      v-if="onThisMachine && openTarget(item)"
                      class="rounded p-1 text-slate-500 opacity-0 transition hover:bg-panel-2 hover:text-slate-200 focus-visible:opacity-100 group-hover:opacity-100"
                      :title="openTitle(item)"
                      :aria-label="`Open ${item.title} in ${browserLabel}`"
                      @click.stop="openInBrowser(item)"
                    >
                      <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24" aria-hidden="true">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                      </svg>
                    </button>
                    <button
                      class="rounded p-1 transition hover:bg-panel-2 focus-visible:opacity-100"
                      :class="item.pinned ? 'text-coral' : 'text-slate-500 opacity-0 hover:text-slate-200 group-hover:opacity-100'"
                      :title="item.pinned ? 'Unpin' : 'Pin to top'"
                      :aria-label="item.pinned ? `Unpin ${item.title}` : `Pin ${item.title} to top`"
                      :aria-pressed="item.pinned"
                      @click.stop="togglePin(item)"
                    >
                      <PinIcon :filled="item.pinned" class="h-3.5 w-3.5" />
                    </button>
                  </span>
                  <span
                    class="shrink-0 rounded border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide"
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
                    <template v-if="item.op_count">{{ item.op_count }} ver{{ item.op_count === 1 ? '' : 's' }}</template>
                    <template v-else>linked</template>
                    <template v-if="item.session_count > 1"> &middot; {{ item.session_count }} sessions</template>
                  </span>
                </div>
              </div>
            </li>
            </template>
          </ul>
        </section>
      </template>

      <template #second>
        <section class="flex h-full min-h-0 flex-col md:pl-3">
          <div
            v-if="!selectedId"
            class="flex flex-1 items-center justify-center rounded-lg border border-dashed border-edge bg-panel/30 text-sm text-slate-500"
          >
            Select an artifact to preview it and see its history
          </div>
          <p v-else-if="detailError" class="text-sm text-red-400">{{ detailError }}</p>
          <div v-else-if="!detail" class="h-40 animate-pulse rounded-lg border border-edge bg-panel/50" />

          <template v-else>
            <div class="shrink-0 rounded-lg border border-edge bg-panel p-3 md:p-4">
              <div class="flex flex-wrap items-start gap-3">
                <span v-if="detail.favicon" class="text-xl leading-none">{{ detail.favicon }}</span>
                <div class="min-w-0 flex-1">
                  <h2 class="truncate text-base font-semibold text-white" :title="detail.title">{{ detail.title }}</h2>
                  <p v-if="detail.description" class="mt-1 text-xs leading-relaxed text-slate-400">{{ detail.description }}</p>
                </div>
                <div class="flex shrink-0 flex-wrap items-center gap-2 max-md:w-full">
                  <button
                    class="flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-colors"
                    :class="detail.pinned ? 'border-coral/50 text-coral hover:bg-coral/10' : 'border-edge text-slate-300 hover:border-slate-500'"
                    :title="detail.pinned ? 'Unpin from the top of the list' : 'Pin to the top of the list'"
                    :aria-pressed="detail.pinned"
                    @click="togglePin(detail)"
                  >
                    <PinIcon :filled="detail.pinned" class="h-3.5 w-3.5" />
                    {{ detail.pinned ? 'Pinned' : 'Pin' }}
                  </button>
                  <button
                    v-if="onThisMachine && openTarget(detail)"
                    class="rounded-md border border-edge px-2.5 py-1.5 text-xs text-slate-300 hover:border-slate-500"
                    :title="openTitle(detail)"
                    @click="openInBrowser(detail)"
                  >
                    Open in {{ browserLabel }} ↗
                  </button>
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
                    {{ detail.op_count || 'linked' }}<template v-if="detail.sharing"> &middot; {{ detail.sharing }}</template>
                    <span v-if="detail.deleted" class="ml-1 text-red-400">deleted</span>
                  </dd>
                </div>
              </dl>
              <p v-if="detail.source_path" class="mt-2 truncate font-mono text-[10px] text-slate-600" :title="detail.source_path">
                source: {{ detail.source_path }}
              </p>
              <p
                v-if="STATUS_NOTES[detail.content_status] && detail.kind === 'artifact'"
                class="mt-2 text-[11px]"
                :class="detail.content_status === 'none' ? 'text-slate-500' : 'text-amber-400/90'"
              >
                {{ STATUS_NOTES[detail.content_status] }}
              </p>
            </div>

            <div class="mb-2 mt-3 flex shrink-0 gap-3 text-xs md:gap-4">
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
              <template v-if="tab === 'preview' && canPreview">
                <ArtifactThemeToggle class="-mt-1 ml-auto" />
                <ExpandButton
                  class="-mt-1 -mr-1 text-[11px]"
                  title="Full screen (esc to exit)"
                  aria-label="Full screen"
                  @click="fullscreen = true"
                >
                  <span class="max-md:hidden">Full screen</span>
                </ExpandButton>
              </template>
            </div>

            <div class="min-h-0 flex-1">
              <template v-if="tab === 'preview' && canPreview">
                <ArtifactPreview v-if="!fullscreenOn" :artifact="detail" />
              </template>
              <ol v-else class="h-full space-y-1 overflow-auto rounded-lg border border-edge bg-panel p-3">
                <li
                  v-for="(op, i) in detail.ops"
                  :key="i"
                  class="flex items-baseline gap-3 border-b border-edge/40 py-1.5 text-xs last:border-0"
                >
                  <span class="w-28 shrink-0 font-mono text-[10px] text-slate-600" :title="op.ts">{{ stamp(op.ts) }}</span>
                  <span class="w-16 shrink-0" :class="ACTION_COLORS[op.action] || 'text-emerald-400'">
                    {{ ACTION_LABELS[op.action] || op.action }}
                  </span>
                  <span class="min-w-0 flex-1 truncate text-slate-300" :title="op.label || op.version">
                    {{ op.label || op.version || '' }}
                  </span>
                  <RouterLink
                    v-if="op.session_id"
                    :to="{ path: '/activity', query: { session: op.session_id } }"
                    class="shrink-0 truncate text-[10px] text-slate-500 hover:text-coral"
                    :title="op.session_title || op.session_id"
                  >
                    {{ op.session_title ? op.session_title.slice(0, 34) : shortSession(op.session_id) }}
                  </RouterLink>
                  <span v-else class="shrink-0 text-[10px] text-slate-600" title="Added by its claude.ai link - no session">
                    by link
                  </span>
                </li>
              </ol>
            </div>
          </template>
        </section>
      </template>
    </SplitPane>

    <!-- full screen: the preview over everything, with a slim bar (safe areas for
         the notch and home indicator when launched from the home screen) -->
    <Teleport to="body">
      <div
        v-if="fullscreenOn"
        class="fixed inset-0 z-50 flex flex-col bg-navy pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)]"
        role="dialog"
        aria-modal="true"
        :aria-label="detail.title"
      >
        <div class="flex shrink-0 items-center gap-2 border-b border-edge bg-panel px-2 pt-[max(0.375rem,env(safe-area-inset-top))] pb-1.5">
          <button
            v-if="narrow"
            class="flex shrink-0 items-center gap-0.5 rounded py-1 pl-0.5 pr-1.5 text-xs text-slate-300 hover:bg-panel-2"
            aria-label="Back to the list"
            @click="exitFullscreen(true)"
          >
            <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
            List
          </button>
          <span v-if="detail.favicon" class="shrink-0 text-base leading-none">{{ detail.favicon }}</span>
          <h2 class="min-w-0 flex-1 truncate text-sm font-medium text-white" :title="detail.title">{{ detail.title }}</h2>
          <ArtifactThemeToggle />
          <ExpandButton
            expanded
            title="Exit full screen - details and history (esc)"
            aria-label="Exit full screen"
            @click="exitFullscreen()"
          />
        </div>
        <ArtifactPreview class="min-h-0 flex-1 !rounded-none !border-0" :artifact="detail" />
      </div>
    </Teleport>

    <Transition
      enter-from-class="translate-y-2 opacity-0"
      enter-active-class="transition duration-150"
      leave-to-class="opacity-0"
      leave-active-class="transition duration-300"
    >
      <div
        v-if="flash"
        role="status"
        class="fixed bottom-5 right-5 z-50 max-w-sm rounded-md border px-3.5 py-2 text-xs shadow-lg tabnav:bottom-[calc(4rem+env(safe-area-inset-bottom))]"
        :class="flash.error ? 'border-red-500/40 bg-red-950/90 text-red-200' : 'border-edge bg-panel-2 text-slate-200'"
      >
        {{ flash.text }}
      </div>
    </Transition>
  </div>
</template>
