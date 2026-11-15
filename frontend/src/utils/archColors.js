// Node colors for the architecture graphs (2D Cytoscape and 3D), shared so
// a project keeps its color whichever view you're in.

export const FOLDER_COLORS = {
  presentation: '#38bdf8',
  core: '#34d399',
  infrastructure: '#fbbf24',
  tests: '#64748b',
}
// colors handed out to solution folders beyond the canonical four, assigned
// in sorted order so a given folder name keeps its color across rescans
export const EXTRA_FOLDER_PALETTE = ['#f472b6', '#22d3ee', '#a3e635', '#fb923c', '#e879f9', '#2dd4bf']
export const KIND_COLORS = {
  view: '#38bdf8',
  component: '#a78bfa',
  store: '#34d399',
  module: '#38bdf8',
  package: '#34d399',
}
export const DEFAULT_COLOR = '#a78bfa'

export const DEFAULT_LEGEND = [
  { label: 'presentation', color: '#38bdf8' },
  { label: 'core', color: '#34d399' },
  { label: 'infrastructure', color: '#fbbf24' },
  { label: 'tests', color: '#64748b' },
  { label: 'other', color: '#a78bfa' },
]

export function folderColorMap(nodes) {
  const map = { ...FOLDER_COLORS }
  const extras = [...new Set(nodes.map((n) => n.folder).filter((f) => f && !(f in map)))].sort()
  extras.forEach((f, i) => {
    map[f] = EXTRA_FOLDER_PALETTE[i % EXTRA_FOLDER_PALETTE.length]
  })
  return map
}

export function nodeColor(node, folderColors) {
  if (node.folder) return folderColors[node.folder]
  return KIND_COLORS[node.kind] || DEFAULT_COLOR
}

// legend derived from the folders actually present; 'other' only when some
// node has no solution folder at all
export function legendFor(nodes, folderColors, override = null) {
  if (override) return override
  const present = new Set(nodes.map((n) => n.folder).filter(Boolean))
  if (!present.size) return DEFAULT_LEGEND
  const items = Object.entries(folderColors)
    .filter(([label]) => present.has(label))
    .map(([label, color]) => ({ label, color }))
  if (nodes.some((n) => !n.folder)) items.push({ label: 'other', color: DEFAULT_COLOR })
  return items
}
