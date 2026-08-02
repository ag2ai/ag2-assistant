// Settings — shared reactive context (Svelte 5 runes).
//
// ONE `$state` object holds every field the Settings pages share
// ({ s, google, drafts, busy, err }) plus the methods that act on it
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
// 3. Deep binds work: `bind:value={ctx.drafts[k.id]}` — the proxy tracks nested mutation.
// 4. createSettingsContext() MUST run synchronously in the shell's <script> top
//    (setContext requirement); ctx.load() is called from the shell's onMount.
// 5. Page-local state (open FolderPicker, MCP health) resets on page switch because
//    pages unmount; anything that must survive a switch lives on ctx (drafts).

import { get } from 'svelte/store'
import { getContext, setContext } from 'svelte'
import { api } from '../../transport/api/index.ts'
import { closeOverlay } from '../../router.ts'
import {
  voicePickerOpen, voicePickerConfig, googleOpen, codexOpen,
  poweredByOpen, onboardingOpen, profileEpoch,
} from '../../store.ts'

const KEY = Symbol('settings')

export function createSettingsContext() {
  const ctx = $state({
    s: null,        // GET /api/settings payload (null until load() resolves)
    google: null,   // GET /api/google/status
    drafts: {},     // provider -> input value (API-key drafts)
    busy: false,
    err: '',
  })

  // Fetch the whole Settings payload. Resets `drafts` so a fresh load starts clean
  // (key rows bind lazily into ctx.drafts[provider]). The epoch guard drops a load
  // that resolves after a profile switch.
  ctx.load = async () => {
    const epoch = get(profileEpoch)
    try {
      const s = await api.settings()
      if (get(profileEpoch) !== epoch) return
      ctx.s = s
      ctx.drafts = {}
    } catch (e) { ctx.err = String(e.message || e) }
    // google status stays fetched here so Integrations shows it ready when opened.
    try { const g = await api.googleStatus(); if (get(profileEpoch) === epoch) ctx.google = g } catch {}
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
  // Close Settings by stripping the hash — the URL is the source of truth; Back,
  // Esc, and × all funnel through here. Returns to the exact Page underneath.
  ctx.close = () => closeOverlay()
  // "Change voice" for a named live config: stack the picker OVER Settings (like
  // openCodex) scoped to that config, so closing it returns to the Live list with the
  // config's new voice — Settings is never torn down and the list stays put.
  ctx.openVoice = (configId = null) => { voicePickerConfig.set(configId); voicePickerOpen.set(true) }
  ctx.openGoogle = () => { closeOverlay(); googleOpen.set(true) }
  // Codex is the ONE opener that does NOT close Settings: it's launched from the
  // half-filled LLM config form, and unmounting Settings would throw that draft
  // away. It stacks over Settings (.modal.over) and closing it reveals the form
  // again, with its signed-in state refreshed.
  ctx.openCodex = () => codexOpen.set(true)
  ctx.openPoweredBy = () => { closeOverlay(); poweredByOpen.set(true) }
  ctx.reRunSetup = () => { closeOverlay(); onboardingOpen.set(true) }

  setContext(KEY, ctx)
  return ctx
}

export function getSettings() {
  return getContext(KEY)
}
