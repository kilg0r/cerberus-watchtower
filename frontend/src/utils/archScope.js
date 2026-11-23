// Narrowed maps below the project level, built from the .NET scanner's type
// and call indexes (the archCtx from useArchContext):
// - namespace / file / class scopes: one node per class, linked by what its
//   methods call, the requests it sends (to their handlers), and what it has
//   injected; interfaces link on to their implementations
// - method / endpoint scopes: one node per method, linked by calls, interface
//   dispatch and MediatR sends - the exact path a request takes
// The scope itself is drawn full size; for class scopes what it reaches
// (downstream, to the chosen depth) and who reaches it (callers, one hop) are
// drawn smaller. A method or endpoint trace draws its downstream path - the
// flow itself - full size too.

export const SCOPE_LIMIT = 160
const USERS_PER_NODE = 12 // callers shown per scope node; the rest are counted

// scanner false positives ("class survived" in a comment) are lowercase
const isType = (name) => /^[A-Z_]/.test(name)
const bareType = (t) => (t || '').replace(/<.*$/, '').replace(/[?[\]\s]/g, '')

export function namespaceOf(info) {
  if (info?.namespace) return info.namespace
  // scans from before namespaces were indexed (cached History deltas):
  // C# convention puts the namespace on the folder path
  const parts = (info?.file || '').split(/[\\/]/).slice(0, -1)
  return parts.length ? parts.join('.') : '(global)'
}
export const fileOf = (info) => (info?.file || '').replace(/\\/g, '/')
export const baseName = (path) => path.split('/').pop()

export const SCOPE_KINDS = {
  namespace: 'namespace',
  file: 'file',
  class: 'class',
  method: 'method',
  endpoint: 'endpoint',
}
export const scopeId = (scope) => (scope ? `${scope.kind}:${scope.key}` : null)

// ── indexes, built once per architecture payload ─────────────────────────────

const memo = new WeakMap()
function indexes(ctx) {
  let ix = memo.get(ctx)
  if (ix) return ix
  const types = ctx.types
  const classOut = new Map() // type -> Map(target -> kind)
  const classIn = new Map() // type -> Set(source)
  const linkClass = (from, to, kind) => {
    if (!to || to === from || !types[to] || !isType(to)) return
    let out = classOut.get(from)
    if (!out) classOut.set(from, (out = new Map()))
    if (!out.has(to)) out.set(to, kind)
    let users = classIn.get(to)
    if (!users) classIn.set(to, (users = new Set()))
    users.add(from)
  }
  const sendersOf = new Map() // request -> ["Type.method"]
  for (const name of Object.keys(types)) {
    if (!isType(name)) continue
    for (const m of ctx.classMethods[name] || []) {
      for (const c of m.calls || []) linkClass(name, c.type, 'calls')
      for (const r of m.sends || []) {
        linkClass(name, ctx.handlerByRequest[r]?.handler, 'sends')
        if (!sendersOf.has(r)) sendersOf.set(r, [])
        sendersOf.get(r).push(`${name}.${m.name}`)
      }
    }
    for (const r of ctx.classSends[name] || []) linkClass(name, ctx.handlerByRequest[r]?.handler, 'sends')
    for (const u of ctx.classServiceUses[name] || []) linkClass(name, bareType(u.type), 'uses')
    for (const d of ctx.classDeps[name] || []) linkClass(name, bareType(d.type), 'injects')
    if (types[name].kind === 'interface') {
      for (const impl of ctx.implementations[name] || []) linkClass(name, impl, 'implemented by')
    }
  }
  ix = { classOut, classIn, sendersOf }
  memo.set(ctx, ix)
  return ix
}

function methodOut(ctx, key) {
  const dot = key.indexOf('.')
  const type = key.slice(0, dot)
  const name = key.slice(dot + 1)
  const out = []
  if (ctx.types[type]?.kind === 'interface') {
    for (const impl of ctx.implementations[type] || []) {
      if ((ctx.classMethods[impl] || []).some((m) => m.name === name)) out.push([`${impl}.${name}`, 'implemented by'])
    }
    return out
  }
  const info = (ctx.classMethods[type] || []).find((m) => m.name === name)
  for (const c of info?.calls || []) if (isType(c.type)) out.push([`${c.type}.${c.method}`, c.via === 'this' ? 'calls (self)' : 'calls'])
  for (const r of info?.sends || []) {
    const handler = ctx.handlerByRequest[r]?.handler
    if (handler) out.push([`${handler}.Handle`, `sends ${r}`])
  }
  return out
}

function methodIn(ctx, ix, key) {
  const dot = key.indexOf('.')
  const type = key.slice(0, dot)
  const name = key.slice(dot + 1)
  const callers = (ctx.callersOf[key] || []).map((c) => [`${c.class}.${c.method}`, 'calls'])
  // a handler's Handle is reached by whoever sends its request
  if (name === 'Handle') {
    for (const [request, h] of Object.entries(ctx.handlerByRequest)) {
      if (h.handler !== type) continue
      for (const sender of ix.sendersOf.get(request) || []) callers.push([sender, `sends ${request}`])
    }
  }
  return callers.filter(([k]) => isType(k))
}

// ── graph building ───────────────────────────────────────────────────────────

function typeNode(ctx, name, role, folders) {
  const info = ctx.types[name] || {}
  return {
    id: `t:${name}`,
    label: name,
    kind: info.kind || 'class',
    typeName: name,
    project: info.project,
    folder: folders[info.project] || undefined,
    namespace: namespaceOf(info),
    file: fileOf(info),
    line: info.line || null,
    role,
    bundle: info.project || 'external',
  }
}

function methodNode(ctx, key, role, folders) {
  const dot = key.indexOf('.')
  const type = key.slice(0, dot)
  const name = key.slice(dot + 1)
  const info = ctx.types[type] || {}
  const method = (ctx.classMethods[type] || []).find((m) => m.name === name)
  return {
    id: `m:${key}`,
    label: `${type}.${name}()`,
    kind: 'method',
    typeName: type,
    methodName: name,
    typeKind: info.kind || 'class',
    project: info.project,
    folder: folders[info.project] || undefined,
    namespace: namespaceOf(info),
    file: fileOf(info),
    line: method?.line || info.line || null,
    role,
    bundle: info.project || 'external',
  }
}

/**
 * Walk out from the seeds: downstream to `depth` hops, callers one hop up,
 * stopping at SCOPE_LIMIT nodes. Edges are every link among the nodes kept.
 */
function walk(seeds, outOf, inOf, { depth, callers, reached = 'uses' }) {
  const roles = new Map(seeds.map((s) => [s, 'scope']))
  const extra = [] // [from, to, kind] links found walking up
  let truncated = 0
  let frontier = [...seeds]
  for (let d = 0; d < depth && frontier.length; d++) {
    const next = []
    for (const a of frontier) {
      for (const [b] of outOf(a)) {
        if (roles.has(b)) continue
        if (roles.size >= SCOPE_LIMIT) {
          truncated++
          continue
        }
        roles.set(b, reached)
        next.push(b)
      }
    }
    frontier = next
  }
  let hiddenCallers = 0
  if (callers) {
    for (const s of seeds) {
      const users = inOf(s).filter(([u]) => !seeds.includes(u))
      hiddenCallers += Math.max(0, users.length - USERS_PER_NODE)
      for (const [u, kind] of users.slice(0, USERS_PER_NODE)) {
        if (!roles.has(u)) {
          if (roles.size >= SCOPE_LIMIT) {
            truncated++
            continue
          }
          roles.set(u, 'usedBy')
        }
        extra.push([u, s, kind])
      }
    }
  }
  const edges = new Map()
  const link = (a, b, kind) => {
    if (a === b || !roles.has(a) || !roles.has(b)) return
    const k = `${a}\u0000${b}`
    if (!edges.has(k)) edges.set(k, { source: a, target: b, kind })
  }
  for (const a of roles.keys()) for (const [b, kind] of outOf(a)) link(a, b, kind)
  for (const [a, b, kind] of extra) link(a, b, kind)
  return { roles, edges: [...edges.values()], truncated, hiddenCallers }
}

/**
 * @param {object} arch the /api/architecture payload (dotnet)
 * @param {object} ctx  the archCtx value
 * @param {{kind: string, key: string, endpoint?: object}} scope
 * @param {{depth?: number, callers?: boolean}} [opts]
 * @returns {{nodes: object[], edges: object[], focusId: string|null, truncated: number, hiddenCallers: number, level: 'class'|'method', seeds: number}}
 */
export function buildScopeGraph(arch, ctx, scope, { depth = 2, callers = true } = {}) {
  const folders = Object.fromEntries((arch?.graph?.nodes || []).map((n) => [n.id, n.folder]))
  const ix = indexes(ctx)
  const types = ctx.types

  if (scope.kind === 'method' || scope.kind === 'endpoint') {
    const endpoint = scope.endpoint
    const seedKey = endpoint ? (endpoint.handler ? `${endpoint.handler}.Handle` : null) : scope.key
    const seeds = seedKey ? [seedKey] : []
    const result = walk(
      seeds,
      (k) => methodOut(ctx, k),
      (k) => methodIn(ctx, ix, k),
      { depth, callers: callers && !endpoint, reached: 'flow' }
    )
    const nodes = [...result.roles].map(([k, role]) => ({ ...methodNode(ctx, k, role, folders), id: `m:${k}` }))
    const edges = result.edges.map((e) => ({ source: `m:${e.source}`, target: `m:${e.target}`, kind: e.kind }))
    let focusId = seedKey ? `m:${seedKey}` : null
    if (endpoint) {
      // the request's way in: route -> handler, drawn on top
      const id = `e:${endpoint.verb} ${endpoint.route}`
      nodes.unshift({
        id,
        label: `${endpoint.verb} ${endpoint.route}`,
        kind: 'endpoint',
        endpoint,
        project: endpoint.project,
        folder: folders[endpoint.project] || undefined,
        file: (endpoint.file || '').replace(/\\/g, '/'),
        role: 'scope',
        bundle: endpoint.project || 'api',
      })
      if (seedKey) edges.unshift({ source: id, target: `m:${seedKey}`, kind: `sends ${endpoint.request}` })
      focusId = id
    }
    return { nodes, edges, focusId, truncated: result.truncated, hiddenCallers: result.hiddenCallers, level: 'method', seeds: nodes.filter((n) => n.role === 'scope').length }
  }

  const all = Object.keys(types).filter(isType)
  const seeds =
    scope.kind === 'namespace'
      ? all.filter((t) => namespaceOf(types[t]) === scope.key && (!scope.project || types[t].project === scope.project))
      : scope.kind === 'file'
        ? all.filter((t) => fileOf(types[t]) === scope.key)
        : all.filter((t) => t === scope.key)
  const result = walk(
    seeds,
    (t) => [...(ix.classOut.get(t) || [])],
    (t) => [...(ix.classIn.get(t) || [])].sort().map((u) => [u, ix.classOut.get(u)?.get(t) || 'uses']),
    { depth, callers }
  )
  const nodes = [...result.roles].map(([t, role]) => typeNode(ctx, t, role, folders))
  const edges = result.edges.map((e) => ({ source: `t:${e.source}`, target: `t:${e.target}`, kind: e.kind }))
  return {
    nodes,
    edges,
    focusId: seeds.length === 1 ? `t:${seeds[0]}` : null,
    truncated: result.truncated,
    hiddenCallers: result.hiddenCallers,
    level: 'class',
    seeds: seeds.length,
  }
}

// ── the namespace -> file -> type tree of one project (the lists below) ─────

export function projectTree(ctx, projectId) {
  const namespaces = new Map()
  for (const [name, info] of Object.entries(ctx.types)) {
    if (info.project !== projectId || !isType(name)) continue
    const ns = namespaceOf(info)
    const file = fileOf(info)
    if (!namespaces.has(ns)) namespaces.set(ns, new Map())
    const files = namespaces.get(ns)
    if (!files.has(file)) files.set(file, [])
    files.get(file).push({ name, kind: info.kind, line: info.line || 0, methods: ctx.classMethods[name] || [] })
  }
  return [...namespaces]
    .map(([ns, files]) => ({
      namespace: ns,
      files: [...files]
        .map(([path, list]) => ({ path, types: list.sort((a, b) => a.line - b.line || a.name.localeCompare(b.name)) }))
        .sort((a, b) => a.path.localeCompare(b.path)),
    }))
    .map((ns) => ({ ...ns, typeCount: ns.files.reduce((n, f) => n + f.types.length, 0) }))
    .sort((a, b) => a.namespace.localeCompare(b.namespace))
}
