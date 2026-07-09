// Settings — shared reactive context (Svelte 5 runes).
//
// ONE `$state` object holds every field the six Settings pages share
// ({ s, google, drafts, model, busy, err }) plus the methods that act on it
// (load, run) and the cross-modal openers. The shell calls createSettingsContext()
// synchronously at init and setContext()s it; each page reads it with getSettings().
//
// Why one $state object (not props, not a module singleton):
//   • props — 4 of 6 pages would each need 6+ bindables; painful and error-prone.
//   • module singleton — would survive modal close with stale data.
// A fresh context per Settings mount is created here and dropped on unmount.
//
// ── Reactivity rules (follow these when touching pages) ─────────────────────
// 1. `ctx.s = await api.settings()` reassigns a property on the $state proxy →
//    the new object is deep-proxied and EVERY page reading ctx.s re-renders.
//    This is WHY the shared data lives on one $state object, not loose rune vars
//    (a plain `let s` reassigned inside this module wouldn't reach the pages).
// 2. NEVER destructure at init: `const { s } = getSettings()` captures the null
//    value forever. Pages dot-access `ctx.s` / `ctx.busy`, or alias via $derived.
// 3. Deep binds work: `bind:value={ctx.drafts[k.id]}`, `bind:value={ctx.s.assistant.provider}`,
//    `bind:value={ctx.model}` — the proxy tracks nested mutation.
// 4. createSettingsContext() MUST run synchronously in the shell's <script> top
//    (setContext requirement); ctx.load() is called from the shell's onMount.
// 5. Page-local state (open FolderPicker, MCP health) resets on page switch because
//    pages unmount; anything that must survive a switch lives on ctx (drafts, model).

import { getContext, setContext } from 'svelte'
import { api } from '../../transport/api.js'
import {
  settingsOpen, voicePickerOpen, googleOpen, memoryOpen, poweredByOpen, onboardingOpen,
} from '../../store.js'

const KEY = Symbol('settings')

export function createSettingsContext() {
  const ctx = $state({
    s: null,        // GET /api/settings payload (null until load() resolves)
    google: null,   // GET /api/google/status
    drafts: {},     // provider -> input value (API-key + ollama drafts)
    model: '',      // assistant model draft
    busy: false,
    err: '',
  })

  // Fetch the whole Settings payload. Mirrors the old Settings.svelte load():
  // resets `drafts` to just the ollama base_url so a fresh load starts clean.
  ctx.load = async () => {
    try {
      ctx.s = await api.settings()
      ctx.model = ctx.s.assistant.model || ''
      ctx.drafts = { ollama: ctx.s.keys.ollama?.base_url || '' }
    } catch (e) { ctx.err = String(e.message || e) }
    // google status stays fetched here so Integrations shows it ready when opened.
    try { ctx.google = await api.googleStatus() } catch {}
  }

  // Run a mutation, then re-fetch the whole payload on success — identical to the
  // old run(): clear err, flag busy, await fn, reload, unflag.
  ctx.run = async (fn) => {
    ctx.err = ''; ctx.busy = true
    try { await fn(); await ctx.load() } catch (e) { ctx.err = String(e.message || e) }
    ctx.busy = false
  }

  // Cross-modal openers — each closes Settings then opens the target modal,
  // exactly the old Settings.svelte behaviour (close settings store, open target).
  ctx.close = () => settingsOpen.set(false)
  ctx.openVoice = () => { settingsOpen.set(false); voicePickerOpen.set(true) }
  ctx.openGoogle = () => { settingsOpen.set(false); googleOpen.set(true) }
  ctx.openMemory = () => { settingsOpen.set(false); memoryOpen.set(true) }
  ctx.openPoweredBy = () => { settingsOpen.set(false); poweredByOpen.set(true) }
  ctx.reRunSetup = () => { settingsOpen.set(false); onboardingOpen.set(true) }

  setContext(KEY, ctx)
  return ctx
}

export function getSettings() {
  return getContext(KEY)
}
