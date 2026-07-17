import { writable } from 'svelte/store'

// Multi-profile registry (§5.2). `list` mirrors GET /api/profiles; `activeId`
// is the profile the client is currently viewing (persisted separately in
// localStorage via lib/profile.js). Minimal in Phase 1 — Phase 2 builds the
// switcher chips + activity badges on top of this.
export const profiles = writable({ list: [], activeId: null })

// The active thread: a projection of one AG2 stream. `items` are folded from
// `{type,data}` events (see project.js). `kind` is 'chat' or 'task'.
export const thread = writable({ id: null, kind: 'chat', items: [], busy: false })

// Drawer: unified history of chats + tasks, plus the user-writable Files tree.
export const chats = writable([])
export const tasks = writable([])
export const drawerTab = writable('chats') // 'chats' | 'tasks' | 'files'

// Current task's durable panel data (tree/schedule/deliverables), when kind==='task'.
export const taskPanel = writable(null)

// Durable HITL: pending questions/permissions across all tasks, answerable
// anywhere (polled). Survives restarts — backed by the InquiryStore.
export const inquiries = writable([])

// Google connect modal open/closed.
export const googleOpen = writable(false)

// "Sign in with ChatGPT" (OpenAI Codex subscription) modal open/closed.
export const codexOpen = writable(false)

// Voice picker modal open/closed.
export const voicePickerOpen = writable(false)
// When the voice picker targets a specific named live config, its id (else null for
// the profile's legacy voice setting). Set alongside voicePickerOpen; the picker
// scopes its voices/select/preview to this config and stacks over Settings.
export const voicePickerConfig = writable(null)

// Deliverable full-view modal: { title, text } when open, null when closed.
export const viewer = writable(null)

// Settings modal open/closed (launches voice picker + Google from one place).
export const settingsOpen = writable(false)

// Memory viewer/editor modal open/closed.
export const memoryOpen = writable(false)

// The valid Settings page ids — the single source of truth for what settingsPage
// may hold. Settings.svelte binds each id to a label + component; callers deep-link
// with SETTINGS_PAGE.* so a bad id is impossible to mistype (no more 'model' vs
// 'models' drift). Frozen so the vocabulary can't be mutated at runtime.
export const SETTINGS_PAGE = Object.freeze({
  GENERAL: 'general',
  PROFILES: 'profiles',
  MODELS: 'models',
  SECRETS: 'secrets',
  FOLDERS: 'folders',
  TOOLS: 'tools',
  INTEGRATIONS: 'integrations',
  ADVANCED: 'advanced',
})

// Which Settings page is shown when the modal opens: one of the SETTINGS_PAGE ids.
// Lets callers deep-link into a page (e.g. settingsPage.set(SETTINGS_PAGE.TOOLS); settingsOpen.set(true)).
// Settings seeds its local `page` from this on mount (validated) and writes it back on nav click.
export const settingsPage = writable(SETTINGS_PAGE.GENERAL)

// "Powered by AG2" architecture-map modal open/closed.
export const poweredByOpen = writable(false)

// App version, seeded from the GET /api/profiles boot payload. Shown in the
// "Powered by AG2" modal footer. Empty until boot completes.
export const appVersion = writable('')

// A bounded buffer of the raw {type,data} events the current chat's stream
// already delivers — the AG2 Inspector renders it to show the live AG2 events
// behind the UI. Reset when a thread opens (see controller.openThread).
export const inspectorEvents = writable([])

// A localStorage-backed preference (per-device): survives reloads.
function persisted(key, initial) {
  let v = initial
  try { const s = localStorage.getItem(key); if (s !== null) v = JSON.parse(s) } catch {}
  const w = writable(v)
  w.subscribe((val) => { try { localStorage.setItem(key, JSON.stringify(val)) } catch {} })
  return w
}

// Play a chime when the assistant needs your input (HITL). Off by default.
export const soundOnInput = persisted('soundOnInput', false)

// "AG2 view" — reveal where AG2 powers things: opens the live event inspector and
// adds per-item provenance tags. A deliberate demo mode. Off by default.
export const ag2View = persisted('ag2View', false)

// App-wide animation quality (per-device — the GPU cost is local). Any rich
// surface should honour it; weather panels are the first consumer:
//   'off'   — static content only (weather: emoji glyph, pure HTML), zero GPU
//   'basic' — simple CSS/SVG animation, compositor-cheap (default: kind to GPUs;
//             High is an explicit opt-in from Settings)
//   'high'  — full WebGPU 3D scenes (volumetrics, bloom); consumers fall back
//             to 'basic' on browsers without WebGPU
export const animations = persisted('animations', 'basic')

// First-run onboarding overlay open/closed. Opened automatically on first launch
// when this install hasn't completed/dismissed it and no provider key is stored
// (see App.svelte), or via Settings → "Re-run setup". The install-level "have we
// onboarded?" flag lives in the registry (GET /api/profiles `onboarded`, §4.2) —
// set via api.setOnboarded() at the END of the onboarding flow (§5.5).
export const onboardingOpen = writable(false)

// Local user display name seeded by onboarding — greets the user. Kept on-device
// (per-profile focus areas moved server-side into each profile's settings.json,
// where they're injected into the agent's context).
export const profile = persisted('ag2-profile', { name: '' })

// Transient toast/notice: { text } when shown, null when hidden. Used by the
// archived-profile recovery flow (§4.9) — a brief message before the client
// re-resolves to a valid profile. Minimal by design; no queue.
export const notice = writable(null)
