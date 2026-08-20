// Wires the UI language (see lib/locale.ts) into the app: resolves it once at
// boot, hands it to the Paraglide runtime, keeps <html lang> in sync, and exposes
// the store App.svelte re-keys the tree on — message functions are plain calls, so
// a locale switch re-renders by remounting, not by per-message reactivity.
import { writable } from 'svelte/store'

import { overwriteGetLocale } from '../paraglide/runtime.js'
import { resolveUiLocale, type UiLocale } from './locale.ts'

const STORAGE_KEY = 'ag2-lang'

function storedChoice(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function browserLanguages(): readonly string[] {
  if (typeof navigator === 'undefined') return []
  return navigator.languages ?? (navigator.language ? [navigator.language] : [])
}

let current: UiLocale = resolveUiLocale(storedChoice(), browserLanguages())

// Message functions read the locale synchronously through this hook.
overwriteGetLocale(() => current)

export const uiLocale = writable<UiLocale>(current)
uiLocale.subscribe((locale) => {
  current = locale
  if (typeof document !== 'undefined') document.documentElement.lang = locale
})

// An explicit user pick — the only writer of the per-device preference. Detection
// never writes it, so a device with no pick keeps following the browser language.
export function setUiLocale(locale: UiLocale): void {
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // localStorage unavailable: the pick still applies for this session
  }
  uiLocale.set(locale)
}
