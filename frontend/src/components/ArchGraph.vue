<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import dagre from 'cytoscape-dagre'

cytoscape.use(dagre)

const props = defineProps({
  nodes: { type: Array, required: true },
  edges: { type: Array, required: true },
  // 'overview' = dense whole-solution map; 'focus' = roomy, readable subgraph
  mode: { type: String, default: 'overview' },
  focusId: { type: String, default: null },
  heightClass: { type: String, default: 'h-[480px]' },
  // [{label, color}] - overrides the default .NET solution-folder legend
  legend: { type: Array, default: null },
})
const emit = defineEmits(['select', 'focus'])

const container = ref(null)
let cy = null
let resizeObserver = null

const FOLDER_COLORS = {
  presentation: '#38bdf8',
  core: '#34d399',
  infrastructure: '#fbbf24',
  tests: '#64748b',
}
const KIND_COLORS = {
  view: '#38bdf8',
  component: '#a78bfa',
  store: '#34d399',
  module: '#38bdf8',
  package: '#34d399',
}
const DEFAULT_COLOR = '#a78bfa'

const DEFAULT_LEGEND = [
  { label: 'presentation', color: '#38bdf8' },
  { label: 'core', color: '#34d399' },
  { label: 'infrastructure', color: '#fbbf24' },
  { label: 'tests', color: '#64748b' },
  { label: 'other', color: '#a78bfa' },
]

function nodeColor(n) {
  return FOLDER_COLORS[n.folder] || KIND_COLORS[n.kind] || DEFAULT_COLOR
}

const MODES = {
  overview: { font: 9, node: 18, nodeSep: 18, rankSep: 90 },
  focus: { font: 12, node: 30, nodeSep: 34, rankSep: 150 },
}

function build() {
  if (cy) cy.destroy()
  const m = MODES[props.mode] || MODES.overview
  cy = cytoscape({
    container: container.value,
    elements: [
      ...props.nodes.map((n) => ({
        data: { ...n, color: nodeColor(n) },
        classes: n.id === props.focusId ? 'focused' : '',
      })),
      ...props.edges.map((e, i) => ({
        data: { id: `e${i}`, source: e.source, target: e.target },
      })),
    ],
    style: [
      {
        selector: 'node',
        style: {
          label: 'data(label)',
          color: '#e2e8f0',
          'font-size': `${m.font}px`,
          'font-family': 'Inter, sans-serif',
          'text-valign': 'bottom',
          'text-margin-y': 5,
          'background-color': 'data(color)',
          'background-opacity': 0.85,
          width: m.node,
          height: m.node,
          shape: 'round-rectangle',
        },
      },
      {
        selector: 'node.focused',
        style: {
          'border-width': 3,
          'border-color': '#ff6b5e',
          'background-opacity': 1,
          'font-weight': 'bold',
          color: '#ffffff',
          width: m.node * 1.4,
          height: m.node * 1.4,
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-width': 2,
          'border-color': '#ff6b5e',
          'background-opacity': 1,
        },
      },
      {
        selector: 'edge',
        style: {
          width: props.mode === 'focus' ? 1.5 : 1,
          'line-color': '#1e3050',
          'target-arrow-color': '#1e3050',
          'target-arrow-shape': 'triangle',
          'arrow-scale': props.mode === 'focus' ? 1 : 0.7,
          'curve-style': 'bezier',
        },
      },
      {
        selector: 'edge.highlighted',
        style: { 'line-color': '#ff6b5e', 'target-arrow-color': '#ff6b5e', width: 2 },
      },
    ],
    layout: { name: 'dagre', rankDir: 'LR', nodeSep: m.nodeSep, rankSep: m.rankSep },
    wheelSensitivity: 0.2,
  })

  if (props.focusId) {
    const focused = cy.getElementById(props.focusId)
    if (focused.nonempty()) focused.connectedEdges().addClass('highlighted')
  }

  cy.on('select', 'node', (event) => {
    const node = event.target
    cy.edges().removeClass('highlighted')
    node.connectedEdges().addClass('highlighted')
    emit('select', node.data())
  })
  cy.on('unselect', 'node', () => {
    cy.edges().removeClass('highlighted')
    emit('select', null)
  })
  cy.on('dbltap', 'node', (event) => emit('focus', event.target.id()))
}

onMounted(() => {
  build()
  // keep the canvas matched to its flex-sized container (viewport resizes)
  let pending = null
  resizeObserver = new ResizeObserver(() => {
    clearTimeout(pending)
    pending = setTimeout(() => {
      if (cy) {
        cy.resize()
        cy.fit(undefined, 30)
      }
    }, 150)
  })
  resizeObserver.observe(container.value)
})
watch(() => [props.nodes, props.edges, props.mode, props.focusId], build, { deep: false })
onBeforeUnmount(() => {
  resizeObserver && resizeObserver.disconnect()
  cy && cy.destroy()
})
</script>

<template>
  <div class="relative h-full">
    <div ref="container" class="w-full rounded-lg border border-edge bg-navy/60" :class="heightClass" />
    <div class="absolute bottom-3 left-3 flex gap-3 rounded-md border border-edge bg-panel/90 px-3 py-1.5 text-[10px] text-slate-400">
      <span v-for="item in legend || DEFAULT_LEGEND" :key="item.label">
        <span class="mr-1 inline-block h-2 w-2 rounded-sm" :style="{ backgroundColor: item.color }" />{{ item.label }}
      </span>
      <span class="text-slate-500">double-click a node to focus</span>
    </div>
  </div>
</template>
