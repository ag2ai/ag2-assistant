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

// The two CustomEvents this module dispatches, declared where they are fired so
// every listener reads `e.detail` typed instead of casting the Event.
declare global {
  interface DocumentEventMap {
    'ag2-accent-change': CustomEvent<{ accent: string }>
    'ag2-theme-change': CustomEvent<{ theme: ThemeMode }>
  }
}

// The frontend's preset catalogue. `hex` is each preset's --p-500 (the applied
// accent); the full hand-tuned ramp lives in design/tokens/palettes.css.
export type Palette = { id: string; label: string; hex: string }

// Order is the swatch order in the picker, and PALETTES[0] is what an unpicked
// profile lands on — green leads so the row doesn't open on a run of blues.
export const PALETTES: Palette[] = [
  { id: 'forest-green', label: 'Forest Green', hex: '#166534' },
  { id: 'navy-blue', label: 'Navy Blue', hex: '#1d4ed8' },
  { id: 'royal-blue', label: 'Royal Blue', hex: '#1e40af' },
  { id: 'dark-indigo', label: 'Dark Indigo', hex: '#4338ca' },
  { id: 'deep-purple', label: 'Deep Purple', hex: '#5b21b6' },
  { id: 'burgundy', label: 'Burgundy', hex: '#9f1239' },
]
// hex → preset id, for the match-by-hex fast path.
const PRESET_BY_HEX = new Map(PALETTES.map((p) => [p.hex.toLowerCase(), p.id]))
export const DEFAULT_ACCENT = PALETTES[0].hex // forest green

// The 10 ramp stops, and how each is mixed from the picked colour (the picked
// hex IS the 500 stop). Light stops mix toward white, dark stops toward black;
// the fractions roughly mirror the hand-tuned preset ramps.
const RAMP: [stop: string, mix: number][] = [
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

function normHex(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const s = value.trim().toLowerCase()
  return /^#[0-9a-f]{6}$/.test(s) ? s : null
}

function toRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ]
}
function toHex(r: number, g: number, b: number): string {
  const h = (n: number) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0')
  return `#${h(r)}${h(g)}${h(b)}`
}
// mix > 0 → toward white by that fraction; mix < 0 → toward black by |mix|.
function mixChannel(c: number, mix: number): number {
  return mix >= 0 ? c + (255 - c) * mix : c * (1 + mix)
}

// Derive the full { '--p-50': '#..', … '--p-900': '#..' } ramp from one hex.
function deriveRamp(hex: string): Record<string, string> {
  const [r, g, b] = toRgb(hex)
  const out: Record<string, string> = {}
  for (const [stop, mix] of RAMP) {
    out[stop] = mix === 0 ? hex : toHex(mixChannel(r, mix), mixChannel(g, mix), mixChannel(b, mix))
  }
  return out
}

// Relative luminance (sRGB) of a hex, 0..1 — the "is this colour light?" signal
// behind the adaptive ink below.
function luminance(hex: string): number {
  const [r, g, b] = toRgb(hex).map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

let _accent = DEFAULT_ACCENT // current applied accent hex

function applyAccent(hex: string): void {
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
  // Adaptive ink on accent fills (send button, active profile chip, unread badge):
  // the accent is user-picked and can be ANY colour — white text on a white or
  // yellow accent would vanish. Light theme applies --p-500 and dark applies
  // --p-400, so white ink is safe only when BOTH are dark enough.
  const ramp = deriveRamp(hex)
  const light = Math.max(luminance(hex), luminance(ramp['--p-400'])) >= 0.45
  ROOT.style.setProperty('--text-on-accent', light ? '#1d1a16' : '#ffffff')
}

export function setAccent(value: unknown): boolean {
  const hex = normHex(value) || DEFAULT_ACCENT
  _accent = hex
  applyAccent(hex)
  try { localStorage.setItem(KEY, hex) } catch {}
  document.dispatchEvent(new CustomEvent('ag2-accent-change', { detail: { accent: hex } }))
  return true
}

export function getAccent(): string {
  return _accent
}

// Readable ink for text/glyphs sitting ON a given accent hex (profile chips fill
// with their own profile colour, not the applied accent, so they can't use the
// global --text-on-accent). Same luminance rule as applyAccent.
export function inkOn(value: unknown): string {
  const hex = normHex(value) || DEFAULT_ACCENT
  return luminance(hex) >= 0.45 ? '#1d1a16' : '#ffffff'
}

// The preset id whose --p-500 equals this hex, or null for a custom colour.
// Handy for labelling ("Navy Blue") without re-implementing the match.
export function presetIdForAccent(value: unknown): string | null {
  const hex = normHex(value)
  return hex ? PRESET_BY_HEX.get(hex) || null : null
}

export type ThemeMode = 'light' | 'dark' | 'auto'

const MQ = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null
let themeMode: ThemeMode = 'auto' // tracks the user's choice; 'auto' follows the OS

// Resolve 'auto' against the OS preference and apply the concrete theme.
function applyResolvedTheme(): void {
  if (themeMode === 'light' || themeMode === 'dark') {
    ROOT.setAttribute('data-theme', themeMode)
  } else if (MQ && MQ.matches) {
    ROOT.setAttribute('data-theme', 'dark')
  } else {
    ROOT.removeAttribute('data-theme') // light is the default
  }
}

export function setTheme(mode: unknown): boolean {
  // 'light' | 'dark' | 'auto'
  if (mode !== 'light' && mode !== 'dark' && mode !== 'auto') return false
  themeMode = mode
  applyResolvedTheme()
  try { localStorage.setItem(THEME_KEY, mode) } catch {}
  document.dispatchEvent(new CustomEvent('ag2-theme-change', { detail: { theme: mode } }))
  return true
}

export function getTheme(): ThemeMode {
  return themeMode
}

function init(): void {
  let saved: string | null = null
  let theme: string | null = null
  try { saved = localStorage.getItem(KEY) } catch {}
  try { theme = localStorage.getItem(THEME_KEY) } catch {}
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
const surface = { PALETTES, setAccent, getAccent, presetIdForAccent, setTheme, getTheme }

declare global {
  interface Window { AG2Accent: typeof surface }
}

window.AG2Accent = surface
