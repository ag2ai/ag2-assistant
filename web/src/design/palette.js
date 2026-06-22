/* ============================================================
   AG2 Assistant — Palette + theme switcher (ESM)
   ------------------------------------------------------------
   Adapted from the design-system's vanilla palette.js. Self-inits
   on import (reads localStorage, applies [data-palette]/[data-theme]
   on <html>, follows the OS while in 'auto'), exposes the same
   window.AG2Palette surface for the agent / console, and additionally
   exports the API as ES module functions for Svelte components.

   Persists to localStorage('ag2-palette' / 'ag2-theme') and fires
   'ag2-palette-change' / 'ag2-theme-change' CustomEvents on document.
   ============================================================ */
const ROOT = document.documentElement
const KEY = 'ag2-palette'
const THEME_KEY = 'ag2-theme'

export const PALETTES = [
  { id: 'teal', label: 'Teal', hex: '#109e91' },
  { id: 'coral', label: 'Coral', hex: '#f95339' },
  { id: 'ocean', label: 'Ocean', hex: '#2f6fe0' },
  { id: 'violet', label: 'Violet', hex: '#7a52ec' },
  { id: 'sage', label: 'Sage', hex: '#2f8c44' },
  { id: 'sunset', label: 'Sunset', hex: '#ec5d18' },
]
const IDS = PALETTES.map((p) => p.id)

export function setPalette(id) {
  if (IDS.indexOf(id) === -1) return false
  ROOT.setAttribute('data-palette', id)
  try { localStorage.setItem(KEY, id) } catch (e) {}
  document.dispatchEvent(new CustomEvent('ag2-palette-change', { detail: { palette: id } }))
  return true
}

export function getPalette() {
  return ROOT.getAttribute('data-palette') || 'teal'
}

const MQ = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null
let themeMode = 'auto' // tracks the user's choice; 'auto' follows the OS

// Resolve 'auto' against the OS preference and apply the concrete theme.
function applyResolvedTheme() {
  if (themeMode === 'light' || themeMode === 'dark') {
    ROOT.setAttribute('data-theme', themeMode)
  } else if (MQ && MQ.matches) {
    ROOT.setAttribute('data-theme', 'dark')
  } else {
    ROOT.removeAttribute('data-theme') // light is the default
  }
}

export function setTheme(mode) {
  // 'light' | 'dark' | 'auto'
  if (mode !== 'light' && mode !== 'dark' && mode !== 'auto') return false
  themeMode = mode
  applyResolvedTheme()
  try { localStorage.setItem(THEME_KEY, mode) } catch (e) {}
  document.dispatchEvent(new CustomEvent('ag2-theme-change', { detail: { theme: mode } }))
  return true
}

export function getTheme() {
  return themeMode
}

function init() {
  let saved, theme
  try { saved = localStorage.getItem(KEY) } catch (e) {}
  try { theme = localStorage.getItem(THEME_KEY) } catch (e) {}
  if (saved && IDS.indexOf(saved) !== -1) ROOT.setAttribute('data-palette', saved)
  else if (!ROOT.getAttribute('data-palette')) ROOT.setAttribute('data-palette', 'teal')
  themeMode = (theme === 'light' || theme === 'dark' || theme === 'auto') ? theme : 'auto'
  applyResolvedTheme()
  // Follow OS changes while in 'auto'.
  if (MQ) {
    const onChange = () => { if (themeMode === 'auto') applyResolvedTheme() }
    if (MQ.addEventListener) MQ.addEventListener('change', onChange)
    else if (MQ.addListener) MQ.addListener(onChange)
  }
}

init()

window.AG2Palette = { PALETTES, setPalette, getPalette, setTheme, getTheme }
