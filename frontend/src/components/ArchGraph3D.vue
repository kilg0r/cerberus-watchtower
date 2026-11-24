<script setup>
// The architecture graph in 3D: same props and events as ArchGraph (the 2D
// Cytoscape view), drawn by utils/archScene.js (three.js, loaded on first
// use). This component owns everything you read: labels, readouts, legend,
// controls. Dependency direction: a packet runs from each node to what it
// uses; selecting a node lights what it uses (coral) and what uses it (blue).
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { folderColorMap, legendFor, nodeColor } from '../utils/archColors'

const props = defineProps({
  nodes: { type: Array, required: true },
  edges: { type: Array, required: true },
  mode: { type: String, default: 'overview' },
  focusId: { type: String, default: null },
  heightClass: { type: String, default: 'h-[480px]' },
  legend: { type: Array, default: null },
  // legend click: 'highlight' dims everything else; 'isolate' asks the parent
  // to redraw the map around that folder (hover still previews it)
  legendAction: { type: String, default: 'highlight' },
  isolated: { type: String, default: null },
  // a line under the HUD title naming what the map is narrowed to
  caption: { type: String, default: null },
  // folder -> color for the whole solution, so a folder keeps its color when
  // the map shows only part of it
  palette: { type: Object, default: null },
  // fly in to the focus node on build (project focus); a narrowed map that's
  // read as a whole (a traced flow) starts fitted instead
  frameFocus: { type: Boolean, default: true },
})
const emit = defineEmits(['select', 'focus', 'unsupported', 'isolate'])

const USES_COLOR = '#ff6b5e'
const USED_BY_COLOR = '#5cb8ff'
const ALL_LABELS_UP_TO = 80 // beyond this only the most connected get labels
const TOP_LABELS = 36
const LABEL_H = 18

const stage = ref(null)
const canvas = ref(null)
const fpsEl = ref(null)
const labelEls = {}
const layerEls = {}
const bundleEls = {}
const tagWidth = {}
const tagCount = {}
const ready = ref(false)
const failed = ref(false)
// a disposed renderer force-loses its canvas's WebGL context, so every
// build gets a fresh <canvas> element
const canvasKey = ref(0)
const hoveredId = ref(null)
const selectedId = ref(null)
const group = ref(null)
const layout = shallowRef(null)
const stored = (key, fallback) => {
  const value = localStorage.getItem(`watchtower:arch3d:${key}`)
  return value === null ? fallback : value === '1'
}
const layered = ref(stored('layered', true))
const autoRotate = ref(stored('rotate', true))
watch(layered, (v) => localStorage.setItem('watchtower:arch3d:layered', v ? '1' : '0'))
watch(autoRotate, (v) => {
  localStorage.setItem('watchtower:arch3d:rotate', v ? '1' : '0')
  engine?.setAutoRotate(v)
})

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

const folderColors = computed(() => props.palette || folderColorMap(props.nodes))
const legendItems = computed(() => legendFor(props.nodes, folderColors.value, props.legend))
const groupOf = (n) => n.folder || (props.legend ? n.kind : 'other')

const degree = computed(() => {
  const d = {}
  for (const e of props.edges) {
    d[e.source] = (d[e.source] || 0) + 1
    d[e.target] = (d[e.target] || 0) + 1
  }
  return d
})
const byId = computed(() => new Map(props.nodes.map((n) => [n.id, n])))
const colorOf = (n) => nodeColor(n, folderColors.value)
const PRIMARY_ROLES = new Set([undefined, null, 'scope', 'member', 'flow'])
const clustered = computed(() => props.nodes.some((n) => n.bundle))
// the bundle holding most of the subject (isolated folder, scope) takes the middle
const hubKey = computed(() => {
  if (!clustered.value) return null
  const counts = new Map()
  for (const n of props.nodes) if (PRIMARY_ROLES.has(n.role)) counts.set(n.bundle, (counts.get(n.bundle) || 0) + 1)
  return [...counts].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null
})
// bundle name tags: the color of the bundle's first member
const bundleTags = computed(() =>
  (layout.value?.bundles || []).map((b) => {
    const first = props.nodes.find((n) => n.bundle === b.key)
    return { ...b, color: first ? colorOf(first) : '#94a3b8' }
  })
)

// tour order for the keyboard: top layer first, then by name
const tour = computed(() => {
  const ranks = layout.value?.ranks
  return props.nodes
    .slice()
    .sort((a, b) => (ranks?.get(a.id) ?? 0) - (ranks?.get(b.id) ?? 0) || a.label.localeCompare(b.label))
})

const neighbours = (id) => {
  const out = new Set()
  if (!id) return out
  for (const e of props.edges) {
    if (e.source === id) out.add(e.target)
    if (e.target === id) out.add(e.source)
  }
  return out
}

// which labels exist at all: every one on small graphs, otherwise the most
// connected plus whatever the selection/hover/focus makes relevant
const labelIds = computed(() => {
  const ids = new Set()
  if (props.nodes.length <= ALL_LABELS_UP_TO || props.mode === 'focus') {
    props.nodes.forEach((n) => ids.add(n.id))
  } else {
    props.nodes
      .slice()
      .sort((a, b) => (degree.value[b.id] || 0) - (degree.value[a.id] || 0))
      .slice(0, TOP_LABELS)
      .forEach((n) => ids.add(n.id))
  }
  for (const id of [selectedId.value, hoveredId.value, props.focusId]) {
    if (!id) continue
    ids.add(id)
    neighbours(id).forEach((x) => ids.add(x))
  }
  if (group.value) props.nodes.filter((n) => groupOf(n) === group.value).forEach((n) => ids.add(n.id))
  return [...ids].filter((id) => byId.value.has(id))
})
const labelled = computed(() => labelIds.value.map((id) => byId.value.get(id)))
// who wins when labels collide: the selection, its neighbours, then the
// most connected
const labelPriority = computed(() => {
  const prio = {}
  const near = new Set([...neighbours(selectedId.value), ...neighbours(hoveredId.value)])
  for (const id of labelIds.value) {
    const emphasis = id === selectedId.value || id === hoveredId.value || id === props.focusId
    const primary = clustered.value && PRIMARY_ROLES.has(byId.value.get(id)?.role)
    prio[id] = emphasis ? 0 : near.has(id) ? 1 : primary ? 1.5 : 2 - Math.min(0.99, (degree.value[id] || 0) / 1000)
  }
  return prio
})
const labelWidth = {}
watch(labelled, () => {
  for (const key of Object.keys(labelWidth)) delete labelWidth[key]
})
const subject = computed(() => selectedId.value || hoveredId.value)
const relation = computed(() => {
  const id = subject.value
  if (!id) return {}
  const map = { [id]: 'self' }
  for (const e of props.edges) {
    if (e.source === id && e.target !== id) map[e.target] = 'uses'
    if (e.target === id && e.source !== id) map[e.source] = map[e.source] === 'uses' ? 'both' : 'usedBy'
  }
  return map
})
const counts = computed(() => {
  const id = subject.value
  if (!id) return null
  let uses = 0
  let usedBy = 0
  for (const e of props.edges) {
    if (e.source === id) uses++
    if (e.target === id) usedBy++
  }
  return { uses, usedBy, label: byId.value.get(id)?.label }
})

// ── per-frame DOM updates (no reactivity: 60+ times a second) ────────────────
let frameCount = 0
const placed = []
function onFrame(f) {
  const prio = labelPriority.value
  const items = f.nodes.filter((n) => labelEls[n.id])
  items.sort((a, b) => (prio[a.id] ?? 3) - (prio[b.id] ?? 3) || a.depth - b.depth)
  placed.length = 0
  for (const n of items) {
    const el = labelEls[n.id]
    const emphasis = n.id === selectedId.value || n.id === hoveredId.value || n.id === props.focusId
    let opacity = n.visible ? (1 - n.depth * 0.7) * (0.25 + 0.75 * n.alpha) : 0
    if (emphasis && n.visible) opacity = 1
    if (opacity > 0.04) {
      // a label that would overlap one already placed stays hidden this frame
      const w = (labelWidth[n.id] ||= el.firstElementChild?.offsetWidth || 80)
      const box = { l: n.x + 8, r: n.x + 12 + w, t: n.y - LABEL_H / 2, b: n.y + LABEL_H / 2 }
      const hit = placed.some((p) => box.l < p.r && box.r > p.l && box.t < p.b && box.b > p.t)
      if (hit && !emphasis) opacity = 0
      else placed.push(box)
    }
    el.style.transform = `translate3d(${n.x.toFixed(1)}px, ${n.y.toFixed(1)}px, 0)`
    el.style.opacity = opacity.toFixed(3)
    el.style.zIndex = String(Math.round((1 - n.depth) * 100) + (emphasis ? 200 : 0))
    el.style.pointerEvents = opacity > 0.3 ? 'auto' : 'none'
  }
  for (const l of f.layers) {
    const el = layerEls[l.rank]
    if (!el) continue
    el.style.transform = `translate3d(${l.x.toFixed(1)}px, ${l.y.toFixed(1)}px, 0)`
    el.style.opacity = l.visible ? '1' : '0'
  }
  // bundle tags: the hub and the biggest bundles win when two would overlap
  const tags = []
  for (const b of f.bundles || []) {
    const el = bundleEls[b.key]
    if (!el) continue
    el.style.transform = `translate3d(${b.x.toFixed(1)}px, ${b.y.toFixed(1)}px, 0)`
    tags.push({ b, el, rank: b.key === hubKey.value ? -1 : -(tagCount[b.key] || 0) })
  }
  tags.sort((a, b) => a.rank - b.rank)
  const taken = []
  for (const { b, el } of tags) {
    const w = (tagWidth[b.key] ||= el.offsetWidth || 90)
    const box = { l: b.x - w / 2, r: b.x + w / 2, t: b.y - 22, b: b.y }
    const hit = taken.some((p) => box.l < p.r && box.r > p.l && box.t < p.b && box.b > p.t)
    el.style.opacity = b.visible && !hit ? '1' : '0'
    if (b.visible && !hit) taken.push(box)
  }
  if (frameCount++ % 15 === 0 && fpsEl.value) fpsEl.value.textContent = String(f.fps || '--').padStart(3, ' ')
}

// ── engine lifecycle ─────────────────────────────────────────────────────────
let mod = null
let engine = null
let unmounted = false

async function build() {
  const hadEngine = !!engine
  engine?.dispose()
  engine = null
  ready.value = false
  if (hadEngine) {
    canvasKey.value++
    await nextTick()
  }
  hoveredId.value = null
  selectedId.value = null
  if (!mod) {
    try {
      mod = await import('../utils/archScene')
    } catch (err) {
      console.error('[arch3d] failed to load', err)
      failed.value = true
      emit('unsupported')
      return
    }
  }
  if (unmounted || !canvas.value || !props.nodes.length) return
  const sceneNodes = props.nodes.map((n) => ({
    id: n.id,
    color: colorOf(n),
    group: groupOf(n),
    bundle: n.bundle,
    emphasis: PRIMARY_ROLES.has(n.role) ? 'primary' : 'secondary',
    degree: degree.value[n.id] || 0,
  }))
  layout.value = mod.layoutGraph(sceneNodes, props.edges, { layered: layered.value, cluster: clustered.value, hub: hubKey.value })
  for (const key of Object.keys(tagWidth)) delete tagWidth[key]
  for (const key of Object.keys(tagCount)) delete tagCount[key]
  for (const b of layout.value.bundles || []) tagCount[b.key] = b.count
  try {
    engine = mod.createArchScene({
      canvas: canvas.value,
      el: stage.value,
      nodes: sceneNodes,
      edges: props.edges,
      layout: layout.value,
      focusId: props.focusId,
      frameFocus: props.frameFocus,
      reducedMotion,
      onFrame,
      onHover: (id) => (hoveredId.value = id),
      onSelect: select,
      onFocus: (id) => emit('focus', id),
    })
  } catch (err) {
    console.warn('[arch3d] WebGL unavailable', err)
    failed.value = true
    emit('unsupported')
    return
  }
  engine.setAutoRotate(autoRotate.value)
  if (group.value) engine.setGroup(group.value)
  ready.value = true
}

function select(id) {
  selectedId.value = id
  engine?.setSelected(id)
  emit('select', id ? byId.value.get(id) : null)
}

function resetView() {
  engine?.resetView()
}

function toggleGroup(label) {
  group.value = group.value === label ? null : label
  engine?.setGroup(group.value)
}

function onLegendClick(label) {
  if (props.legendAction === 'isolate') emit('isolate', label)
  else toggleGroup(label)
}
// isolate mode: hovering a folder previews it before the click redraws
function previewGroup(label) {
  if (props.legendAction === 'isolate') engine?.setGroup(label || group.value)
}
const legendActive = (label) => group.value === label || props.isolated === label

function step(delta) {
  const list = tour.value
  if (!list.length) return
  const i = list.findIndex((n) => n.id === selectedId.value)
  const next = i < 0 ? (delta > 0 ? 0 : list.length - 1) : (i + delta + list.length) % list.length
  select(list[next].id)
}

function onKeydown(e) {
  if (e.target !== stage.value) return
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
    e.preventDefault()
    step(1)
  } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
    e.preventDefault()
    step(-1)
  } else if (e.key === 'Enter' && selectedId.value) {
    emit('focus', selectedId.value)
  }
}

onMounted(build)
onBeforeUnmount(() => {
  unmounted = true
  engine?.dispose()
  engine = null
})
watch(() => [props.nodes, props.edges, props.focusId, layered.value], build)

const relationStyle = (id) => {
  const r = relation.value[id]
  if (r === 'uses') return { '--rel': USES_COLOR }
  if (r === 'usedBy' || r === 'both') return { '--rel': USED_BY_COLOR }
  return null
}
</script>

<template>
  <div class="relative h-full">
    <div
      ref="stage"
      class="arch-stage relative w-full select-none overflow-hidden rounded-lg border border-edge outline-none focus-visible:ring-2 focus-visible:ring-coral/50"
      :class="heightClass"
      tabindex="0"
      role="group"
      aria-roledescription="interactive 3D dependency graph"
      :aria-label="`Dependency graph: ${nodes.length} nodes, ${edges.length} links. Arrow keys move between nodes, Enter focuses one.`"
      @keydown="onKeydown"
    >
      <div class="arch-grid pointer-events-none absolute inset-0" aria-hidden="true" />
      <canvas
        :key="canvasKey"
        ref="canvas"
        class="absolute inset-0 h-full w-full transition-opacity duration-700"
        :class="ready ? 'opacity-100' : 'opacity-0'"
        aria-hidden="true"
      />

      <!-- layer markers -->
      <div class="pointer-events-none absolute inset-0" aria-hidden="true">
        <span
          v-for="layer in layout?.layers || []"
          :key="layer.rank"
          :ref="(el) => (layerEls[layer.rank] = el)"
          class="arch-layer"
        >
          L{{ layer.rank }} <span class="text-white/25">· {{ layer.count }}</span>
        </span>
      </div>

      <!-- bundle tags (isolated folders, narrowed scopes): positioned every frame -->
      <div class="pointer-events-none absolute inset-0" aria-hidden="true">
        <span
          v-for="b in bundleTags"
          :key="b.key"
          :ref="(el) => (bundleEls[b.key] = el)"
          class="arch-bundle"
          :style="{ '--node': b.color }"
        >
          <span class="arch-bundle-bar" />{{ b.key }} <span class="text-white/35">· {{ b.count }}</span>
        </span>
      </div>

      <!-- node labels: positioned every frame by onFrame -->
      <div class="pointer-events-none absolute inset-0">
        <button
          v-for="node in labelled"
          :key="node.id"
          :ref="(el) => (labelEls[node.id] = el)"
          type="button"
          tabindex="-1"
          data-node-label
          :data-id="node.id"
          class="arch-label"
          :class="{
            'is-selected': node.id === selectedId,
            'is-hovered': node.id === hoveredId,
            'is-focus': node.id === focusId,
            'has-rel': !!relationStyle(node.id),
          }"
          :style="{ '--node': colorOf(node), ...relationStyle(node.id) }"
          :title="node.id"
        >
          <span class="arch-chip"><span class="arch-dot" />{{ node.label }}</span>
        </button>
      </div>

      <!-- readouts -->
      <div class="arch-hud pointer-events-none absolute left-4 top-3" aria-hidden="true">
        <p class="text-white/75">WATCHTOWER <span class="text-white/30">//</span> ARCH.3D</p>
        <p class="mt-0.5 text-white/40">
          {{ nodes.length }} NODES · {{ edges.length }} LINKS<template v-if="layout?.layers?.length"> · {{ layout.layers.length }} LAYERS</template>
        </p>
        <p v-if="caption" class="mt-0.5 max-w-[40vw] truncate text-coral/80">{{ caption }}</p>
      </div>
      <div class="arch-hud pointer-events-none absolute right-4 top-3 text-right" aria-hidden="true">
        <p class="text-white/40">VIEW <span class="text-white/75">{{ layered ? 'LAYERED' : 'FREE' }}</span></p>
        <p class="mt-0.5 text-white/40">FPS <span ref="fpsEl" class="text-white/75">--</span></p>
      </div>

      <!-- controls -->
      <div class="absolute left-3 flex items-center gap-1.5" :class="caption ? 'top-[3.75rem]' : 'top-12'" data-arch-ui @pointerdown.stop>
        <div class="flex overflow-hidden rounded-md border border-white/10 bg-[#070d1a]/70 backdrop-blur">
          <button
            type="button"
            class="arch-btn"
            :class="{ 'is-active': layered }"
            title="Dependency layers top to bottom: entry points on top"
            @click="layered = true"
          >
            Layered
          </button>
          <button
            type="button"
            class="arch-btn"
            :class="{ 'is-active': !layered }"
            title="Free 3D force layout"
            @click="layered = false"
          >
            Free
          </button>
        </div>
        <button
          type="button"
          class="arch-btn rounded-md border border-white/10 bg-[#070d1a]/70 backdrop-blur"
          :class="{ 'is-active': autoRotate }"
          title="Slow spin while idle"
          @click="autoRotate = !autoRotate"
        >
          ⟳ Spin
        </button>
        <button
          type="button"
          class="arch-btn rounded-md border border-white/10 bg-[#070d1a]/70 backdrop-blur"
          title="Back to the whole graph"
          @click="resetView"
        >
          Reset view
        </button>
      </div>

      <!-- what the selection touches -->
      <div
        v-if="counts"
        class="arch-hud pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-white/10 bg-[#070d1a]/70 px-3 py-1.5 text-center backdrop-blur"
        aria-live="polite"
      >
        <p class="truncate text-white/85 normal-case tracking-normal">{{ counts.label }}</p>
        <p class="mt-0.5">
          <span :style="{ color: USES_COLOR }">● uses {{ counts.uses }}</span>
          <span class="text-white/30"> · </span>
          <span :style="{ color: USED_BY_COLOR }">● used by {{ counts.usedBy }}</span>
        </p>
      </div>

      <!-- legend: click a group to isolate it. UI blocks stop pointerdown: OrbitControls
           captures the pointer on the stage, which would retarget the click away.
           On a phone it is one row you swipe sideways (11 folders took a third of the map) -->
      <div class="absolute bottom-3 left-3 flex max-w-[60%] flex-wrap gap-1.5 max-md:right-3 max-md:max-w-none max-md:touch-pan-x max-md:flex-nowrap max-md:overflow-x-auto max-md:pb-1 max-md:[&>*]:shrink-0" data-arch-ui @pointerdown.stop>
        <button
          v-for="item in legendItems"
          :key="item.label"
          type="button"
          class="arch-key"
          :class="{ 'is-active': legendActive(item.label) }"
          :style="{ '--node': item.color }"
          :title="legendActive(item.label)
            ? 'show everything'
            : legendAction === 'isolate'
              ? `isolate ${item.label} and the libraries it links to`
              : `highlight ${item.label}`"
          @click="onLegendClick(item.label)"
          @pointerenter="previewGroup(item.label)"
          @pointerleave="previewGroup(null)"
        >
          <span class="arch-dot" />{{ item.label }}
        </button>
      </div>

      <p class="arch-hud pointer-events-none absolute bottom-4 right-4 hidden text-right text-white/30 2xl:block" aria-hidden="true">
        DRAG ORBIT · RIGHT-DRAG PAN · SCROLL ZOOM · CLICK SELECT · DOUBLE-CLICK FOCUS
      </p>

      <span class="arch-corner left-2 top-2 border-l border-t" aria-hidden="true" />
      <span class="arch-corner right-2 top-2 border-r border-t" aria-hidden="true" />
      <span class="arch-corner bottom-2 left-2 border-b border-l" aria-hidden="true" />
      <span class="arch-corner bottom-2 right-2 border-b border-r" aria-hidden="true" />

      <div v-if="!ready && !failed" class="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden="true">
        <div class="text-center">
          <div class="arch-loader mx-auto mb-3" />
          <p class="arch-hud text-white/50">BUILDING 3D MAP</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.arch-stage {
  isolation: isolate;
  background: radial-gradient(ellipse 75% 85% at 50% 45%, #14204a 0%, #0a1230 42%, #050813 80%);
  touch-action: none;
  cursor: grab;
}
.arch-stage:active {
  cursor: grabbing;
}
.arch-grid {
  background-image: radial-gradient(rgba(255, 255, 255, 0.07) 1px, transparent 1.3px);
  background-size: 22px 22px;
  mask-image: radial-gradient(ellipse 70% 70% at 50% 50%, #000 25%, transparent 80%);
  -webkit-mask-image: radial-gradient(ellipse 70% 70% at 50% 50%, #000 25%, transparent 80%);
}
.arch-hud {
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  letter-spacing: 0.12em;
  line-height: 1.4;
  white-space: pre;
  text-transform: uppercase;
}
.arch-corner {
  position: absolute;
  width: 12px;
  height: 12px;
  border-color: rgba(255, 255, 255, 0.22);
  pointer-events: none;
}
.arch-layer {
  position: absolute;
  left: 0;
  top: 0;
  opacity: 0;
  transform: translate(8px, -50%);
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  font-size: 9px;
  letter-spacing: 0.12em;
  color: rgba(165, 180, 252, 0.55);
  white-space: nowrap;
  will-change: transform, opacity;
}

.arch-bundle {
  position: absolute;
  left: 0;
  top: 0;
  opacity: 0;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  white-space: nowrap;
  translate: -50% -100%;
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: color-mix(in srgb, var(--node) 70%, white);
  padding: 2px 8px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--node) 12%, rgba(5, 8, 22, 0.7));
  border: 1px solid color-mix(in srgb, var(--node) 35%, transparent);
  will-change: transform, opacity;
  transition: opacity 0.3s;
}
.arch-bundle-bar {
  width: 3px;
  height: 10px;
  border-radius: 2px;
  background: var(--node);
}
.arch-label {
  position: absolute;
  left: 0;
  top: 0;
  opacity: 0;
  pointer-events: none;
  will-change: transform, opacity;
  outline: none;
  cursor: pointer;
}
.arch-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  white-space: nowrap;
  transform: translate(10px, -50%);
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  color: rgba(226, 232, 240, 0.78);
  padding: 2px 7px;
  border-radius: 5px;
  background: rgba(5, 8, 22, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.07);
  backdrop-filter: blur(4px);
  transition: color 0.2s, background-color 0.2s, border-color 0.2s, box-shadow 0.2s;
}
.arch-label.has-rel .arch-chip {
  border-color: color-mix(in srgb, var(--rel) 55%, transparent);
  color: #fff;
}
.arch-label.is-hovered .arch-chip,
.arch-label.is-selected .arch-chip,
.arch-label.is-focus .arch-chip {
  color: #fff;
  border-color: var(--node);
  background: color-mix(in srgb, var(--node) 22%, rgba(5, 8, 22, 0.8));
  box-shadow: 0 0 22px color-mix(in srgb, var(--node) 35%, transparent);
}
.arch-dot {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 9999px;
  background: var(--node);
  box-shadow: 0 0 8px var(--node);
}
.arch-key {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  border-radius: 9999px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(5, 8, 22, 0.65);
  padding: 3px 9px;
  font-family: ui-monospace, 'Cascadia Code', Consolas, monospace;
  font-size: 10px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: rgba(226, 232, 240, 0.72);
  backdrop-filter: blur(4px);
  transition: border-color 0.2s, color 0.2s, background-color 0.2s;
}
.arch-key:hover,
.arch-key.is-active {
  color: #fff;
  border-color: var(--node);
  background: color-mix(in srgb, var(--node) 16%, rgba(5, 8, 22, 0.75));
}
.arch-btn {
  padding: 4px 10px;
  font-size: 11px;
  color: rgba(203, 213, 225, 0.7);
  transition: color 0.2s, background-color 0.2s;
}
.arch-btn:hover {
  color: #fff;
}
.arch-btn.is-active {
  color: #ff6b5e;
  background: rgba(255, 107, 94, 0.1);
}
.arch-loader {
  width: 36px;
  height: 36px;
  border-radius: 9999px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-top-color: #a78bfa;
  animation: arch-spin 1s linear infinite;
}
@keyframes arch-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .arch-loader {
    animation: none;
  }
}
</style>
