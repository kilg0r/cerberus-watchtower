<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetch } from '../composables/useApi'
import BundleBar from '../components/BundleBar.vue'
import BundleReview from '../components/BundleReview.vue'
import CommitRow from '../components/CommitRow.vue'
import DiffStatBadge from '../components/DiffStatBadge.vue'
import GitFileRow from '../components/GitFileRow.vue'
import SplitPane from '../components/SplitPane.vue'

const ALL = '__all__'
const PAGE = 50

const route = useRoute()
const router = useRouter()

const repos = ref([])
const selectedId = ref(null)
const commits = ref([])
const loading = ref(false)
const loadingMore = ref(false)
const error = ref(null)
const hasMore = ref(false)
const limit = ref(PAGE)

// bundle selection (newest-first, mirroring the list order)
const selected = ref([])
const bundleMode = ref('range')

// right pane: a single commit's detail, or a bundle review
const rightMode = ref(null) // 'commit' | 'bundle' | null
const openSha = ref(null)
const detail = ref(null)
const detailRepo = ref(null)
const detailLoading = ref(false)
const bundleRepo = ref(null)

const isUniversal = computed(() => selectedId.value === ALL)
const selectable = computed(() => !isUniversal.value)
const selectedShas = computed(() => new Set(selected.value.map((c) => c.sha)))

const groupedRepos = computed(() => {
  const groups = new Map()
  for (const repo of repos.value) {
    if (!groups.has(repo.group)) groups.set(repo.group, [])
    groups.get(repo.group).push(repo)
  }
  return [...groups.entries()]
})

function repoOf(commit) {
  return isUniversal.value ? commit.repo_id : selectedId.value
}

// ---- loading commits ----
function commitsUrl(skip) {
  if (isUniversal.value) return `/api/git/history?limit=${limit.value}`
  return `/api/git/${selectedId.value}/commits?limit=${PAGE}&skip=${skip}`
}

async function load() {
  loading.value = true
  error.value = null
  commits.value = []
  limit.value = PAGE
  try {
    const data = await apiFetch(commitsUrl(0))
    commits.value = data
    hasMore.value = data.length >= PAGE
  } catch (err) {
    error.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  loadingMore.value = true
  try {
    if (isUniversal.value) {
      limit.value += PAGE
      const data = await apiFetch(commitsUrl(0))
      hasMore.value = data.length >= limit.value
      commits.value = data
    } else {
      const data = await apiFetch(commitsUrl(commits.value.length))
      hasMore.value = data.length >= PAGE
      commits.value = [...commits.value, ...data]
    }
  } catch {
    hasMore.value = false
  } finally {
    loadingMore.value = false
  }
}

function onRepoChange() {
  selected.value = []
  rightMode.value = null
  openSha.value = null
  detail.value = null
  load()
}

// ---- bundle selection ----
function toggle(commit) {
  if (!selectable.value) return
  const exists = selectedShas.value.has(commit.sha)
  if (exists) {
    selected.value = selected.value.filter((c) => c.sha !== commit.sha)
  } else {
    // keep list (newest-first) order
    const order = new Map(commits.value.map((c, i) => [c.sha, i]))
    selected.value = [...selected.value, commit].sort(
      (a, b) => (order.get(a.sha) ?? 0) - (order.get(b.sha) ?? 0)
    )
  }
}

function reviewBundle() {
  if (!selected.value.length) return
  bundleRepo.value = selectedId.value
  rightMode.value = 'bundle'
  openSha.value = null
}

const bundleSpec = computed(() => {
  if (rightMode.value !== 'bundle' || !selected.value.length) return null
  if (bundleMode.value === 'range') {
    return {
      mode: 'range',
      head: selected.value[0].sha,
      base: selected.value[selected.value.length - 1].sha,
      shas: [],
    }
  }
  return { mode: 'set', base: null, head: null, shas: selected.value.map((c) => c.sha) }
})

// ---- single commit detail ----
async function open(commit) {
  rightMode.value = 'commit'
  openSha.value = commit.sha
  detailRepo.value = repoOf(commit)
  detail.value = null
  detailLoading.value = true
  try {
    detail.value = await apiFetch(`/api/git/${detailRepo.value}/commit/${commit.sha}`)
  } catch (err) {
    detail.value = { error: err.message }
  } finally {
    detailLoading.value = false
  }
}

const commitDiffUrl = (path) =>
  `/api/git/${detailRepo.value}/commit/${openSha.value}/diff?path=${encodeURIComponent(path)}`

// ---- deep link (Activity "Commits" jump): ?repo=&bundle=sha,sha&mode=set ----
async function applyDeepLink() {
  const { repo, bundle, mode } = route.query
  if (!repo || !bundle) return false
  selectedId.value = repo
  await load()
  bundleMode.value = mode === 'set' ? 'set' : 'range'
  const shas = String(bundle).split(',').filter(Boolean)
  const details = await Promise.all(
    shas.map((sha) => apiFetch(`/api/git/${repo}/commit/${sha}`).catch(() => null))
  )
  const order = new Map(commits.value.map((c, i) => [c.sha, i]))
  selected.value = details
    .filter(Boolean)
    .sort((a, b) => (order.get(a.sha) ?? 0) - (order.get(b.sha) ?? 0))
  if (selected.value.length) reviewBundle()
  // clear the query so a manual refresh doesn't re-pin the bundle
  router.replace({ query: {} })
  return true
}

onMounted(async () => {
  try {
    const all = await apiFetch('/api/repos')
    repos.value = all.filter((r) => r.exists && r.is_git)
  } catch (err) {
    error.value = err.message === 'backend-unreachable' ? 'Backend not reachable' : err.message
    return
  }
  if (await applyDeepLink()) return
  selectedId.value = ALL
  load()
})
</script>

<template>
  <div class="flex h-full flex-col">
    <header class="mb-4 flex shrink-0 flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-white">History</h1>
        <p class="mt-1 text-sm text-slate-500">
          Commit history, combined diffs, and architectural change across a bundle
        </p>
      </div>
      <select
        v-model="selectedId"
        class="rounded-md border border-edge bg-panel-2 px-3 py-1.5 text-xs text-slate-200 focus:border-coral/60 focus:outline-none"
        @change="onRepoChange"
      >
        <option :value="ALL">All repos - universal history</option>
        <optgroup v-for="[group, items] in groupedRepos" :key="group" :label="group">
          <option v-for="repo in items" :key="repo.id" :value="repo.id">{{ repo.name }}</option>
        </optgroup>
      </select>
    </header>

    <p v-if="error" class="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
      {{ error }}
    </p>

    <SplitPane v-else direction="horizontal" storage-key="history" :initial="0.42" class="min-h-0 flex-1">
      <!-- ============ commit list ============ -->
      <template #first>
        <section class="flex h-full min-h-0 flex-col pr-3">
          <div v-if="loading" class="space-y-2">
            <div v-for="i in 6" :key="i" class="h-12 animate-pulse rounded-md border border-edge bg-panel/50" />
          </div>
          <p v-else-if="!commits.length" class="rounded-lg border border-dashed border-edge bg-panel/40 px-6 py-10 text-center text-sm text-slate-500">
            No commits found
          </p>
          <div v-else class="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
            <p v-if="isUniversal" class="pb-1 text-[11px] text-slate-500">
              Pick a single repo to bundle commits for review.
            </p>
            <CommitRow
              v-for="c in commits"
              :key="(c.repo_id || '') + c.sha"
              :commit="c"
              :show-repo="isUniversal"
              :selected="selectedShas.has(c.sha)"
              :active="openSha === c.sha"
              @toggle="toggle"
              @open="open"
            />
            <button
              v-if="hasMore"
              class="mt-2 w-full rounded-md border border-edge px-3 py-1.5 text-xs text-slate-400 transition-colors hover:border-coral/40 hover:text-coral disabled:opacity-50"
              :disabled="loadingMore"
              @click="loadMore"
            >
              {{ loadingMore ? 'Loading…' : 'Load more' }}
            </button>
          </div>

          <div class="mt-2 shrink-0">
            <BundleBar
              :selected="selected"
              :mode="bundleMode"
              @update:mode="bundleMode = $event"
              @review="reviewBundle"
              @clear="selected = []"
              @remove="toggle"
            />
          </div>
        </section>
      </template>

      <!-- ============ detail / bundle ============ -->
      <template #second>
        <section class="flex h-full min-h-0 flex-col pl-3">
          <!-- bundle review -->
          <BundleReview
            v-if="rightMode === 'bundle' && bundleSpec"
            :repo-id="bundleRepo"
            :mode="bundleSpec.mode"
            :base="bundleSpec.base"
            :head="bundleSpec.head"
            :shas="bundleSpec.shas"
          />

          <!-- single commit detail -->
          <div v-else-if="rightMode === 'commit'" class="flex min-h-0 flex-1 flex-col">
            <p v-if="detailLoading" class="text-xs text-slate-500">Loading commit&hellip;</p>
            <p v-else-if="detail?.error" class="text-xs text-red-400">{{ detail.error }}</p>
            <div v-else-if="detail" class="flex min-h-0 flex-1 flex-col">
              <div class="shrink-0 rounded-lg border border-edge bg-panel p-4">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-xs text-coral">{{ detail.short }}</span>
                  <h2 class="min-w-0 flex-1 truncate text-sm font-semibold text-white" :title="detail.subject">
                    {{ detail.subject }}
                  </h2>
                  <DiffStatBadge :additions="detail.additions" :deletions="detail.deletions" />
                </div>
                <p class="mt-1 text-[11px] text-slate-500">
                  {{ detail.author }} &middot; {{ new Date(detail.date).toLocaleString() }}
                </p>
                <p v-if="detail.body" class="mt-2 whitespace-pre-line border-t border-edge/60 pt-2 text-xs leading-relaxed text-slate-400">
                  {{ detail.body }}
                </p>
              </div>
              <div class="mt-3 min-h-0 flex-1 overflow-auto pr-1">
                <GitFileRow v-for="f in detail.files" :key="f.path" :file="f" :diff-url="commitDiffUrl(f.path)" />
              </div>
            </div>
          </div>

          <!-- empty state -->
          <div v-else class="flex h-full items-center justify-center">
            <p class="max-w-xs text-center text-sm text-slate-500">
              Select a commit to view its changes, or check commits and hit
              <span class="text-coral">Review</span> to bundle them.
            </p>
          </div>
        </section>
      </template>
    </SplitPane>
  </div>
</template>
