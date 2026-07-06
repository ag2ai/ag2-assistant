// Headless smoke test: run the built bundle under jsdom to surface mount errors
// or infinite loops (run with a timeout). Not part of the app.
import { JSDOM } from 'jsdom'
import { readFileSync, readdirSync } from 'node:fs'

// New route shape carries the profile id: /app/{pid}/c/{sid}.
const dom = new JSDOM('<!doctype html><html><body><div id="app"></div></body></html>', {
  url: 'http://127.0.0.1:8800/app/diagprof/c/diagtest',
  pretendToBeVisual: true,
})
const w = dom.window
global.window = w
global.document = w.document
global.location = w.location
global.history = w.history
for (const k of ['HTMLElement', 'Node', 'NodeFilter', 'MutationObserver', 'Element', 'Text',
  'Comment', 'DocumentFragment', 'Event', 'CustomEvent', 'getComputedStyle', 'CSSStyleSheet']) {
  if (w[k] !== undefined) global[k] = w[k]
}
global.requestAnimationFrame = () => 0
global.cancelAnimationFrame = (id) => clearTimeout(id)
global.WebSocket = class { constructor() {} send() {} close() {} addEventListener() {} set onmessage(v) {} set onclose(v) {} set onopen(v) {} set onerror(v) {} }

// Mocked backend covering the new profile-scoped API surface. /api/profiles is
// fetched FIRST on boot (§7 item 4) and must return a non-empty registry whose
// id matches the URL pid, so the client resolves an active profile and mounts
// the full app (Drawer + Thread) rather than the zero-profile create form.
const seen = new Set()
const scopedPids = new Set()  // which pid(s) the client actually scoped requests to
global.fetch = async (url) => {
  const path = String(url)
  const m = path.match(/\/api\/p\/([^/]+)/)
  if (m) scopedPids.add(decodeURIComponent(m[1]))
  seen.add(path.replace(/\/api\/p\/[^/]+/, '/api/p/{pid}'))  // record shape, pid-normalised
  let body = {}
  if (path.includes('/api/profiles')) {
    // Two profiles; active_default is 'other', but the boot URL names 'diagprof'
    // (see JSDOM url above). A VALID URL pid must win over active_default, so
    // the client must resolve 'diagprof' and issue /api/p/diagprof/… requests.
    body = {
      profiles: [
        { id: 'other', name: 'Other', palette: 'ocean', workspace: '/tmp/other' },
        { id: 'diagprof', name: 'Diag', palette: 'teal', workspace: '/tmp/diag' },
      ],
      active_default: 'other',
      onboarded: true,
    }
  } else if (path.includes('/settings')) {
    body = { onboarded: true, keys: { gemini: { set: true } }, fs: {} }
  } else if (path.includes('/sessions')) {
    body = { sessions: [] }
  } else if (path.includes('/tasks')) {
    body = { tasks: [] }
  } else if (path.includes('/usage')) {
    // Global roll-up shape: {profiles:[{pid,name,...usage_today()}], total}.
    body = {
      profiles: [
        { pid: 'diagprof', name: 'Diag', date: '2026-07-06', prompt: 0, completion: 0, total: 0, cost: 0, priced: false, by_model: {} },
      ],
      total: { prompt: 0, completion: 0, total: 0, cost: 0, priced: false },
    }
  } else if (path.includes('/inquiries') || path.includes('/hitl')) {
    body = { pending: [] }
  }
  return { ok: true, status: 200, json: async () => body, text: async () => '' }
}

process.on('uncaughtException', (e) => { console.error('UNCAUGHT:', e && (e.stack || e)); process.exit(2) })
process.on('unhandledRejection', (e) => { console.error('UNHANDLED:', e && (e.stack || e)); process.exit(3) })

const dir = new URL('../src/assistant/gateway/static/app/assets/', import.meta.url)
const js = readdirSync(dir).find((f) => f.endsWith('.js'))
console.log('loading bundle:', js)
await import(new URL(js, dir).href)
console.log('MOUNTED OK. #app length =', w.document.getElementById('app').innerHTML.length)
setTimeout(() => {
  const len = w.document.getElementById('app').innerHTML.length
  console.log('still alive after 1s, app len =', len)
  console.log('fetched (pid-normalised):', [...seen].sort().join('  '))
  // Boot must have fetched the global registry first, then resolved a profile
  // and issued at least one profile-scoped (/api/p/{pid}/…) request.
  const hitProfiles = [...seen].some((p) => p.includes('/api/profiles'))
  const hitScoped = [...seen].some((p) => p.includes('/api/p/{pid}/'))
  if (!hitProfiles) { console.error('FAIL: /api/profiles was never fetched'); process.exit(4) }
  if (!hitScoped) { console.error('FAIL: no profile-scoped /api/p/{pid}/ route was called'); process.exit(5) }
  if (!len) { console.error('FAIL: app did not render'); process.exit(6) }
  // URL-pid-wins: the boot URL names 'diagprof' while active_default is 'other'.
  // A valid URL pid must win, so every scoped request must target 'diagprof'.
  console.log('scoped pids:', [...scopedPids].sort().join(', '))
  if (!scopedPids.has('diagprof') || scopedPids.has('other')) {
    console.error('FAIL: URL pid did not win (expected only diagprof, got:', [...scopedPids].join(', '), ')')
    process.exit(7)
  }
  console.log('DIAG OK: registry fetched + profile-scoped routes exercised + URL pid wins')
  process.exit(0)
}, 1000)
