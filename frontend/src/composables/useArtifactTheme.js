import { ref, watch } from 'vue'

// Light / dark for artifact previews (and the "Open in Chrome" page copy).
// Auto follows the system, like claude.ai; light and dark are forced by the
// server (/api/artifacts/{id}/view/{theme}/...). One app-wide choice,
// remembered across sessions.
export const ARTIFACT_THEMES = [
  { value: 'auto', label: 'Auto', hint: 'Follow the system theme' },
  { value: 'light', label: 'Light', hint: 'Force light mode' },
  { value: 'dark', label: 'Dark', hint: 'Force dark mode' },
]

const KEY = 'watchtower:artifacts:theme'
const saved = localStorage.getItem(KEY)
const theme = ref(ARTIFACT_THEMES.some((t) => t.value === saved) ? saved : 'auto')
watch(theme, (value) => localStorage.setItem(KEY, value))

export function useArtifactTheme() {
  return theme
}
