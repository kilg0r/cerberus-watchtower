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
