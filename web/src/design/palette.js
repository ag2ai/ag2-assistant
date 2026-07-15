/* ============================================================
   AG2 Assistant — Accent + theme switcher (ESM)
   ------------------------------------------------------------
   A profile's *Accent* is one opaque #rrggbb hex (ADR 0002). This
   module is the only place that knows how a hex becomes an applied
   theme:

     • a hex equal to a PRESET's --p-500 → activate that preset's
       hand-tuned [data-palette="<id>"] block (palettes.css);
     • any other hex → data-palette="custom" + a 10-step ramp
       derived here and written as inline --p-* vars on <html>
       (inline styles outrank the stylesheet blocks).

   "Palette" now means only the frontend's preset catalogue + its
   ramps — NOT the domain concept, which is the Accent (one colour).

   Self-inits on import (reads localStorage, applies the accent +
   [data-theme] on <html>, follows the OS while theme='auto').
   Persists to localStorage('ag2-accent' / 'ag2-theme') and fires
   'ag2-accent-change' / 'ag2-theme-change' CustomEvents on document.
   ============================================================ */
const ROOT = document.documentElement
const KEY = 'ag2-accent'
const THEME_KEY = 'ag2-theme'

// The frontend's preset catalogue. `hex` is each preset's --p-500 (the applied
// accent); the full hand-tuned ramp lives in design/tokens/palettes.css.
export const PALETTES = [
  { id: 'teal', label: 'Teal', hex: '#109e91' },
  { id: 'coral', label: 'Coral', hex: '#f95339' },
  { id: 'ocean', label: 'Ocean', hex: '#2f6fe0' },
  { id: 'violet', label: 'Violet', hex: '#7a52ec' },
  { id: 'sage', label: 'Sage', hex: '#2f8c44' },
  { id: 'sunset', label: 'Sunset', hex: '#ec5d18' },
]
// hex → preset id, for the match-by-hex fast path.
const PRESET_BY_HEX = new Map(PALETTES.map((p) => [p.hex.toLowerCase(), p.id]))
export const DEFAULT_ACCENT = PALETTES[0].hex // teal

// The 10 ramp stops, and how each is mixed from the picked colour (the picked
// hex IS the 500 stop). Light stops mix toward white, dark stops toward black;
// the fractions roughly mirror the hand-tuned preset ramps.
const RAMP = [
  ['--p-50', 0.92],
  ['--p-100', 0.82],
  ['--p-200', 0.62],
  ['--p-300', 0.40],
  ['--p-400', 0.18],
  ['--p-500', 0.0],
  ['--p-600', -0.16],
  ['--p-700', -0.34],
  ['--p-800', -0.50],
  ['--p-900', -0.66],
]

function normHex(value) {
  if (typeof value !== 'string') return null
  const s = value.trim().toLowerCase()
  return /^#[0-9a-f]{6}$/.test(s) ? s : null
}

function toRgb(hex) {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ]
}
function toHex(r, g, b) {
  const h = (n) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0')
  return `#${h(r)}${h(g)}${h(b)}`
}
// mix > 0 → toward white by that fraction; mix < 0 → toward black by |mix|.
function mixChannel(c, mix) {
  return mix >= 0 ? c + (255 - c) * mix : c * (1 + mix)
}

// Derive the full { '--p-50': '#..', … '--p-900': '#..' } ramp from one hex.
function deriveRamp(hex) {
  const [r, g, b] = toRgb(hex)
  const out = {}
  for (const [stop, mix] of RAMP) {
    out[stop] = mix === 0 ? hex : toHex(mixChannel(r, mix), mixChannel(g, mix), mixChannel(b, mix))
  }
  return out
}

let _accent = DEFAULT_ACCENT // current applied accent hex

function applyAccent(hex) {
  const preset = PRESET_BY_HEX.get(hex)
  if (preset) {
    // Preset colour: use its hand-tuned block; drop any custom inline ramp.
    for (const [stop] of RAMP) ROOT.style.removeProperty(stop)
    ROOT.setAttribute('data-palette', preset)
  } else {
    // Custom colour: inline the derived ramp (wins over the stylesheet blocks).
    const ramp = deriveRamp(hex)
    for (const [stop] of RAMP) ROOT.style.setProperty(stop, ramp[stop])
    ROOT.setAttribute('data-palette', 'custom')
  }
}

export function setAccent(value) {
  const hex = normHex(value) || DEFAULT_ACCENT
  _accent = hex
  applyAccent(hex)
  try { localStorage.setItem(KEY, hex) } catch (e) {}
  document.dispatchEvent(new CustomEvent('ag2-accent-change', { detail: { accent: hex } }))
  return true
}

export function getAccent() {
  return _accent
}

// The preset id whose --p-500 equals this hex, or null for a custom colour.
// Handy for labelling ("Teal") without re-implementing the match.
export function presetIdForAccent(value) {
  const hex = normHex(value)
  return hex ? PRESET_BY_HEX.get(hex) || null : null
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
  // localStorage is only a flash-avoiding *hint*; App.svelte corrects it from the
  // active profile's registry accent on boot.
  _accent = normHex(saved) || DEFAULT_ACCENT
  applyAccent(_accent)
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

// Console / agent surface. `setAccent` accepts any #rrggbb hex.
window.AG2Accent = { PALETTES, setAccent, getAccent, presetIdForAccent, setTheme, getTheme }
