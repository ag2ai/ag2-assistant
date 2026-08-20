// Active-profile plumbing (WP6 minimal cutover). One place that knows which
// profile the client is viewing, so every REST/WS call can prefix itself with
// /api/p/{pid}. Phase 2 grows the switcher UI on top of the `profiles` store
// (store.js); this module stays the low-level source of truth.
//
// NOTE: the plan names localStorage('ag2-profile') for the active id, but that
// key is already taken by the on-device user profile object {name,focuses} in
// store.js. To avoid clobbering it we persist the active *profile id* under
// 'ag2-profile-id'. (Documented deviation.)

import { get } from 'svelte/store'
import { profiles, notice } from '../store.ts'
import { m } from '../paraglide/messages.js'

const LS_KEY = 'ag2-profile-id'

let _activeId: string | null = null

export function getActiveProfileId(): string | null {
  return _activeId
}

export function setActiveProfileId(pid: string | null | undefined): void {
  _activeId = pid || null
  try {
    if (_activeId) localStorage.setItem(LS_KEY, _activeId)
    else localStorage.removeItem(LS_KEY)
  } catch {}
}

export function storedProfileId(): string | null {
  try { return localStorage.getItem(LS_KEY) || null } catch { return null }
}

export function clearStoredProfileId(): void {
  try { localStorage.removeItem(LS_KEY) } catch {}
}

// Profile-scoped URL: api('/chats') -> '/api/p/<pid>/chats'.
export function api(path: string): string {
  return `/api/p/${encodeURIComponent(_activeId ?? '')}${path}`
}

// Explicit-pid scoped URL: pidApi('work', '/settings/focuses') ->
// '/api/p/work/settings/focuses'. Used by flows (onboarding's per-profile setup)
// that must target a SPECIFIC profile rather than whichever one is active.
export function pidApi(pid: string, path: string): string {
  return `/api/p/${encodeURIComponent(pid)}${path}`
}

// Global (unprefixed) URL: globalApi('/profiles') -> '/api/profiles'.
export function globalApi(path: string): string {
  return `/api${path}`
}

// A profile went away underneath us (WS close 4001/4410, or a 410 from a
// scoped route). §4.9 recovery: show a brief notice naming the archived profile
// and the default it's switching to, then re-resolve via a full reload of the
// SPA shell at /app/ (App.svelte's boot picks the new active_default and applies
// its palette). Open tabs on the archived profile recover instead of spinning.
//
// store.js doesn't import this module, so a static import is cycle-free. Fired
// once — a second gone-signal during the grace window is ignored (navigation is
// already scheduled).
const RECOVER_DELAY = 1500
let _recovering = false
export function onProfileGone(reason = ''): void {
  console.warn('[profile] active profile gone (' + reason + '); recovering')
  const goneId = _activeId               // the archived profile is still "active" here
  clearStoredProfileId()
  if (_recovering) return
  _recovering = true
  try {
    const reg = get(profiles) || { list: [] }
    const gone = reg.list.find((p) => p.id === goneId)?.name || ''
    // Name the destination if we can (any surviving profile); else say "default".
    const dest = reg.list.find((p) => p.id !== goneId)?.name || ''
    const text = gone
      ? (dest ? m.notice_profile_archived({ name: gone, dest }) : m.notice_profile_archived_default({ name: gone }))
      : (dest ? m.notice_profile_archived_unnamed({ dest }) : m.notice_profile_archived_unnamed_default())
    notice.set({ text })
  } catch {}
  setTimeout(() => location.assign('/app/'), RECOVER_DELAY)
}
