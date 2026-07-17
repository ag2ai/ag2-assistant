// Pure route algebra for the app shell — no DOM, no Svelte. This is the single
// tested seam (see route.test.mjs). The router shell (../router.js) wraps these
// two functions with location/history and the derived stores.
//
// Two dimensions live in the URL:
//   • the PATH carries the Page (profile + Tab + optional open Thread):
//     /app/{pid}/{tab}[/{c|t}/{id}] — Tab and Thread are orthogonal.
//   • the HASH carries the open Modal as a single overlay slot
//     (`#<modal>=<value>`, split on the first `=`), client-side only. Only
//     `settings` is wired now; the grammar is general so other Modals plug in later.

const BASE = '/app'

const dec = (s) => decodeURIComponent(s)

// The valid Settings Section ids — the modal's nav targets. Frozen so the
// vocabulary can't mutate at runtime. Re-exported from ../store.js as the app's
// SETTINGS_PAGE, so deep-links stay typo-proof (SETTINGS_PAGE.MODELS).
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
const SECTIONS = new Set(Object.values(SETTINGS_PAGE))

// The main-pane driver, derived from what Thread (if any) is open, INDEPENDENT of
// the Tab: an open Thread stays open while you switch drawer Tabs. With a Thread →
// 'chat'/'task'; otherwise the Tab's own empty page ('files'/'tasks') or 'home'
// (boot spins up a fresh chat) for chats.
function threadName(tab, kind) {
  if (kind === 't') return 'task'
  if (kind === 'c') return 'chat'
  if (tab === 'files') return 'files'
  if (tab === 'tasks') return 'tasks'
  return 'home'
}

// Parse the URL hash into the single Modal slot. Grammar: `#<modal>` or
// `#<modal>=<value>`, split on the FIRST `=` so a future value may contain `=`/`/`.
// Only `settings` is wired: a settings hash yields overlay='settings' + a validated
// Section (bogus/missing → General). Any other fragment opens no Modal (overlay=null).
function parseHash(hash) {
  const h = (hash || '').replace(/^#/, '')
  if (!h) return { overlay: null, overlayValue: null }
  const i = h.indexOf('=')
  const name = i === -1 ? h : h.slice(0, i)
  const value = i === -1 ? null : h.slice(i + 1)
  if (name === 'settings') {
    return { overlay: 'settings', overlayValue: SECTIONS.has(value) ? value : SETTINGS_PAGE.GENERAL }
  }
  return { overlay: null, overlayValue: null }
}

// parse(pathname, hash) → route. Routes carry the profile id, the drawer Tab, an
// optional open Thread, and the open Modal slot:
//   /app/{pid}/{tab}[/{c|t}/{id}]#<modal>=<value>
// tab ∈ chats|tasks|files is the drawer (left rail); the trailing c|t + id is the
// Thread in the main pane, preserved across Tab switches. Legacy
// /app/{pid}/c/{id} and /t/{id} still parse (resolve() canonicalises them).
export function parse(pathname, hash) {
  const p = pathname
  const o = parseHash(hash)
  let m
  if ((m = p.match(/^\/app\/([^/]+)\/(chats|tasks|files)(?:\/(c|t)\/(.+?))?\/?$/))) {
    const tab = m[2], kind = m[3] || null, id = m[4] ? dec(m[4]) : null
    return { name: threadName(tab, kind), tab, kind, id, pid: dec(m[1]), ...o }
  }
  if ((m = p.match(/^\/app\/([^/]+)\/t\/(.+)$/))) return { name: 'task', tab: 'tasks', kind: 't', id: dec(m[2]), pid: dec(m[1]), ...o }
  if ((m = p.match(/^\/app\/([^/]+)\/c\/(.+)$/))) return { name: 'chat', tab: 'chats', kind: 'c', id: dec(m[2]), pid: dec(m[1]), ...o }
  if ((m = p.match(/^\/app\/([^/]+)\/?$/))) return { name: 'home', tab: 'chats', kind: null, id: null, pid: dec(m[1]), ...o }
  // Bare /app/ or any legacy/unknown shape → home with no pid (boot resolves it).
  return { name: 'home', tab: 'chats', kind: null, id: null, pid: null, ...o }
}

// Resolve a caller path against the current route so Tab and Thread stay
// independent:
//   • '/chats' | '/tasks' | '/files' (bare Tab) → switch the drawer but KEEP the
//     open Thread as a suffix, so tabbing to Files doesn't close your chat.
//   • '/c/{id}' | '/t/{id}' (thread shorthand) → open that Thread in the CURRENT
//     Tab, so every existing go('/c/'…)/go('/t/'…) call site keeps working.
function normalizePath(path, r) {
  if (path === '/chats' || path === '/tasks' || path === '/files') {
    return path + (r.kind && r.id ? '/' + r.kind + '/' + r.id : '')
  }
  if (path.startsWith('/c/')) return '/' + r.tab + '/c/' + path.slice(3)
  if (path.startsWith('/t/')) return '/' + r.tab + '/t/' + path.slice(3)
  return path
}

// Build the hash fragment for a Modal slot value: `#name` (bare) or `#name=value`.
function overlayHash(name, value) {
  return value == null || value === '' ? '#' + name : '#' + name + '=' + value
}

// resolve(current, intent) → next URL string (path + hash). `current` is the two
// URL parts ({ pathname, hash }); `intent` is the navigation behind a nav helper.
// Path and hash are orthogonal: path intents preserve the current hash, Modal
// intents preserve the current path.
//   • go              — path nav (normalize against current); preserves the hash.
//   • openOverlay     — set the Modal hash; preserves the path.  (shell pushes)
//   • replaceOverlay  — same URL as openOverlay.                 (shell replaces)
//   • closeOverlay    — strip the hash entirely; preserves the path.
//   • redirectToProfile — canonicalise to /app/{pid}/; preserves the hash.
export function resolve(current, intent) {
  const r = parse(current.pathname, current.hash)
  const hash = current.hash || ''
  switch (intent.type) {
    case 'go': {
      const pid = intent.pid || r.pid
      return BASE + '/' + pid + normalizePath(intent.path, r) + hash
    }
    case 'openOverlay':
    case 'replaceOverlay':
      return current.pathname + overlayHash(intent.name, intent.value)
    case 'closeOverlay':
      return current.pathname
    case 'redirectToProfile':
      return BASE + '/' + intent.pid + '/' + hash
    default:
      return current.pathname + hash
  }
}
