// Unified diff -> structure the diff reader can render: commit headers (git
// show / patch unions carry them), files, hunks and numbered lines, plus the
// words that changed inside each substituted line.

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$/

function stripPrefix(path) {
  if (!path || path === '/dev/null') return null
  return path.replace(/^[ab]\//, '').replace(/\t.*$/, '')
}

/**
 * @returns {{ type: 'meta', lines: string[] } | { type: 'file', path, oldPath, newPath, notes: string[], hunks: object[] }}[]
 */
export function parseDiff(text) {
  const blocks = []
  let meta = null
  let file = null
  let hunk = null
  let oldLeft = 0
  let newLeft = 0
  let id = 0

  const startMeta = () => {
    meta = { type: 'meta', lines: [] }
    blocks.push(meta)
    file = null
    hunk = null
  }
  const startFile = (path) => {
    file = { type: 'file', key: id++, path, oldPath: path, newPath: path, notes: [], hunks: [] }
    blocks.push(file)
    meta = null
    hunk = null
  }

  for (const line of text.replace(/\n$/, '').split('\n')) {
    // inside a hunk: its own line counts say where it ends
    if (hunk && (oldLeft > 0 || newLeft > 0)) {
      const mark = line[0]
      if (mark === '+' && newLeft > 0) {
        hunk.lines.push({ type: 'add', text: line.slice(1), new: hunk.newNext++ })
        newLeft--
        continue
      }
      if (mark === '-' && oldLeft > 0) {
        hunk.lines.push({ type: 'del', text: line.slice(1), old: hunk.oldNext++ })
        oldLeft--
        continue
      }
      if ((mark === ' ' || line === '') && oldLeft > 0 && newLeft > 0) {
        hunk.lines.push({ type: 'ctx', text: line.slice(1), old: hunk.oldNext++, new: hunk.newNext++ })
        oldLeft--
        newLeft--
        continue
      }
    }
    if (hunk && line.startsWith('\\')) {
      hunk.lines.push({ type: 'note', text: line.slice(1).trim() })
      continue
    }

    if (/^commit [0-9a-f]{7,40}\b/.test(line)) {
      // the next commit of a patch union (or git show's own header)
      startMeta()
      meta.lines.push(line)
      continue
    }
    if (line.startsWith('diff --git ')) {
      const m = line.match(/ b\/(.+)$/)
      startFile(m ? m[1] : line.slice(11))
      continue
    }
    const h = line.match(HUNK_RE)
    if (h) {
      if (!file) startFile(null)
      const oldStart = Number(h[1])
      const newStart = Number(h[3])
      oldLeft = h[2] === undefined ? 1 : Number(h[2])
      newLeft = h[4] === undefined ? 1 : Number(h[4])
      hunk = { key: id++, header: line, section: h[5] || '', oldStart, newStart, oldNext: oldStart, newNext: newStart, lines: [] }
      file.hunks.push(hunk)
      continue
    }
    if (file && !file.hunks.length) {
      // the file's own header: paths, modes, renames, binary
      if (line.startsWith('--- ')) file.oldPath = stripPrefix(line.slice(4))
      else if (line.startsWith('+++ ')) {
        file.newPath = stripPrefix(line.slice(4))
        file.path = file.newPath || file.oldPath || file.path
      } else if (/^(new file|deleted file|rename from|rename to|similarity index|Binary files|old mode|new mode|copy from|copy to)/.test(line)) {
        file.notes.push(line)
      }
      continue
    }
    if (line.startsWith('--- ') || line.startsWith('+++ ')) {
      // a hunk-less patch (no diff --git line)
      if (!file || file.hunks.length) startFile(null)
      if (line.startsWith('+++ ')) file.path = file.newPath = stripPrefix(line.slice(4))
      else file.oldPath = stripPrefix(line.slice(4))
      continue
    }
    if (!meta) startMeta()
    meta.lines.push(line)
  }
  for (const block of blocks) {
    if (block.type === 'file') block.path ||= block.newPath || block.oldPath
  }
  // a lone empty trailing meta block is noise
  return blocks.filter((b) => b.type !== 'meta' || b.lines.some((l) => l.trim()))
}

// ── words that changed inside a substituted line ─────────────────────────────

const WORD_RE = /\w+|\s+|[^\w\s]/g
const MAX_CELLS = 60000 // LCS table budget per line pair

function changedRanges(a, b) {
  const ta = a.match(WORD_RE) || []
  const tb = b.match(WORD_RE) || []
  if (!ta.length || !tb.length || ta.length * tb.length > MAX_CELLS) return null
  const n = ta.length
  const m = tb.length
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = ta[i] === tb[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const keepA = new Array(n).fill(false)
  const keepB = new Array(m).fill(false)
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (ta[i] === tb[j]) {
      keepA[i++] = true
      keepB[j++] = true
    } else if (dp[i + 1][j] >= dp[i][j + 1]) i++
    else j++
  }
  const ranges = (tokens, keep) => {
    const out = []
    let at = 0
    tokens.forEach((t, k) => {
      if (!keep[k] && t.trim()) {
        const last = out.at(-1)
        if (last && last[1] >= at - 1) last[1] = at + t.length
        else out.push([at, at + t.length])
      }
      at += t.length
    })
    return out
  }
  return { a: ranges(ta, keepA), b: ranges(tb, keepB) }
}

const covered = (ranges, len) => ranges.reduce((n, [s, e]) => n + (e - s), 0) / Math.max(1, len)

/**
 * Pair each run of removed lines with the added lines right after it and mark
 * the words that differ (`line.emph = [[start, end], ...]`). A line that
 * changed almost entirely gets no marks - highlighting all of it says nothing.
 */
export function markWordChanges(hunk) {
  const lines = hunk.lines
  let k = 0
  while (k < lines.length) {
    if (lines[k].type !== 'del') {
      k++
      continue
    }
    const dels = []
    while (k < lines.length && lines[k].type === 'del') dels.push(lines[k++])
    const adds = []
    while (k < lines.length && lines[k].type === 'add') adds.push(lines[k++])
    for (let p = 0; p < Math.min(dels.length, adds.length); p++) {
      const d = dels[p]
      const a = adds[p]
      if (d.text.length > 600 || a.text.length > 600) continue
      const r = changedRanges(d.text, a.text)
      if (!r) continue
      if (covered(r.a, d.text.length) > 0.6 || covered(r.b, a.text.length) > 0.6) continue
      d.emph = r.a
      a.emph = r.b
      d.pair = a
      a.pair = d
    }
  }
}

/**
 * Side-by-side rows: context on both sides, each removed run beside the added
 * run that replaced it.
 */
export function splitRows(hunk) {
  const rows = []
  const lines = hunk.lines
  let k = 0
  while (k < lines.length) {
    const line = lines[k]
    if (line.type === 'ctx') {
      rows.push({ left: line, right: line })
      k++
    } else if (line.type === 'note') {
      rows.push({ note: line })
      k++
    } else {
      const dels = []
      while (k < lines.length && lines[k].type === 'del') dels.push(lines[k++])
      const adds = []
      while (k < lines.length && lines[k].type === 'add') adds.push(lines[k++])
      for (let p = 0; p < Math.max(dels.length, adds.length); p++) rows.push({ left: dels[p] || null, right: adds[p] || null })
    }
  }
  return rows
}

/**
 * Syntax tokens (or plain text) cut at the changed-word marks:
 * [{ text, color, italic, bold, emph }]
 */
export function segmentsOf(text, tokens, emph) {
  const parts = tokens?.length
    ? tokens.map((t) => ({ text: t.content, color: t.color, fontStyle: t.fontStyle }))
    : [{ text, color: null, fontStyle: 0 }]
  if (!emph?.length) return parts.map(style)
  const out = []
  let at = 0
  for (const part of parts) {
    let start = 0
    const end = part.text.length
    while (start < end) {
      const abs = at + start
      const inside = emph.find(([s, e]) => abs >= s && abs < e)
      let stop = end
      if (inside) stop = Math.min(end, inside[1] - at)
      else {
        const next = emph.find(([s]) => s > abs)
        if (next) stop = Math.min(end, next[0] - at)
      }
      out.push(style({ ...part, text: part.text.slice(start, stop), emph: !!inside }))
      start = stop
    }
    at += end
  }
  return out
}

function style(part) {
  return {
    text: part.text,
    color: part.color,
    italic: !!(part.fontStyle & 1),
    bold: !!(part.fontStyle & 2),
    emph: !!part.emph,
  }
}
