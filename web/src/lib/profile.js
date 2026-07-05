// Active-profile plumbing (WP6 minimal cutover). One place that knows which
// profile the client is viewing, so every REST/WS call can prefix itself with
// /api/p/{pid}. Phase 2 grows the switcher UI on top of the `profiles` store
// (store.js); this module stays the low-level source of truth.
//
// NOTE: the plan names localStorage('ag2-profile') for the active id, but that
// key is already taken by the on-device user profile object {name,focuses} in
// store.js. To avoid clobbering it we persist the active *profile id* under
// 'ag2-profile-id'. (Documented deviation.)

const LS_KEY = 'ag2-profile-id'

let _activeId = null

export function getActiveProfileId() {
  return _activeId
}

export function setActiveProfileId(pid) {
  _activeId = pid || null
  try {
    if (_activeId) localStorage.setItem(LS_KEY, _activeId)
    else localStorage.removeItem(LS_KEY)
  } catch {}
}

export function storedProfileId() {
  try { return localStorage.getItem(LS_KEY) || null } catch { return null }
}

export function clearStoredProfileId() {
  try { localStorage.removeItem(LS_KEY) } catch {}
}

// Profile-scoped URL: api('/sessions') -> '/api/p/<pid>/sessions'.
export function api(path) {
  return `/api/p/${encodeURIComponent(_activeId)}${path}`
}

// Global (unprefixed) URL: globalApi('/profiles') -> '/api/profiles'.
export function globalApi(path) {
  return `/api${path}`
}

// A profile went away underneath us (WS close 4001/4410, or a 410 from a
// scoped route). Drop the stored id and re-resolve from scratch via a full
// reload of the SPA shell at /app/. Phase 2 replaces this with an in-place
// toast + switch; for now a hard re-resolve is correct and simple (§7 item 6).
export function onProfileGone(reason = '') {
  console.warn('[profile] active profile gone (' + reason + '); re-resolving')
  clearStoredProfileId()
  location.assign('/app/')
}
