// Pure route algebra for the app shell — no DOM, no Svelte. This is the single
// tested seam (see route.test.mjs). The router shell (../router.js) wraps these
// two functions with location/history and the derived stores.
//
// Two dimensions live in the URL:
//   • the PATH carries the Page (profile + Tab + optional open Thread):
//     /app/{pid}/{tab}[/{c|t}/{id}] — Tab and Thread are orthogonal.
//   • the HASH carries two orthogonal, client-side-only overlay slots as a
//     multi-key fragment (`#k1=v1&k2=v2`, `&`-separated, each pair split on the
//     first `=`): the open Modal (`settings=<section>`) and the right-rail
//     `aside` occupant (`aside=file:<path>` | `aside=inspector`).

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

// The canonical serialization order of the hash keys, so a built URL is
// deterministic regardless of how the caller's keys were ordered.
const HASH_KEY_ORDER = ['settings', 'aside']

// Parse a multi-key hash fragment into an ordered key→raw-value Map. Grammar:
// `#k1=v1&k2=v2`; each pair split on the FIRST `=` (a value may contain `=`/`/`).
// A bare key (no `=`, e.g. `#settings`) maps to '' so it re-serializes bare.
function parseHashKeys(hash) {
  const h = (hash || '').replace(/^#/, '')
  const out = new Map()
  if (!h) return out
  for (const part of h.split('&')) {
    if (!part) continue
    const i = part.indexOf('=')
    const key = i === -1 ? part : part.slice(0, i)
    if (!key) continue
    out.set(key, i === -1 ? '' : part.slice(i + 1))
  }
  return out
}

// Serialize a key→raw-value Map back to a hash string in canonical key order.
// Only known keys are emitted (unknown fragments are noise). '' → a bare key.
function buildHash(keys) {
  const parts = []
  for (const k of HASH_KEY_ORDER) {
    if (!keys.has(k)) continue
    const v = keys.get(k)
    parts.push(v === '' ? k : k + '=' + v)
  }
  return parts.length ? '#' + parts.join('&') : ''
}

// Interpret the raw `aside` value into the rail occupant. `file:<path>` → a file
// preview (path %-decoded); `inspector` → the Inspector; anything else, empty, or
// `file:` with no path → null (rail closed), mirroring the bogus-Section fallback.
function parseAside(value) {
  if (value == null || value === '') return null
  if (value === 'inspector') return { kind: 'inspector' }
  if (value.startsWith('file:')) {
    const path = dec(value.slice(5))
    return path ? { kind: 'file', path } : null
  }
  return null
}

// Serialize a rail occupant to its raw `aside` value (inverse of parseAside); the
// file path is encoded segment-wise so `&`/spaces are safe while `/` stays readable.
function asideValue(aside) {
  if (!aside) return null
  if (aside.kind === 'inspector') return 'inspector'
  if (aside.kind === 'file' && aside.path) {
    return 'file:' + aside.path.split('/').map(encodeURIComponent).join('/')
  }
  return null
}

// Parse the hash into the independent Modal slot (`overlay`/`overlayValue`, a
// `settings` key validated to a Section) and the `aside` rail occupant.
function parseHash(hash) {
  const keys = parseHashKeys(hash)
  const overlay = keys.has('settings') ? 'settings' : null
  const section = keys.get('settings')
  return {
    overlay,
    overlayValue: overlay ? (SECTIONS.has(section) ? section : SETTINGS_PAGE.GENERAL) : null,
    aside: parseAside(keys.get('aside')),
  }
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

// resolve(current, intent) → next URL string (path + hash). `current` is the two
// URL parts ({ pathname, hash }); `intent` is the navigation behind a nav helper.
// Path and hash are orthogonal, and within the hash the Modal and `aside` keys are
// orthogonal too: each intent touches one key and preserves the other verbatim.
//   • go                — path nav (normalize against current); preserves the whole hash.
//   • openOverlay       — set the Modal key; preserves the aside key.   (shell pushes)
//   • replaceOverlay    — same URL as openOverlay.                      (shell replaces)
//   • closeOverlay      — drop the Modal key; preserves the aside key.
//   • openAside         — set the aside key; preserves the Modal key.   (shell pushes)
//   • replaceAside      — same URL as openAside (switch occupant).      (shell replaces)
//   • closeAside        — drop the aside key; preserves the Modal key.
//   • redirectToProfile — canonicalise to /app/{pid}/; preserves the hash.
export function resolve(current, intent) {
  const r = parse(current.pathname, current.hash)
  const hash = current.hash || ''
  const keys = parseHashKeys(hash)
  switch (intent.type) {
    case 'go': {
      const pid = intent.pid || r.pid
      return BASE + '/' + pid + normalizePath(intent.path, r) + hash
    }
    case 'openOverlay':
    case 'replaceOverlay':
      keys.set(intent.name, intent.value == null ? '' : intent.value)
      return current.pathname + buildHash(keys)
    case 'closeOverlay':
      // Close the Modal slot only — the aside key (if any) survives.
      for (const k of [...keys.keys()]) if (k !== 'aside') keys.delete(k)
      return current.pathname + buildHash(keys)
    case 'openAside':
    case 'replaceAside': {
      const v = asideValue(intent.aside)
      if (v == null) keys.delete('aside')
      else keys.set('aside', v)
      return current.pathname + buildHash(keys)
    }
    case 'closeAside':
      keys.delete('aside')
      return current.pathname + buildHash(keys)
    case 'redirectToProfile':
      return BASE + '/' + intent.pid + '/' + hash
    default:
      return current.pathname + hash
  }
}
