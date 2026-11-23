// The 3D architecture map: one glowing node per project / module, a line per
// dependency with a packet running from each node to what it uses. Built on
// the same ideas as the cerberuslabsllc portfolio sphere (additive glow,
// shader-faded lines, labels owned by the Vue component): ArchGraph3D.vue
// owns everything the user reads, this module owns the pixels, the layout,
// the camera and picking, and reports screen positions back every frame.
//
// Loaded with a dynamic import, so three.js stays out of the main bundle.

import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  Color,
  Group,
  Line,
  LineLoop,
  LineSegments,
  PerspectiveCamera,
  Points,
  Scene,
  ShaderMaterial,
  Sprite,
  SpriteMaterial,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
} from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

const FOV = 42
// world units between dependency layers: roomy for a few, tighter for many
const layerGap = (maxRank) => Math.min(3.4, Math.max(1.9, 18 / Math.max(1, maxRank)))
const SPRING_LENGTH = 2.6
const IDLE_ROTATE_DELAY = 5000 // ms after the last touch before the slow spin resumes
const CLICK_SLOP = 5 // px a press may travel and still be a click
const SEGMENTS = 14 // per curved edge
// edge states (shader attribute)
const NORMAL = 0
const DIMMED = 1
const USES = 2 // the selection depends on this (outgoing)
const USED_BY = 3 // this depends on the selection (incoming)
export const USES_COLOR = '#ff6b5e'
export const USED_BY_COLOR = '#5cb8ff'

// ── Layout ──────────────────────────────────────────────────────────────────

function mulberry32(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * Dependency rank of every node: entry points (nothing depends on them) are
 * rank 0, each dependency sits at least one layer below everything that uses
 * it. Cycles (python imports have them) are broken by releasing the node with
 * the most already-ranked users.
 */
export function rankNodes(count, links) {
  const out = Array.from({ length: count }, () => [])
  const incoming = Array.from({ length: count }, () => [])
  const remaining = new Array(count).fill(0)
  for (const [a, b] of links) {
    out[a].push(b)
    incoming[b].push(a)
    remaining[b]++
  }
  const rank = new Array(count).fill(-1)
  const settled = new Array(count).fill(false)
  const queue = []
  for (let i = 0; i < count; i++) {
    if (!remaining[i]) {
      rank[i] = 0
      settled[i] = true
      queue.push(i)
    }
  }
  let head = 0
  const settle = () => {
    while (head < queue.length) {
      const a = queue[head++]
      for (const b of out[a]) {
        if (settled[b]) continue
        rank[b] = Math.max(rank[b], rank[a] + 1)
        if (--remaining[b] === 0) {
          settled[b] = true
          queue.push(b)
        }
      }
    }
  }
  settle()
  while (queue.length < count) {
    let best = -1
    let bestScore = -1
    for (let i = 0; i < count; i++) {
      if (settled[i]) continue
      const score = incoming[i].reduce((n, p) => n + (settled[p] ? 1 : 0), 0)
      if (score > bestScore) {
        best = i
        bestScore = score
      }
    }
    rank[best] = Math.max(rank[best], 0)
    settled[best] = true
    queue.push(best)
    settle()
  }
  return rank
}

/**
 * Place the graph in 3D. Layered: each dependency rank is a horizontal plane
 * (entry points on top), nodes spread on it by a force simulation that only
 * moves them sideways. Free: a plain 3D force layout. Seeded, so the same
 * graph always lands the same way.
 *
 * `cluster`: nodes sharing a `bundle` (a solution folder, a project) are
 * gathered into one wedge of the map - every layer of a bundle in the same
 * direction - and `bundles` reports where to label each one. With a `hub`
 * bundle (the isolated folder, the scope's own project) that one holds the
 * middle and the others ring it, each in its own wedge.
 * @returns {{positions: Map<string, Vector3>, ranks: Map<string, number>, layers: {rank:number,y:number,radius:number,count:number}[], bundles: {key:string,count:number,anchor:Vector3}[], radius: number, height: number, width: number}}
 */
export function layoutGraph(nodes, edges, { layered = true, seed = 7, cluster = false, hub = null } = {}) {
  const n = nodes.length
  const index = new Map(nodes.map((node, i) => [node.id, i]))
  const links = []
  const seen = new Set()
  for (const e of edges) {
    const a = index.get(e.source)
    const b = index.get(e.target)
    if (a === undefined || b === undefined || a === b) continue
    const key = a * n + b
    if (seen.has(key)) continue
    seen.add(key)
    links.push([a, b])
  }
  const rank = rankNodes(n, links)
  const maxRank = Math.max(0, ...rank)
  const gap = layerGap(maxRank)
  const random = mulberry32(seed + n * 31 + links.length)

  // starting positions: rank planes, members ordered around a circle by the
  // average angle of their users so dependencies start under what uses them
  const x = new Float64Array(n)
  const y = new Float64Array(n)
  const z = new Float64Array(n)
  const angle = new Float64Array(n)
  const users = Array.from({ length: n }, () => [])
  for (const [a, b] of links) users[b].push(a)
  const byRank = Array.from({ length: maxRank + 1 }, () => [])
  rank.forEach((r, i) => byRank[r].push(i))

  // clustered: each bundle owns a sector of the circle, sized by its members
  const bundleOf = (i) => nodes[i].bundle ?? nodes[i].group ?? ''
  const sectors = new Map()
  const isHub = (i) => cluster && hub != null && bundleOf(i) === hub
  let hubRadius = 0
  let ringMin = 0
  if (cluster) {
    const sizes = new Map()
    for (let i = 0; i < n; i++) if (!isHub(i)) sizes.set(bundleOf(i), (sizes.get(bundleOf(i)) || 0) + 1)
    const order = [...sizes].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
    const weight = (count) => Math.sqrt(count) + 0.6
    const total = order.reduce((s, [, c]) => s + weight(c), 0)
    let at = 0
    for (const [key, count] of order) {
      const width = (weight(count) / total) * Math.PI * 2
      sectors.set(key, { start: at, width })
      at += width
    }
    if (hub != null) {
      const perLayer = new Map()
      for (let i = 0; i < n; i++) if (isHub(i)) perLayer.set(rank[i], (perLayer.get(rank[i]) || 0) + 1)
      const widest = Math.max(1, ...perLayer.values())
      hubRadius = 0.75 * Math.sqrt(layered ? widest : [...perLayer.values()].reduce((a, b) => a + b, 0)) + 0.3
      ringMin = hubRadius + 2.4
    }
  }

  for (let r = 0; r <= maxRank; r++) {
    const members = byRank[r]
    if (cluster) {
      // spread each bundle's members on this layer across its own sector
      const perBundle = new Map()
      for (const i of members) {
        const key = bundleOf(i)
        if (!perBundle.has(key)) perBundle.set(key, [])
        perBundle.get(key).push(i)
      }
      for (const [key, list] of perBundle) {
        list.sort((a, b) => (nodes[a].id < nodes[b].id ? -1 : 1))
        if (hub != null && key === hub) {
          list.forEach((i, k) => (angle[i] = (k / list.length) * Math.PI * 2))
          continue
        }
        const { start, width } = sectors.get(key)
        list.forEach((i, k) => (angle[i] = start + width * ((k + 0.5) / list.length) * 0.8 + width * 0.1))
      }
    } else if (r === 0) {
      members.sort((a, b) => (nodes[a].group || '').localeCompare(nodes[b].group || ''))
      members.forEach((i, k) => (angle[i] = (k / Math.max(1, members.length)) * Math.PI * 2))
    } else {
      const target = (i) => {
        const ps = users[i]
        if (!ps.length) return random() * Math.PI * 2
        let sx = 0
        let sz = 0
        for (const p of ps) {
          sx += Math.cos(angle[p])
          sz += Math.sin(angle[p])
        }
        return Math.atan2(sz, sx)
      }
      const wanted = new Map(members.map((i) => [i, target(i)]))
      members.sort((a, b) => wanted.get(a) - wanted.get(b))
      members.forEach((i) => (angle[i] = wanted.get(i)))
    }
    const radius = 0.9 * Math.sqrt(members.length) + 0.4
    for (const i of members) {
      let rr = radius * (0.55 + 0.45 * random())
      if (hub != null && cluster) rr = isHub(i) ? hubRadius * (0.35 + 0.65 * random()) : ringMin + random()
      x[i] = Math.cos(angle[i]) * rr
      z[i] = Math.sin(angle[i]) * rr
      y[i] = layered ? (maxRank / 2 - r) * gap : (random() - 0.5) * radius * 2
    }
  }

  // force simulation
  const iterations = n < 80 ? 320 : n < 200 ? 240 : 150
  const repulse = 3.2
  const spring = 0.06
  const gravity = layered ? 0.012 : 0.02
  const dx = new Float64Array(n)
  const dy = new Float64Array(n)
  const dz = new Float64Array(n)
  for (let it = 0; it < iterations; it++) {
    const cool = 1 - it / iterations
    dx.fill(0)
    dy.fill(0)
    dz.fill(0)
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let ex = x[i] - x[j]
        let ey = y[i] - y[j]
        let ez = z[i] - z[j]
        let d2 = ex * ex + ey * ey + ez * ez
        if (d2 < 1e-4) {
          ex = random() - 0.5
          ez = random() - 0.5
          d2 = 0.01
        }
        if (d2 > 400) continue
        const f = repulse / d2
        const inv = 1 / Math.sqrt(d2)
        dx[i] += ex * inv * f
        dy[i] += ey * inv * f
        dz[i] += ez * inv * f
        dx[j] -= ex * inv * f
        dy[j] -= ey * inv * f
        dz[j] -= ez * inv * f
      }
    }
    for (const [a, b] of links) {
      const ex = x[b] - x[a]
      const ey = y[b] - y[a]
      const ez = z[b] - z[a]
      const d = Math.sqrt(ex * ex + ey * ey + ez * ez) || 1e-3
      // in layered mode the vertical gap is fixed: springs pull sideways only
      const rest = layered ? Math.sqrt(SPRING_LENGTH ** 2 + ey * ey) : SPRING_LENGTH
      const f = (d - rest) * spring
      dx[a] += (ex / d) * f
      dy[a] += (ey / d) * f
      dz[a] += (ez / d) * f
      dx[b] -= (ex / d) * f
      dy[b] -= (ey / d) * f
      dz[b] -= (ez / d) * f
    }
    if (cluster) {
      // bundle cohesion: everyone drifts toward their bundle's middle
      const centers = new Map()
      for (let i = 0; i < n; i++) {
        const key = bundleOf(i)
        const c = centers.get(key) || { x: 0, y: 0, z: 0, n: 0 }
        c.x += x[i]
        c.y += y[i]
        c.z += z[i]
        c.n++
        centers.set(key, c)
      }
      for (let i = 0; i < n; i++) {
        if (hub != null) {
          const d = Math.hypot(x[i], z[i]) || 1e-3
          if (isHub(i)) {
            // the hub keeps the middle
            dx[i] -= x[i] * 0.06
            dz[i] -= z[i] * 0.06
            continue
          }
          // everything else stays out on the ring around it
          if (d < ringMin) {
            dx[i] += (x[i] / d) * (ringMin - d) * 0.25
            dz[i] += (z[i] / d) * (ringMin - d) * 0.25
          }
        }
        const c = centers.get(bundleOf(i))
        if (c.n < 2) continue
        dx[i] += (c.x / c.n - x[i]) * 0.05
        dz[i] += (c.z / c.n - z[i]) * 0.05
        if (!layered) dy[i] += (c.y / c.n - y[i]) * 0.05
      }
    }
    const step = 0.08 + 0.55 * cool
    for (let i = 0; i < n; i++) {
      dx[i] -= x[i] * gravity
      dz[i] -= z[i] * gravity
      if (!layered) dy[i] -= y[i] * gravity
      const len = Math.sqrt(dx[i] ** 2 + (layered ? 0 : dy[i] ** 2) + dz[i] ** 2)
      const scale = len > step ? step / len : 1
      x[i] += dx[i] * scale
      z[i] += dz[i] * scale
      if (!layered) y[i] += dy[i] * scale
    }
  }

  // center sideways; free layouts center vertically too
  let mx = 0
  let mz = 0
  let my = 0
  for (let i = 0; i < n; i++) {
    mx += x[i]
    mz += z[i]
    my += y[i]
  }
  mx /= n || 1
  mz /= n || 1
  my = layered ? 0 : my / (n || 1)
  const positions = new Map()
  const ranks = new Map()
  let radius = 1
  let height = 0.5 // half extents, for fitting the camera
  let width = 0.5
  const layerRadius = new Array(maxRank + 1).fill(0)
  nodes.forEach((node, i) => {
    const p = new Vector3(x[i] - mx, y[i] - my, z[i] - mz)
    positions.set(node.id, p)
    ranks.set(node.id, rank[i])
    radius = Math.max(radius, p.length())
    height = Math.max(height, Math.abs(p.y))
    width = Math.max(width, Math.hypot(p.x, p.z))
    layerRadius[rank[i]] = Math.max(layerRadius[rank[i]], Math.hypot(p.x, p.z))
  })
  const layers = layered
    ? byRank.map((members, r) => ({ rank: r, y: (maxRank / 2 - r) * gap, radius: layerRadius[r] + 0.9, count: members.length }))
    : []

  // where each bundle's name goes: just outside and above its wedge
  const bundles = []
  if (cluster) {
    const groups = new Map()
    nodes.forEach((node, i) => {
      const key = bundleOf(i)
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key).push(positions.get(node.id))
    })
    for (const [key, ps] of groups) {
      const c = new Vector3()
      ps.forEach((p) => c.add(p))
      c.multiplyScalar(1 / ps.length)
      let top = -Infinity
      let reach = 0
      for (const p of ps) {
        top = Math.max(top, p.y)
        reach = Math.max(reach, Math.hypot(p.x - c.x, p.z - c.z))
      }
      const dir = new Vector3(c.x, 0, c.z)
      if (dir.lengthSq() < 1e-4) dir.set(0, 0, 1)
      dir.normalize()
      const anchor = layered
        ? new Vector3(c.x, top + 0.8, c.z).addScaledVector(dir, Math.min(reach, 2) * 0.5)
        : c.clone().add(new Vector3(0, reach + 0.8, 0))
      bundles.push({ key, count: ps.length, anchor })
    }
  }
  return { positions, ranks, layers, bundles, radius, height, width }
}

// ── Shaders ─────────────────────────────────────────────────────────────────

// Glowing points: node halos and cores, packets, dust.
const GLOW_VERT = /* glsl */ `
  attribute vec3 aColor;
  attribute float aSize;
  attribute float aAlpha;
  uniform float uScale;
  uniform float uWhite;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vColor = mix(aColor, vec3(1.0), uWhite);
    vAlpha = aAlpha;
    gl_PointSize = max(1.5, aSize * uScale / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`

const SOFT_POINT_FRAG = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float core = smoothstep(0.5, 0.0, d);
    gl_FragColor = vec4(vColor * (0.7 + 0.6 * core), pow(core, 1.6) * vAlpha);
    #include <colorspace_fragment>
  }
`

// Dependency lines. aT runs 0 -> 1 from the user to what it uses; a comet
// tail travels that way. aState: 0 normal, 1 dimmed, 2 uses, 3 used by.
const EDGE_VERT = /* glsl */ `
  attribute vec3 aColor;
  attribute float aT;
  attribute float aState;
  attribute float aPhase;
  uniform float uTime;
  uniform float uMotion;
  uniform vec3 uUses;
  uniform vec3 uUsedBy;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    float hi = step(1.5, aState);
    float dimmed = step(0.5, aState) * (1.0 - hi);
    float base = mix(mix(0.14, 0.025, dimmed), 0.55, hi);
    float speed = mix(0.14, 0.45, hi);
    float d = fract(uTime * speed + aPhase) - aT;
    if (d < 0.0) d += 1.0;
    float tail = smoothstep(0.24, 0.0, d);
    float pulse = uMotion * tail * tail * mix(mix(0.45, 0.0, dimmed), 1.0, hi);
    vColor = aState > 2.5 ? uUsedBy : (aState > 1.5 ? uUses : aColor);
    vAlpha = base + pulse;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`

const EDGE_FRAG = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    gl_FragColor = vec4(vColor, vAlpha);
    #include <colorspace_fragment>
  }
`

// ── Helpers ─────────────────────────────────────────────────────────────────

const additive = { transparent: true, depthWrite: false, blending: AdditiveBlending }

function glowTexture(stops) {
  const size = 256
  const c = document.createElement('canvas')
  c.width = c.height = size
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  for (const [at, color] of stops) g.addColorStop(at, color)
  ctx.fillStyle = g
  ctx.fillRect(0, 0, size, size)
  const texture = new CanvasTexture(c)
  texture.colorSpace = SRGBColorSpace
  return texture
}

const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

function bezier(p0, c, p1, t, out) {
  const u = 1 - t
  return out
    .copy(p0)
    .multiplyScalar(u * u)
    .addScaledVector(c, 2 * u * t)
    .addScaledVector(p1, t * t)
}

// ── Scene ───────────────────────────────────────────────────────────────────

/**
 * @param {object} opts
 * @param {HTMLCanvasElement} opts.canvas
 * @param {HTMLElement} opts.el the stage: pointer input is read here
 * @param {{id: string, color: string, group: string, degree: number}[]} opts.nodes
 * @param {{source: string, target: string}[]} opts.edges
 * @param {ReturnType<typeof layoutGraph>} opts.layout
 * @param {string|null} [opts.focusId] a node to mark (focus mode)
 * @param {boolean} [opts.frameFocus] start flown in to the focus node (else the whole map)
 * @param {boolean} [opts.reducedMotion]
 * @param {(frame: object) => void} opts.onFrame after every render
 * @param {(id: string|null) => void} opts.onHover
 * @param {(id: string|null) => void} opts.onSelect click on a node, or on empty space (null)
 * @param {(id: string) => void} opts.onFocus double-click on a node
 * Throws if WebGL isn't available.
 */
export function createArchScene({ canvas, el, nodes: nodeDefs, edges: edgeDefs, layout, focusId = null, frameFocus = true, reducedMotion = false, onFrame, onHover, onSelect, onFocus }) {
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' })
  renderer.setClearColor(0x000000, 0)

  const scene = new Scene()
  const camera = new PerspectiveCamera(FOV, 1, 0.1, 4000)
  const world = new Group()
  scene.add(world)

  const disposables = []
  const track = (...items) => {
    disposables.push(...items)
    return items[0]
  }
  const shared = { uTime: { value: 0 }, uScale: { value: 1 } }

  const R = Math.max(4, layout.radius)
  const n = nodeDefs.length
  // secondary nodes (what an isolated folder links to, what a scope reaches)
  // are drawn smaller and fainter than the subject itself
  const nodes = nodeDefs.map((def, i) => ({
    ...def,
    index: i,
    pos: layout.positions.get(def.id) || new Vector3(),
    color: new Color(def.color),
    base: def.emphasis === 'secondary' ? 0.62 : 1,
    size: (0.55 + 0.16 * Math.sqrt(def.degree || 0)) * (def.emphasis === 'secondary' ? 0.74 : 1),
    x: 0,
    y: 0,
    dist: 0,
    visible: false,
  }))
  const nodeById = new Map(nodes.map((node) => [node.id, node]))

  // Nodes: a soft halo and a bright core per node, two draws for all of them
  const positionAttr = new BufferAttribute(new Float32Array(n * 3), 3)
  const colorAttr = new BufferAttribute(new Float32Array(n * 3), 3)
  nodes.forEach((node, i) => {
    node.pos.toArray(positionAttr.array, i * 3)
    node.color.toArray(colorAttr.array, i * 3)
  })
  const pointLayer = (sizeOf, alpha, white) => {
    const g = track(new BufferGeometry())
    g.setAttribute('position', positionAttr)
    g.setAttribute('aColor', colorAttr)
    g.setAttribute('aSize', new BufferAttribute(Float32Array.from(nodes, sizeOf), 1))
    g.setAttribute('aAlpha', new BufferAttribute(new Float32Array(n).fill(alpha), 1))
    const material = track(
      new ShaderMaterial({
        vertexShader: GLOW_VERT,
        fragmentShader: SOFT_POINT_FRAG,
        uniforms: { uScale: shared.uScale, uWhite: { value: white } },
        ...additive,
      })
    )
    world.add(new Points(g, material))
    return g
  }
  const halos = pointLayer((node) => node.size * 2.6, 0.5, 0)
  const cores = pointLayer((node) => node.size * 0.8, 1, 0.35)
  const haloSize = halos.getAttribute('aSize')
  const haloAlpha = halos.getAttribute('aAlpha')
  const coreAlpha = cores.getAttribute('aAlpha')
  const baseHalo = Float32Array.from(haloSize.array)

  // Edges: curved, bowed away from the vertical axis, one LineSegments draw
  const edges = []
  {
    const tmp = new Vector3()
    const mid = new Vector3()
    const out = new Vector3()
    for (const e of edgeDefs) {
      const a = nodeById.get(e.source)
      const b = nodeById.get(e.target)
      if (!a || !b || a === b) continue
      const len = a.pos.distanceTo(b.pos)
      mid.copy(a.pos).add(b.pos).multiplyScalar(0.5)
      out.set(mid.x, 0, mid.z)
      if (out.lengthSq() < 1e-4) out.set(1, 0, 0)
      out.normalize()
      const control = mid.clone().addScaledVector(out, len * 0.16).add(new Vector3(0, len * 0.04, 0))
      const pts = []
      for (let s = 0; s <= SEGMENTS; s++) pts.push(bezier(a.pos, control, b.pos, s / SEGMENTS, tmp).clone())
      edges.push({ a, b, control, pts, phase: Math.random(), state: NORMAL })
    }
  }
  const E = edges.length
  const vertsPerEdge = SEGMENTS * 2
  const edgeGeo = track(new BufferGeometry())
  {
    const pos = new Float32Array(E * vertsPerEdge * 3)
    const col = new Float32Array(E * vertsPerEdge * 3)
    const ts = new Float32Array(E * vertsPerEdge)
    const phase = new Float32Array(E * vertsPerEdge)
    const c = new Color()
    const slate = new Color('#64748b')
    edges.forEach((edge, k) => {
      for (let s = 0; s < SEGMENTS; s++) {
        for (const [o, step] of [[0, s], [1, s + 1]]) {
          const v = k * vertsPerEdge + s * 2 + o
          const t = step / SEGMENTS
          edge.pts[step].toArray(pos, v * 3)
          c.copy(edge.a.color).lerp(edge.b.color, t).lerp(slate, 0.35).toArray(col, v * 3)
          ts[v] = t
          phase[v] = edge.phase
        }
      }
    })
    edgeGeo.setAttribute('position', new BufferAttribute(pos, 3))
    edgeGeo.setAttribute('aColor', new BufferAttribute(col, 3))
    edgeGeo.setAttribute('aT', new BufferAttribute(ts, 1))
    edgeGeo.setAttribute('aPhase', new BufferAttribute(phase, 1))
    edgeGeo.setAttribute('aState', new BufferAttribute(new Float32Array(E * vertsPerEdge), 1))
    const material = track(
      new ShaderMaterial({
        vertexShader: EDGE_VERT,
        fragmentShader: EDGE_FRAG,
        uniforms: {
          uTime: shared.uTime,
          uMotion: { value: reducedMotion ? 0 : 1 },
          uUses: { value: new Color(USES_COLOR) },
          uUsedBy: { value: new Color(USED_BY_COLOR) },
        },
        ...additive,
      })
    )
    world.add(new LineSegments(edgeGeo, material))
  }
  const edgeState = edgeGeo.getAttribute('aState')

  // Packets: the head of each comet, a glowing dot riding from user to dependency
  const packets = track(new BufferGeometry())
  {
    const m = Math.max(1, E)
    packets.setAttribute('position', new BufferAttribute(new Float32Array(m * 3), 3))
    const col = new Float32Array(m * 3)
    edges.forEach((edge, k) => edge.a.color.clone().lerp(edge.b.color, 0.5).toArray(col, k * 3))
    packets.setAttribute('aColor', new BufferAttribute(col, 3))
    packets.setAttribute('aSize', new BufferAttribute(new Float32Array(m).fill(0.5), 1))
    packets.setAttribute('aAlpha', new BufferAttribute(new Float32Array(m).fill(reducedMotion ? 0 : 0.55), 1))
    const material = track(
      new ShaderMaterial({
        vertexShader: GLOW_VERT,
        fragmentShader: SOFT_POINT_FRAG,
        uniforms: { uScale: shared.uScale, uWhite: { value: 0.15 } },
        ...additive,
      })
    )
    world.add(new Points(packets, material))
  }
  const packetPos = packets.getAttribute('position')
  const packetColor = packets.getAttribute('aColor')
  const packetAlpha = packets.getAttribute('aAlpha')
  const packetSize = packets.getAttribute('aSize')
  const packetBase = Float32Array.from(packetColor.array)
  const usesColor = new Color(USES_COLOR)
  const usedByColor = new Color(USED_BY_COLOR)

  // Layer rings and the spine through them (layered layouts)
  const layerAnchors = []
  if (layout.layers.length) {
    for (const layer of layout.layers) {
      const pts = []
      const seg = 96
      for (let i = 0; i < seg; i++) {
        const a = (i / seg) * Math.PI * 2
        pts.push(Math.cos(a) * layer.radius, layer.y, Math.sin(a) * layer.radius)
      }
      const g = track(new BufferGeometry())
      g.setAttribute('position', new BufferAttribute(new Float32Array(pts), 3))
      const ring = new LineLoop(g, track(new ShaderMaterial({
        vertexShader: /* glsl */ `void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
        fragmentShader: /* glsl */ `void main() { gl_FragColor = vec4(0.42, 0.5, 0.72, 0.22); }`,
        ...additive,
      })))
      world.add(ring)
      layerAnchors.push({ ...layer, anchor: new Vector3(layer.radius, layer.y, 0) })
    }
    const top = layout.layers[0].y + 1.5
    const bottom = layout.layers[layout.layers.length - 1].y - 1.5
    const g = track(new BufferGeometry())
    g.setAttribute('position', new BufferAttribute(new Float32Array([0, top, 0, 0, bottom, 0]), 3))
    world.add(new Line(g, track(new ShaderMaterial({
      vertexShader: /* glsl */ `void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */ `void main() { gl_FragColor = vec4(0.45, 0.42, 0.95, 0.18); }`,
      ...additive,
    }))))
  }

  // Selection / hover / focus rings: billboarded sprites
  const ringTexture = track(glowTexture([
    [0, 'rgba(0,0,0,0)'],
    [0.62, 'rgba(255,255,255,0)'],
    [0.7, 'rgba(255,255,255,0.9)'],
    [0.76, 'rgba(255,255,255,0.25)'],
    [1, 'rgba(0,0,0,0)'],
  ]))
  const ringSprite = (color) => {
    const sprite = new Sprite(track(new SpriteMaterial({ map: ringTexture, color: new Color(color), ...additive })))
    sprite.visible = false
    world.add(sprite)
    return sprite
  }
  const selectRing = ringSprite('#ffffff')
  const hoverRing = ringSprite('#cbd5e1')
  const focusRing = ringSprite(USES_COLOR)

  // Dust: specks far out, for depth while orbiting
  {
    const N = 420
    const pos = new Float32Array(N * 3)
    const v = new Vector3()
    for (let i = 0; i < N; i++) {
      v.set(Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random() * 2 - 1).normalize().multiplyScalar(R * (2.2 + Math.random() * 3))
      v.toArray(pos, i * 3)
    }
    const g = track(new BufferGeometry())
    g.setAttribute('position', new BufferAttribute(pos, 3))
    g.setAttribute('aColor', new BufferAttribute(new Float32Array(N * 3).fill(0.75), 3))
    g.setAttribute('aSize', new BufferAttribute(Float32Array.from({ length: N }, () => 0.08 + Math.random() * 0.14), 1))
    g.setAttribute('aAlpha', new BufferAttribute(Float32Array.from({ length: N }, () => 0.12 + Math.random() * 0.3), 1))
    const dust = new Points(g, track(new ShaderMaterial({
      vertexShader: GLOW_VERT,
      fragmentShader: SOFT_POINT_FRAG,
      uniforms: { uScale: shared.uScale, uWhite: { value: 0 } },
      ...additive,
    })))
    scene.add(dust)
  }

  // ── Camera & controls ──────────────────────────────────────────────────────
  const controls = new OrbitControls(camera, el)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.rotateSpeed = 0.55
  controls.zoomSpeed = 0.9
  controls.panSpeed = 0.7
  controls.screenSpacePanning = true
  controls.autoRotateSpeed = 0.35
  controls.minDistance = R * 0.25
  controls.maxDistance = R * 7

  const home = { target: new Vector3(0, 0, 0), position: new Vector3() }
  // far enough that the tallest and widest parts both fit, from a camera a
  // little above the graph (the near side sticks out by about `width`)
  function fitDistance() {
    const tanV = Math.tan((FOV * Math.PI) / 360)
    const tanH = tanV * Math.max(0.5, camera.aspect)
    const h = (layout.height || R) + 1
    const w = (layout.width || R) + 1
    return Math.max(h / tanV, w / tanH) * 1.05 + w * 0.6
  }
  function homePosition() {
    const d = fitDistance()
    return new Vector3(0.38, 0.22, 0.9).normalize().multiplyScalar(d)
  }
  home.position.copy(homePosition())
  camera.position.copy(home.position)
  controls.target.copy(home.target)
  controls.update()

  let lastInteraction = performance.now()
  let autoRotate = !reducedMotion
  controls.addEventListener('start', () => {
    lastInteraction = performance.now()
    flight = null
  })

  let flight = null // { fromTarget, toTarget, fromPos, toPos, start, duration }
  function flyTo(target, position, duration = 750) {
    if (reducedMotion) {
      controls.target.copy(target)
      camera.position.copy(position)
      return
    }
    flight = {
      fromTarget: controls.target.clone(),
      toTarget: target.clone(),
      fromPos: camera.position.clone(),
      toPos: position.clone(),
      start: performance.now(),
      duration,
    }
  }
  function flyToNode(id) {
    const node = nodeById.get(id)
    if (!node) return
    // keep the viewing direction, move in a little if far out
    const offset = camera.position.clone().sub(controls.target)
    const dist = Math.min(offset.length(), R * 1.6)
    offset.setLength(Math.max(dist, R * 0.7))
    flyTo(node.pos, node.pos.clone().add(offset))
  }

  // ── Highlight state ───────────────────────────────────────────────────────
  let selectedId = null
  let hoverId = null
  let groupKey = null

  function applyStates() {
    const subject = selectedId || hoverId
    const near = new Set()
    if (subject) {
      near.add(subject)
      for (const edge of edges) {
        if (edge.a.id === subject) near.add(edge.b.id)
        if (edge.b.id === subject) near.add(edge.a.id)
      }
    }
    edges.forEach((edge, k) => {
      let state = NORMAL
      if (subject) state = edge.a.id === subject ? USES : edge.b.id === subject ? USED_BY : DIMMED
      else if (groupKey) state = edge.a.group === groupKey || edge.b.group === groupKey ? NORMAL : DIMMED
      edge.state = state
      edgeState.array.fill(state, k * vertsPerEdge, (k + 1) * vertsPerEdge)
      const hi = state === USES || state === USED_BY
      const tint = state === USES ? usesColor : state === USED_BY ? usedByColor : null
      if (tint) tint.toArray(packetColor.array, k * 3)
      else packetColor.array.set(packetBase.subarray(k * 3, k * 3 + 3), k * 3)
      packetAlpha.array[k] = reducedMotion ? 0 : hi ? 1 : state === DIMMED ? 0.04 : 0.55
      packetSize.array[k] = hi ? 0.75 : 0.5
    })
    nodes.forEach((node, i) => {
      let alpha = node.base
      if (subject) alpha = near.has(node.id) ? 1 : 0.14
      else if (groupKey) alpha = node.group === groupKey ? Math.max(node.base, 0.8) : 0.14
      node.alpha = alpha
      haloAlpha.array[i] = 0.5 * alpha
      coreAlpha.array[i] = alpha
    })
    for (const attr of [edgeState, packetColor, packetAlpha, packetSize, haloAlpha, coreAlpha]) attr.needsUpdate = true
  }
  applyStates()

  function placeRing(sprite, id, scale) {
    const node = id && nodeById.get(id)
    sprite.visible = !!node
    if (node) {
      sprite.position.copy(node.pos)
      sprite.scale.setScalar(node.size * scale)
    }
  }
  placeRing(focusRing, focusId, 3.4)

  // ── Picking & input ───────────────────────────────────────────────────────
  const size = { w: 1, h: 1 }
  const pointer = { x: 0, y: 0, inside: false, down: null }
  const local = (e) => {
    const rect = el.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }
  const isUi = (target) => !!target?.closest?.('[data-arch-ui]')
  const labelIdOf = (target) => target?.closest?.('[data-node-label]')?.dataset.id || null

  function pick(x, y) {
    let best = null
    let bestD = Infinity
    for (const node of nodes) {
      if (!node.visible) continue
      const radius = Math.max(10, node.px * 0.9)
      const d = Math.hypot(node.x - x, node.y - y)
      if (d < radius && d < bestD) {
        best = node
        bestD = d
      }
    }
    return best ? best.id : null
  }

  function setHover(id) {
    if (id === hoverId) return
    hoverId = id
    applyStates()
    onHover?.(id)
  }

  function onPointerDown(e) {
    if (isUi(e.target)) return
    const p = local(e)
    pointer.down = { x: p.x, y: p.y, t: performance.now(), label: labelIdOf(e.target) }
    lastInteraction = performance.now()
  }
  function onPointerMove(e) {
    const p = local(e)
    pointer.x = p.x
    pointer.y = p.y
    pointer.inside = true
    pointer.overUi = isUi(e.target)
    pointer.overLabel = labelIdOf(e.target)
  }
  function onPointerUp(e) {
    const down = pointer.down
    pointer.down = null
    if (!down || isUi(e.target)) return
    const p = local(e)
    if (Math.hypot(p.x - down.x, p.y - down.y) > CLICK_SLOP) return
    onSelect?.(down.label || pick(p.x, p.y))
  }
  function onDoubleClick(e) {
    if (isUi(e.target)) return
    const p = local(e)
    const id = labelIdOf(e.target) || pick(p.x, p.y)
    if (id) onFocus?.(id)
  }
  const onPointerLeave = () => {
    pointer.inside = false
    pointer.overLabel = null
  }
  el.addEventListener('pointerdown', onPointerDown)
  el.addEventListener('pointermove', onPointerMove)
  el.addEventListener('pointerup', onPointerUp)
  el.addEventListener('pointerleave', onPointerLeave)
  el.addEventListener('dblclick', onDoubleClick)

  function setSize(w, h) {
    if (!w || !h) return
    size.w = w
    size.h = h
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    // world size -> pixel size at distance 1
    shared.uScale.value = (h * renderer.getPixelRatio()) / (2 * Math.tan((FOV * Math.PI) / 360))
  }
  const resizeObserver = new ResizeObserver(([entry]) => {
    const { width, height } = entry.contentRect
    setSize(Math.round(width), Math.round(height))
  })
  resizeObserver.observe(el)

  // ── Loop ───────────────────────────────────────────────────────────────────
  let raf = 0
  let visible = true
  let paused = false
  let last = performance.now()
  let fpsFrames = 0
  let fpsSince = last
  let fps = 0
  const frameNodes = nodes.map((node) => ({ id: node.id, x: 0, y: 0, depth: 0, visible: false, alpha: 1 }))
  const frameLayers = layerAnchors.map((layer) => ({ rank: layer.rank, count: layer.count, x: 0, y: 0, visible: false }))
  const bundleAnchors = layout.bundles || []
  const frameBundles = bundleAnchors.map((b) => ({ key: b.key, x: 0, y: 0, visible: false }))
  const frame = { nodes: frameNodes, layers: frameLayers, bundles: frameBundles, fps: 0, hover: null, dragging: false }
  const v = new Vector3()
  const tmp = new Vector3()
  const pxPerUnit = () => shared.uScale.value / renderer.getPixelRatio()

  function tick(now) {
    raf = requestAnimationFrame(tick)
    const dt = Math.min(0.05, (now - last) / 1000)
    last = now
    const time = now / 1000
    shared.uTime.value = time

    if (flight) {
      const k = Math.min(1, (now - flight.start) / flight.duration)
      const e = easeInOutCubic(k)
      controls.target.lerpVectors(flight.fromTarget, flight.toTarget, e)
      camera.position.lerpVectors(flight.fromPos, flight.toPos, e)
      if (k >= 1) flight = null
    }
    controls.autoRotate = autoRotate && !selectedId && !flight && now - lastInteraction > IDLE_ROTATE_DELAY
    controls.update()

    // screen positions: labels, picking and depth fading all need them
    camera.updateMatrixWorld()
    let near = Infinity
    let far = 0
    for (const node of nodes) {
      node.dist = camera.position.distanceTo(node.pos)
      near = Math.min(near, node.dist)
      far = Math.max(far, node.dist)
    }
    const span = Math.max(1e-3, far - near)
    nodes.forEach((node, i) => {
      v.copy(node.pos).project(camera)
      const inFront = v.z < 1 && v.z > -1
      node.x = (v.x * 0.5 + 0.5) * size.w
      node.y = (-v.y * 0.5 + 0.5) * size.h
      node.visible = inFront
      node.px = (node.size * 0.8 * pxPerUnit()) / Math.max(0.1, node.dist)
      const f = frameNodes[i]
      f.x = node.x
      f.y = node.y
      f.visible = inFront
      f.depth = (node.dist - near) / span
      f.alpha = node.alpha
    })
    frameLayers.forEach((f, i) => {
      tmp.copy(layerAnchors[i].anchor).project(camera)
      f.visible = tmp.z < 1 && tmp.z > -1
      f.x = (tmp.x * 0.5 + 0.5) * size.w
      f.y = (-tmp.y * 0.5 + 0.5) * size.h
    })
    frameBundles.forEach((f, i) => {
      tmp.copy(bundleAnchors[i].anchor).project(camera)
      f.visible = tmp.z < 1 && tmp.z > -1
      f.x = (tmp.x * 0.5 + 0.5) * size.w
      f.y = (-tmp.y * 0.5 + 0.5) * size.h
    })

    // hover: over a label, or near a node (never mid-drag)
    if (pointer.inside && !pointer.down && !pointer.overUi) setHover(pointer.overLabel || pick(pointer.x, pointer.y))
    else if ((!pointer.inside || pointer.overUi) && hoverId) setHover(null)

    // the selected node breathes; packets ride their curves
    const pulse = reducedMotion ? 0 : Math.sin(time * 3) * 0.08
    placeRing(selectRing, selectedId, 3 * (1 + pulse))
    placeRing(hoverRing, hoverId && hoverId !== selectedId ? hoverId : null, 2.7)
    nodes.forEach((node, i) => {
      const boost = node.id === selectedId ? 1.35 : node.id === hoverId ? 1.2 : 1
      haloSize.array[i] = baseHalo[i] * boost
    })
    haloSize.needsUpdate = true
    if (!reducedMotion) {
      edges.forEach((edge, k) => {
        const hi = edge.state === USES || edge.state === USED_BY
        const t = (time * (hi ? 0.45 : 0.14) + edge.phase) % 1
        bezier(edge.a.pos, edge.control, edge.b.pos, t, tmp).toArray(packetPos.array, k * 3)
      })
      packetPos.needsUpdate = true
    }

    renderer.render(scene, camera)

    fpsFrames++
    if (now - fpsSince > 500) {
      fps = Math.round((fpsFrames * 1000) / (now - fpsSince))
      fpsFrames = 0
      fpsSince = now
    }
    frame.fps = fps
    frame.hover = hoverId
    frame.dragging = !!pointer.down
    onFrame?.(frame)
  }

  function updateRunning() {
    const shouldRun = visible && !paused && document.visibilityState === 'visible'
    if (shouldRun && !raf) {
      last = performance.now()
      fpsSince = last
      fpsFrames = 0
      raf = requestAnimationFrame(tick)
    } else if (!shouldRun && raf) {
      cancelAnimationFrame(raf)
      raf = 0
    }
  }
  const io = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting
    updateRunning()
  })
  io.observe(el)
  document.addEventListener('visibilitychange', updateRunning)

  const r0 = el.getBoundingClientRect()
  setSize(Math.round(r0.width), Math.round(r0.height))
  home.position.copy(homePosition())
  camera.position.copy(home.position)
  controls.maxDistance = Math.max(R * 7, fitDistance() * 2.5)
  controls.update()
  if (frameFocus && focusId && nodeById.has(focusId)) flyToNode(focusId)
  updateRunning()

  return {
    setSelected(id) {
      selectedId = id && nodeById.has(id) ? id : null
      lastInteraction = performance.now()
      applyStates()
      if (selectedId) flyToNode(selectedId)
    },
    setGroup(key) {
      groupKey = key
      applyStates()
    },
    setAutoRotate(on) {
      autoRotate = on && !reducedMotion
      lastInteraction = 0
    },
    resetView() {
      lastInteraction = performance.now()
      flyTo(home.target, homePosition())
    },
    flyTo: flyToNode,
    setPaused(value) {
      paused = value
      updateRunning()
    },
    dispose() {
      cancelAnimationFrame(raf)
      raf = 0
      io.disconnect()
      resizeObserver.disconnect()
      document.removeEventListener('visibilitychange', updateRunning)
      el.removeEventListener('pointerdown', onPointerDown)
      el.removeEventListener('pointermove', onPointerMove)
      el.removeEventListener('pointerup', onPointerUp)
      el.removeEventListener('pointerleave', onPointerLeave)
      el.removeEventListener('dblclick', onDoubleClick)
      controls.dispose()
      for (const d of disposables) d.dispose?.()
      renderer.dispose()
      renderer.forceContextLoss()
    },
  }
}
