// Syntax colors for the diff reader: the VS Code Dark+ theme over VS Code's
// own TextMate grammars (Shiki), so classes, namespaces, methods, keywords
// and strings read the way they do in the editor. Shiki and each grammar load
// on first use (their own chunks), and the JavaScript regex engine avoids
// shipping Oniguruma's wasm.

export const THEME = 'dark-plus'
export const FOREGROUND = '#D4D4D4'

// file extension (or exact name) -> grammar
const BY_EXT = {
  cs: 'csharp',
  csx: 'csharp',
  razor: 'razor',
  cshtml: 'razor',
  ts: 'typescript',
  mts: 'typescript',
  cts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'jsx',
  vue: 'vue',
  py: 'python',
  pyi: 'python',
  json: 'json',
  jsonc: 'jsonc',
  md: 'markdown',
  css: 'css',
  scss: 'scss',
  less: 'less',
  html: 'html',
  htm: 'html',
  xml: 'xml',
  csproj: 'xml',
  props: 'xml',
  targets: 'xml',
  resx: 'xml',
  xaml: 'xml',
  config: 'xml',
  svg: 'xml',
  yml: 'yaml',
  yaml: 'yaml',
  toml: 'toml',
  ps1: 'powershell',
  psm1: 'powershell',
  psd1: 'powershell',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  sql: 'sql',
  ini: 'ini',
  bicep: 'bicep',
  tf: 'terraform',
  dockerfile: 'docker',
}
const BY_NAME = { dockerfile: 'docker', makefile: 'make', '.gitignore': 'shellscript', '.editorconfig': 'ini' }

const LOADERS = {
  csharp: () => import('@shikijs/langs/csharp'),
  razor: () => import('@shikijs/langs/razor'),
  typescript: () => import('@shikijs/langs/typescript'),
  tsx: () => import('@shikijs/langs/tsx'),
  javascript: () => import('@shikijs/langs/javascript'),
  jsx: () => import('@shikijs/langs/jsx'),
  vue: () => import('@shikijs/langs/vue'),
  python: () => import('@shikijs/langs/python'),
  json: () => import('@shikijs/langs/json'),
  jsonc: () => import('@shikijs/langs/jsonc'),
  markdown: () => import('@shikijs/langs/markdown'),
  css: () => import('@shikijs/langs/css'),
  scss: () => import('@shikijs/langs/scss'),
  less: () => import('@shikijs/langs/less'),
  html: () => import('@shikijs/langs/html'),
  xml: () => import('@shikijs/langs/xml'),
  yaml: () => import('@shikijs/langs/yaml'),
  toml: () => import('@shikijs/langs/toml'),
  powershell: () => import('@shikijs/langs/powershell'),
  bash: () => import('@shikijs/langs/bash'),
  shellscript: () => import('@shikijs/langs/shellscript'),
  sql: () => import('@shikijs/langs/sql'),
  ini: () => import('@shikijs/langs/ini'),
  bicep: () => import('@shikijs/langs/bicep'),
  terraform: () => import('@shikijs/langs/terraform'),
  docker: () => import('@shikijs/langs/docker'),
  make: () => import('@shikijs/langs/make'),
}

// more than this per side and a hunk stays plain - a generated file isn't read
const MAX_LINES = 6000

export function languageOf(path) {
  if (!path) return null
  const name = path.split(/[\\/]/).pop().toLowerCase()
  if (BY_NAME[name]) return BY_NAME[name]
  const dot = name.lastIndexOf('.')
  if (dot < 0) return null
  return BY_EXT[name.slice(dot + 1)] || null
}

let highlighter = null
const loaded = new Map() // lang -> Promise<boolean>

async function core() {
  highlighter ||= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
      import('shiki/core'),
      import('shiki/engine/javascript'),
    ])
    return createHighlighterCore({
      themes: [import('@shikijs/themes/dark-plus')],
      langs: [],
      engine: createJavaScriptRegexEngine({ forgiving: true }),
    })
  })()
  return highlighter
}

async function ensure(lang) {
  if (!LOADERS[lang]) return false
  if (!loaded.has(lang)) {
    loaded.set(
      lang,
      core()
        .then((hl) => hl.loadLanguage(LOADERS[lang]()))
        .then(() => true)
        .catch((err) => {
          console.warn('[highlight] no grammar for', lang, err)
          return false
        })
    )
  }
  return loaded.get(lang)
}

/**
 * Tokens for a block of code, one array per line: [{ content, color, fontStyle }].
 * Null when the language has no grammar here (the caller renders plain text).
 */
export async function tokenize(code, lang) {
  if (!lang || !(await ensure(lang))) return null
  if (code.split('\n').length > MAX_LINES) return null
  const hl = await core()
  try {
    return hl.codeToTokensBase(code, { lang, theme: THEME }).map((line) =>
      line.map((t) => ({ content: t.content, color: t.color, fontStyle: t.fontStyle || 0 }))
    )
  } catch (err) {
    console.warn('[highlight] failed', lang, err)
    return null
  }
}
