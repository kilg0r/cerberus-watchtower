// What kind of work a process or tool call is - the Processes view's color
// key. Seven identities in a fixed order (validated as a set on the panel
// surface #111f33 with the dataviz palette checker: adjacent CVD ΔE >= 8.4,
// normal-vision >= 19.3, all >= 3:1 contrast) plus neutral "tool". Color is
// never the only cue: every chip also carries its label.
export const CATEGORIES = [
  { key: 'script', label: 'Script', color: '#3987e5', hint: 'python, node, scripts' },
  { key: 'build', label: 'Build', color: '#d95926', hint: 'dotnet build, vite build, compilers' },
  { key: 'test', label: 'Test', color: '#199e70', hint: 'dotnet test, pytest, vitest, mutants' },
  { key: 'shell', label: 'Shell', color: '#c98500', hint: 'commands, git, installs' },
  { key: 'network', label: 'Network', color: '#d55181', hint: 'web, MCP, ssh, CI, docker' },
  { key: 'service', label: 'Service', color: '#0ca30c', hint: 'servers, watchers, monitors' },
  { key: 'agent', label: 'Agent', color: '#9085e9', hint: 'subagents, forks, workflows' },
  { key: 'tool', label: 'Tool', color: '#64748b', hint: 'file reads, edits, search' },
]

// status, not identity: shown with an icon + text, never as a series color
export const WAITING = { key: 'waiting', label: 'Needs you', color: '#fab219' }

const BY_KEY = Object.fromEntries([...CATEGORIES, WAITING].map((c) => [c.key, c]))

export const categoryOf = (key) => BY_KEY[key] || BY_KEY.tool
export const colorOf = (key) => categoryOf(key).color

// Where the RAM goes. Hues follow the key above where the meaning carries over
// (the claude process runs the agents, MCP servers are services); validated
// in this order on #111f33: adjacent CVD ΔE >= 18, normal-vision >= 18.6.
// "Other apps" is a deliberate neutral below 3:1, so its amount is always
// printed as text.
export const MEMORY_PARTS = [
  { key: 'cli', label: 'Claude', color: '#9085e9', hint: 'the claude processes themselves' },
  { key: 'mcp', label: 'MCP servers', color: '#0ca30c', hint: 'MCP servers and helpers each session keeps alive' },
  { key: 'tools', label: 'Tool processes', color: '#3987e5', hint: 'shells and everything tool calls started' },
  { key: 'other', label: 'Other apps', color: '#5c6370', hint: 'everything else on the machine' },
]

// The activity timeline: emphasis form - activity in grays, "needs you" in
// the status color (and drawn taller, so it never relies on color alone).
export const ACTIVITY = {
  w: { label: 'Working', color: '#cbd5e1', tall: false },
  b: { label: 'Background', color: '#64748b', tall: false, hint: 'turn ended, agents or tasks still running' },
  '!': { label: 'Needs you', color: WAITING.color, tall: true },
}
