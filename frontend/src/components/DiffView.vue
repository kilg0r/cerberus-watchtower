<script setup>
// The diff reader (History commits and bundles, Review Queue files): line
// numbers, the file's own syntax colors on both sides - what was removed reads
// as code too - the words that changed inside a substituted line, and a
// unified or side-by-side layout (remembered).
import { computed, ref, shallowRef, watch } from 'vue'
import { markWordChanges, parseDiff, segmentsOf, splitRows } from '../utils/diffParse'
import { FOREGROUND, languageOf, tokenize } from '../utils/highlighter'

const props = defineProps({
  diff: { type: String, required: true },
  // the file this diff is for - picks the grammar when the diff has no header
  path: { type: String, default: null },
  // fill the parent's height and scroll inside it (History's file reader),
  // instead of a box capped at 70vh inside a scrolling list
  fill: { type: Boolean, default: false },
})

const MODE_KEY = 'watchtower:diff:mode'
const WRAP_KEY = 'watchtower:diff:wrap'
const mode = ref(localStorage.getItem(MODE_KEY) === 'split' ? 'split' : 'unified')
const wrap = ref(localStorage.getItem(WRAP_KEY) === '1')
watch(mode, (v) => localStorage.setItem(MODE_KEY, v))
watch(wrap, (v) => localStorage.setItem(WRAP_KEY, v ? '1' : '0'))
const wrapped = computed(() => wrap.value || mode.value === 'split')

const blocks = computed(() => {
  const parsed = parseDiff(props.diff)
  for (const block of parsed) if (block.type === 'file') block.hunks.forEach(markWordChanges)
  return parsed
})
const files = computed(() => blocks.value.filter((b) => b.type === 'file'))
const langOf = (file) => languageOf(file.path || props.path)
const LANG_NAMES = { csharp: 'C#', razor: 'Razor', typescript: 'TypeScript', javascript: 'JavaScript', python: 'Python', powershell: 'PowerShell', yaml: 'YAML', json: 'JSON', markdown: 'Markdown', css: 'CSS', html: 'HTML', xml: 'XML', sql: 'SQL', vue: 'Vue', bash: 'Bash' }
const languages = computed(() => [...new Set(files.value.map(langOf).filter(Boolean))].map((l) => LANG_NAMES[l] || l))

// syntax tokens land on the parsed lines (line.tok); `painted` re-renders
const painted = ref(0)
const highlighting = ref(false)
watch(
  blocks,
  async (list) => {
    const jobs = []
    for (const file of list.filter((b) => b.type === 'file')) {
      const lang = langOf(file)
      if (!lang) continue
      for (const hunk of file.hunks) {
        // each side of a hunk is tokenized as the code it was, so strings and
        // comments that span lines color right on both sides
        const oldSide = hunk.lines.filter((l) => l.type === 'ctx' || l.type === 'del')
        const newSide = hunk.lines.filter((l) => l.type === 'ctx' || l.type === 'add')
        jobs.push(
          Promise.all([
            tokenize(oldSide.map((l) => l.text).join('\n'), lang),
            tokenize(newSide.map((l) => l.text).join('\n'), lang),
          ]).then(([oldTok, newTok]) => {
            if (list !== blocks.value) return
            if (oldTok) oldSide.forEach((l, i) => l.type === 'del' && (l.tok = oldTok[i]))
            if (newTok) newSide.forEach((l, i) => (l.tok = newTok[i]))
          })
        )
      }
    }
    if (!jobs.length) return
    highlighting.value = true
    await Promise.all(jobs)
    if (list === blocks.value) {
      highlighting.value = false
      painted.value++
    }
  },
  { immediate: true }
)

// git show's commit header, folded to one line (the commit panel above
// already shows the message): sha, author, subject
function commitSummary(lines) {
  const sha = lines.find((l) => l.startsWith('commit '))?.slice(7, 16)
  const author = lines.find((l) => l.startsWith('Author:'))?.slice(7).replace(/<.*>/, '').trim()
  const dateAt = lines.findIndex((l) => l.startsWith('Date:'))
  const subject = lines.slice(dateAt + 1).find((l) => l.trim())?.trim()
  return sha ? { sha, author, subject } : null
}
const openMeta = ref({})

const MARK = { add: '+', del: '-', ctx: ' ' }
const cell = (line) =>
  line && {
    type: line.type,
    old: line.old,
    new: line.new,
    mark: MARK[line.type],
    segs: segmentsOf(line.text, line.tok, line.emph),
  }

// everything the template draws, rebuilt when tokens arrive or the layout flips
const rows = shallowRef([])
watch(
  [blocks, painted, mode],
  () => {
    const out = []
    // filling a reader, a lone commit header and the file name repeat what the
    // reader already shows above - skip them (several headers, a patch union of
    // commits, still separate the patches)
    const metas = blocks.value.filter((b) => b.type === 'meta').length
    const skipMeta = props.fill && metas === 1
    const showFiles = files.value.length > 1 || (metas > 0 && !skipMeta)
    for (const block of blocks.value) {
      if (block.type === 'meta') {
        if (skipMeta) continue
        out.push({ kind: 'meta', key: `m${out.length}`, lines: block.lines, summary: commitSummary(block.lines) })
        continue
      }
      if (showFiles || block.notes.length) {
        out.push({ kind: 'file', key: `f${block.key}`, path: block.path, notes: block.notes })
      }
      for (const hunk of block.hunks) {
        out.push({ kind: 'hunk', key: `h${hunk.key}`, header: hunk.header.match(/^@@[^@]*@@/)?.[0] || hunk.header, section: hunk.section })
        if (mode.value === 'split') {
          splitRows(hunk).forEach((r, i) =>
            out.push(r.note
              ? { kind: 'note', key: `h${hunk.key}n${i}`, text: r.note.text }
              : { kind: 'split', key: `h${hunk.key}s${i}`, left: cell(r.left), right: cell(r.right) })
          )
        } else {
          hunk.lines.forEach((line, i) =>
            out.push(line.type === 'note'
              ? { kind: 'note', key: `h${hunk.key}n${i}`, text: line.text }
              : { kind: 'line', key: `h${hunk.key}l${i}`, ...cell(line) })
          )
        }
      }
    }
    rows.value = out
  },
  { immediate: true }
)

const segStyle = (seg, type) => ({
  color: seg.color || FOREGROUND,
  fontStyle: seg.italic ? 'italic' : undefined,
  fontWeight: seg.bold ? 600 : undefined,
  backgroundColor: seg.emph ? (type === 'add' ? 'rgba(46, 160, 67, 0.42)' : 'rgba(248, 81, 73, 0.42)') : undefined,
  borderRadius: seg.emph ? '2px' : undefined,
})
const lineBg = { add: 'diff-add', del: 'diff-del', ctx: '' }
</script>

<template>
  <div class="overflow-hidden rounded-md border border-edge bg-[#0b1322]" :class="{ 'flex min-h-0 flex-col': fill }">
    <!-- toolbar -->
    <div class="flex shrink-0 items-center gap-2 border-b border-edge/70 bg-panel-2/60 px-2 py-1 text-[11px]">
      <div class="flex overflow-hidden rounded border border-edge">
        <button
          v-for="m in ['unified', 'split']"
          :key="m"
          class="px-2 py-0.5 capitalize transition-colors"
          :class="mode === m ? 'bg-coral/10 text-coral' : 'text-slate-400 hover:text-slate-200'"
          @click="mode = m"
        >
          {{ m }}
        </button>
      </div>
      <button
        class="rounded border px-2 py-0.5 transition-colors disabled:opacity-40"
        :class="wrapped ? 'border-coral/40 text-coral' : 'border-edge text-slate-400 hover:text-slate-200'"
        :disabled="mode === 'split'"
        :title="mode === 'split' ? 'side by side always wraps' : 'wrap long lines'"
        @click="wrap = !wrap"
      >
        Wrap
      </button>
      <span class="ml-auto text-slate-500">
        <template v-if="highlighting">coloring&hellip; · </template>{{ languages.join(' · ') || 'plain text' }}
      </span>
    </div>

    <div class="diff-body overflow-auto font-mono text-[12px] leading-5" :class="fill ? 'min-h-0 flex-1' : 'max-h-[70vh]'">
      <div :class="wrapped ? 'w-full' : 'w-max min-w-full'">
        <template v-for="row in rows" :key="row.key">
          <!-- commit header (git show / patch union): one line, click for all of it -->
          <div v-if="row.kind === 'meta'" class="border-b border-edge/60 bg-panel/60 text-[11px]">
            <button
              v-if="row.summary"
              class="flex w-full items-center gap-2 px-3 py-1 text-left text-slate-500 hover:text-slate-300"
              :title="openMeta[row.key] ? 'fold the commit header' : 'show the whole commit header'"
              @click="openMeta[row.key] = !openMeta[row.key]"
            >
              <span class="select-none">{{ openMeta[row.key] ? '▾' : '▸' }}</span>
              <span class="text-coral/80">{{ row.summary.sha }}</span>
              <span>{{ row.summary.author }}</span>
              <span class="min-w-0 truncate text-slate-400">{{ row.summary.subject }}</span>
            </button>
            <pre
              v-if="!row.summary || openMeta[row.key]"
              class="whitespace-pre-wrap px-3 pb-2 pt-1 leading-4 text-slate-500"
            >{{ row.lines.join('\n') }}</pre>
          </div>

          <div v-else-if="row.kind === 'file'" class="border-y border-edge/70 bg-panel-2 px-3 py-1 text-[11px]">
            <span class="text-slate-200">{{ row.path }}</span>
            <span v-for="note in row.notes" :key="note" class="ml-3 text-slate-500">{{ note }}</span>
          </div>

          <div v-else-if="row.kind === 'hunk'" class="sticky-hunk flex gap-3 bg-[#13233d] px-3 text-[11px] text-sky-400/80">
            <span class="select-none">{{ row.header }}</span>
            <span class="truncate text-slate-400">{{ row.section }}</span>
          </div>

          <div v-else-if="row.kind === 'note'" class="pl-28 pr-3 text-[11px] italic text-slate-500">{{ row.text }}</div>

          <!-- unified -->
          <div v-else-if="row.kind === 'line'" class="flex" :class="lineBg[row.type]">
            <span class="gutter">{{ row.old }}</span>
            <span class="gutter">{{ row.new }}</span>
            <span class="mark" :class="`mark-${row.type}`">{{ row.mark }}</span>
            <span class="code" :class="wrapped ? 'whitespace-pre-wrap [overflow-wrap:anywhere]' : 'whitespace-pre'"><span
              v-for="(seg, i) in row.segs"
              :key="i"
              :style="segStyle(seg, row.type)"
            >{{ seg.text }}</span><template v-if="!row.segs.length || !row.segs.some((s) => s.text)">&#8203;</template></span>
          </div>

          <!-- side by side -->
          <div v-else-if="row.kind === 'split'" class="grid grid-cols-2">
            <template v-for="(side, s) in [row.left, row.right]" :key="s">
              <div class="flex min-w-0" :class="[side ? lineBg[side.type] : 'diff-empty', s === 0 ? 'border-r border-edge/60' : '']">
                <template v-if="side">
                  <span class="gutter">{{ s === 0 ? side.old : side.new }}</span>
                  <span class="mark" :class="`mark-${side.type}`">{{ side.mark }}</span>
                  <span class="code min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]"><span
                    v-for="(seg, i) in side.segs"
                    :key="i"
                    :style="segStyle(seg, side.type)"
                  >{{ seg.text }}</span>&#8203;</span>
                </template>
              </div>
            </template>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gutter {
  width: 3rem;
  flex-shrink: 0;
  padding-right: 0.5rem;
  text-align: right;
  color: #475569;
  user-select: none;
  font-variant-numeric: tabular-nums;
}
.mark {
  width: 1rem;
  flex-shrink: 0;
  text-align: center;
  user-select: none;
  color: #475569;
}
.mark-add {
  color: #3fb950;
}
.mark-del {
  color: #f85149;
}
.code {
  flex: 1 1 auto;
  padding-right: 1rem;
  tab-size: 4;
}
.diff-add {
  background-color: rgba(46, 160, 67, 0.13);
}
.diff-add .gutter {
  background-color: rgba(46, 160, 67, 0.2);
  color: #7ee2a0;
}
.diff-del {
  background-color: rgba(248, 81, 73, 0.12);
}
.diff-del .gutter {
  background-color: rgba(248, 81, 73, 0.2);
  color: #ffa198;
}
.diff-empty {
  background-image: repeating-linear-gradient(135deg, transparent 0 6px, rgba(148, 163, 184, 0.05) 6px 7px);
}
.sticky-hunk {
  position: sticky;
  left: 0;
}
</style>
