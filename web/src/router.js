import { writable, derived } from 'svelte/store'
import { getActiveProfileId } from './lib/profile.js'
import { parse, resolve, SETTINGS_PAGE } from './lib/route.js'
import { confirmDiscard } from './lib/unsavedGuard.js'

// Thin DOM/store shell over the pure route core (lib/route.js). The two dimensions:
// the PATH is the Page (profile + Tab + optional Thread); the HASH is the open
// Modal slot (`#settings=<section>`), client-side only and never sent to the
// gateway. `route` is the single source of truth; `settingsOpen`/the active
// Section are DERIVED off it (see store.js) — no store↔URL drift.

// SETTINGS_PAGE lives in the pure core (parse validates against it); re-export so
// existing `import { SETTINGS_PAGE } from './router.js'` sites (if any) keep working.
export { SETTINGS_PAGE }

function read() { return parse(location.pathname, location.hash) }
function current() { return { pathname: location.pathname, hash: location.hash } }

export const route = writable(read())

// Settings modal open/closed — DERIVED from the route (the open Modal lives in the
// URL hash, `#settings=<section>`), so the URL is the single source of truth and
// there's no store↔URL drift. Re-exported from store.js for consumers. Lives here,
// beside `route`, to avoid a store.js↔router.js module-init cycle (store.js only
// re-exports the binding; it never touches `route` at init).
export const settingsOpen = derived(route, ($r) => $r.overlay === 'settings')

// The "Powered by AG2" architecture map — the Modal slot's other occupant, so it
// gets the same deal as Settings: deep-linkable (`#poweredby`), and Back dismisses
// it. Opened from Settings it PUSHES over `#settings=…`, so Back lands you back in
// Settings, where you came from. Re-exported from store.js for consumers.
export const poweredByOpen = derived(route, ($r) => $r.overlay === 'poweredby')

// The pid segment for URLs: the one in the current path if any, else the active id.
function currentPid() {
  return read().pid || getActiveProfileId()
}

// go('/files') switches Tab and keeps the open Thread; go('/c/{id}') opens a
// Thread in the current Tab. The current hash (an open Modal) is preserved, so
// Page navigation never dismisses Settings. Pass a pid explicitly to switch profiles.
export function go(path, pid = currentPid()) {
  const full = resolve(current(), { type: 'go', path, pid })
  if (location.pathname + location.hash !== full) history.pushState({}, '', full)
  route.set(read())
}

// Navigate to a bare Tab, CLOSING the open Thread — go('/tasks') would keep it
// (that contract exists so Tab switches don't close your chat); deleting/leaving
// a thread needs the opposite: land on the Tab's own empty page, not back on the
// (now-gone) Thread. Preserves the hash, same as go().
export function goTab(tab, pid = currentPid()) {
  const full = resolve(current(), { type: 'goTab', tab, pid })
  if (location.pathname + location.hash !== full) history.pushState({}, '', full)
  route.set(read())
}

// Replace the current URL with /app/{pid}/ (used on boot to canonicalise a bare
// /app/ or a stale pid into the resolved profile). Preserves the hash so cold
// deep-links (`/app/#settings=…`) and the profile-switch reload survive. replaceState
// so the bare URL doesn't linger in history.
export function redirectToProfile(pid) {
  const full = resolve(current(), { type: 'redirectToProfile', pid })
  if (location.pathname + location.hash !== full) history.replaceState({}, '', full)
  route.set(read())
}

// ── Modal slot helpers (the hash) ────────────────────────────────────────────
// Each preserves the path and touches only the hash. Opening a Modal PUSHES a
// history entry (so Back dismisses it); switching Section and closing REPLACE (no
// history spam per Section click; close strips the hash to reveal the Page).

export function openOverlay(name, value) {
  const full = resolve(current(), { type: 'openOverlay', name, value })
  history.pushState({}, '', full)
  route.set(read())
}

export function replaceOverlay(name, value) {
  const full = resolve(current(), { type: 'replaceOverlay', name, value })
  history.replaceState({}, '', full)
  route.set(read())
}

export function closeOverlay() {
  const full = resolve(current(), { type: 'closeOverlay' })
  history.replaceState({}, '', full)
  route.set(read())
}

// ── Aside slot helpers (the right rail — ADR 0009) ───────────────────────────
// Each helper touches only the `aside` hash key and preserves the Modal key.
// Opening the rail from closed pushes (Back closes it); switching occupant replaces.

function setAside(next) {
  const cur = read().aside
  // Re-pointing the rail at the file it already shows isn't a teardown; any other
  // occupant change tears a dirty editor down, so guard it first.
  const sameFile = cur?.kind === 'file' && next?.kind === 'file' && cur.path === next.path
  if (!sameFile && !confirmDiscard()) return
  const wasOpen = cur != null
  const full = resolve(current(), { type: wasOpen ? 'replaceAside' : 'openAside', aside: next })
  if (wasOpen) history.replaceState({}, '', full)
  else history.pushState({}, '', full)
  route.set(read())
}

// Open a file preview in the rail. A blank path is a no-op (never closes the rail).
export function openAsideFile(path) { if (path) setAside({ kind: 'file', path }) }

// Open the AG2 Inspector as the rail occupant.
export function openAsideInspector() { setAside({ kind: 'inspector' }) }

export function closeAside() {
  if (!confirmDiscard()) return
  const full = resolve(current(), { type: 'closeAside' })
  history.replaceState({}, '', full)
  route.set(read())
}

// Toggle the Inspector as the rail occupant on/off.
export function toggleAsideInspector() {
  if (read().aside?.kind === 'inspector') closeAside()
  else openAsideInspector()
}

// Whether the AG2 Inspector occupies the rail; also gates the per-item provenance
// tags. Derived from the route, re-exported from store.js as `ag2View`.
export const ag2View = derived(route, ($r) => $r.aside?.kind === 'inspector')

export function newChatId() {
  return 'web-' + Math.random().toString(36).slice(2, 10)
}

// popstate covers Back/Forward (Page and Modal); hashchange covers a hash edited
// directly in the address bar. Both re-derive the route from the URL.
// Known gap: Back past an open dirty editor is intentionally un-guarded (ticket 04).
window.addEventListener('popstate', () => route.set(read()))
window.addEventListener('hashchange', () => route.set(read()))
