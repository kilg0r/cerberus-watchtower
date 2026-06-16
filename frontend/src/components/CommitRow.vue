<script setup>
import DiffStatBadge from './DiffStatBadge.vue'

const props = defineProps({
  commit: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  active: { type: Boolean, default: false }, // currently open in the detail pane
  showRepo: { type: Boolean, default: false }, // universal feed: tag the repo
})
const emit = defineEmits(['toggle', 'open'])

function relative(iso) {
  if (!iso) return '?'
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

const MATCH_STYLES = {
  time: 'border-sky-500/40 text-sky-300',
  files: 'border-emerald-500/40 text-emerald-300',
}
</script>

<template>
  <div
    class="flex items-start gap-2 rounded-md border px-2 py-2 transition-colors"
    :class="[
      active ? 'border-coral/50 bg-panel-2' : 'border-edge bg-panel hover:border-coral/30',
      selected ? 'ring-1 ring-coral/40' : '',
    ]"
  >
    <input
      type="checkbox"
      class="mt-0.5 accent-[#ff6b5e]"
      :checked="selected"
      title="add to bundle"
      @change="emit('toggle', commit)"
    />
    <button class="min-w-0 flex-1 text-left" @click="emit('open', commit)">
      <div class="flex items-center gap-2">
        <span class="shrink-0 font-mono text-[11px] text-coral">{{ commit.short }}</span>
        <span class="min-w-0 flex-1 truncate text-xs text-slate-200" :title="commit.subject">
          {{ commit.subject }}
        </span>
      </div>
      <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-slate-500">
        <span
          v-if="showRepo && commit.repo_name"
          class="rounded-full border border-edge px-1.5 py-0.5 text-slate-400"
        >
          {{ commit.repo_name }}
        </span>
        <span>{{ commit.author }}</span>
        <span>&middot;</span>
        <span>{{ relative(commit.date) }}</span>
        <span>&middot;</span>
        <span>{{ commit.files_changed }} file{{ commit.files_changed === 1 ? '' : 's' }}</span>
        <DiffStatBadge class="!text-[10px]" :additions="commit.additions" :deletions="commit.deletions" />
        <span
          v-for="m in commit.matched_by || []"
          :key="m"
          class="rounded-full border px-1.5 py-0.5"
          :class="MATCH_STYLES[m] || 'border-edge text-slate-400'"
        >
          {{ m }}
        </span>
      </div>
    </button>
  </div>
</template>
