import { writable, derived, type Readable, type Writable } from 'svelte/store'
import { getActiveProfileId } from './lib/profile.ts'
import { parse, resolve, SETTINGS_PAGE } from './lib/route.ts'
import type { Tab } from './lib/route.ts'
import { confirmDiscard } from './lib/unsavedGuard.ts'

// Thin DOM/store shell over the pure route core (lib/route.js). The two dimensions:
// the PATH is the Page (profile + Tab + optional Thread); the HASH is the open
// Modal slot (`#settings=<section>`), client-side only and never sent to the
// gateway. `route` is the single source of truth; `settingsOpen`/the active
// Section are DERIVED off it (see store.ts) — no store↔URL drift.

// SETTINGS_PAGE lives in the pure core (parse validates against it); re-export so
// existing `import { SETTINGS_PAGE } from './router.ts'` sites (if any) keep working.
export { SETTINGS_PAGE }

// The route shape is the pure core's own — inferred from lib/route.js until task 17
// converts it, so the two can never drift.
export type Route = ReturnType<typeof parse>
export type SettingsPage = (typeof SETTINGS_PAGE)[keyof typeof SETTINGS_PAGE]

// The right-rail occupant the `aside` hash key addresses.
export type Aside = { kind: 'file'; path: string } | { kind: 'inspector' }

function read(): Route { return parse(location.pathname, location.hash) }
function current(): { pathname: string; hash: string } {
  return { pathname: location.pathname, hash: location.hash }
}

export const route: Writable<Route> = writable(read())

// Settings modal open/closed — DERIVED from the route (the open Modal lives in the
// URL hash, `#settings=<section>`), so the URL is the single source of truth and
// there's no store↔URL drift. Re-exported from store.ts for consumers. Lives here,
// beside `route`, to avoid a store.ts↔router.ts module-init cycle (store.ts only
// re-exports the binding; it never touches `route` at init).
export const settingsOpen: Readable<boolean> = derived(route, ($r) => $r.overlay === 'settings')

// The pid segment for URLs: the one in the current path if any, else the active id.
function currentPid(): string {
  // Both empty only before boot resolves a profile, when no nav can run yet.
  return read().pid || getActiveProfileId() || ''
}

// go('/files') switches Tab and keeps the open Thread; go('/c/{id}') opens a
// Thread in the current Tab. The current hash (an open Modal) is preserved, so
// Page navigation never dismisses Settings. Pass a pid explicitly to switch profiles.
export function go(path: string, pid: string = currentPid()): void {
  const full = resolve(current(), { type: 'go', path, pid })
  if (location.pathname + location.hash !== full) history.pushState({}, '', full)
  route.set(read())
}

// Navigate to a bare Tab, CLOSING the open Thread — go('/tasks') would keep it
// (that contract exists so Tab switches don't close your chat); deleting/leaving
// a thread needs the opposite: land on the Tab's own empty page, not back on the
// (now-gone) Thread. Preserves the hash, same as go().
export function goTab(tab: Tab, pid: string = currentPid()): void {
  const full = resolve(current(), { type: 'goTab', tab, pid })
  if (location.pathname + location.hash !== full) history.pushState({}, '', full)
  route.set(read())
}

// Replace the current URL with /app/{pid}/ (used on boot to canonicalise a bare
// /app/ or a stale pid into the resolved profile). Preserves the hash so cold
// deep-links (`/app/#settings=…`) and the profile-switch reload survive. replaceState
// so the bare URL doesn't linger in history.
export function redirectToProfile(pid: string): void {
  const full = resolve(current(), { type: 'redirectToProfile', pid })
  if (location.pathname + location.hash !== full) history.replaceState({}, '', full)
  route.set(read())
}

// ── Modal slot helpers (the hash) ────────────────────────────────────────────
// Each preserves the path and touches only the hash. Opening a Modal PUSHES a
// history entry (so Back dismisses it); switching Section and closing REPLACE (no
// history spam per Section click; close strips the hash to reveal the Page).

export function openOverlay(name: string, value?: string): void {
  const full = resolve(current(), { type: 'openOverlay', name, value })
  history.pushState({}, '', full)
  route.set(read())
}

export function replaceOverlay(name: string, value?: string): void {
  const full = resolve(current(), { type: 'replaceOverlay', name, value })
  history.replaceState({}, '', full)
  route.set(read())
}

export function closeOverlay(): void {
  const full = resolve(current(), { type: 'closeOverlay' })
  history.replaceState({}, '', full)
  route.set(read())
}

// ── Aside slot helpers (the right rail — ADR 0009) ───────────────────────────
// Each helper touches only the `aside` hash key and preserves the Modal key.
// Opening the rail from closed pushes (Back closes it); switching occupant replaces.

function setAside(next: Aside | null): void {
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
export function openAsideFile(path: string | null | undefined): void {
  if (path) setAside({ kind: 'file', path })
}

// Open the AG2 Inspector as the rail occupant.
export function openAsideInspector(): void { setAside({ kind: 'inspector' }) }

export function closeAside(): void {
  if (!confirmDiscard()) return
  const full = resolve(current(), { type: 'closeAside' })
  history.replaceState({}, '', full)
  route.set(read())
}

// Toggle the Inspector as the rail occupant on/off.
export function toggleAsideInspector(): void {
  if (read().aside?.kind === 'inspector') closeAside()
  else openAsideInspector()
}

// Whether the AG2 Inspector occupies the rail; also gates the per-item provenance
// tags. Derived from the route, re-exported from store.ts as `ag2View`.
export const ag2View: Readable<boolean> = derived(route, ($r) => $r.aside?.kind === 'inspector')

export function newChatId(): string {
  return 'web-' + Math.random().toString(36).slice(2, 10)
}

// popstate covers Back/Forward (Page and Modal); hashchange covers a hash edited
// directly in the address bar. Both re-derive the route from the URL.
// Known gap: Back past an open dirty editor is intentionally un-guarded (ticket 04).
window.addEventListener('popstate', () => route.set(read()))
window.addEventListener('hashchange', () => route.set(read()))
