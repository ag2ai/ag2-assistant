import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parse, resolve } from './route.js'

// The four full-object parse assertions below carry every route field. `aside`
// (the right-rail occupant, ADR 0009) defaults to null (rail closed).

// ── parse(pathname, hash) → route ───────────────────────────────────────────
// Asserts only external behaviour: URL string in, route object out.

test('parse: each Tab, no Thread', () => {
  assert.deepEqual(parse('/app/work/chats', ''), {
    name: 'home', tab: 'chats', kind: null, id: null, pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
  assert.deepEqual(parse('/app/work/tasks', ''), {
    name: 'tasks', tab: 'tasks', kind: null, id: null, pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
  assert.deepEqual(parse('/app/work/files', ''), {
    name: 'files', tab: 'files', kind: null, id: null, pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
})

test('parse: open Thread under a Tab (chat and task), preserved across Tabs', () => {
  assert.deepEqual(parse('/app/work/chats/c/web-abc', ''), {
    name: 'chat', tab: 'chats', kind: 'c', id: 'web-abc', pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
  // A Chat kept open while on the Files Tab — Tab and Thread are orthogonal.
  assert.deepEqual(parse('/app/work/files/c/web-abc', ''), {
    name: 'chat', tab: 'files', kind: 'c', id: 'web-abc', pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
  assert.deepEqual(parse('/app/work/tasks/t/t-42', ''), {
    name: 'task', tab: 'tasks', kind: 't', id: 't-42', pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
})

test('parse: bare /app/{pid}/ → home on chats', () => {
  assert.deepEqual(parse('/app/work/', ''), {
    name: 'home', tab: 'chats', kind: null, id: null, pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
})

test('parse: bare /app/ (no pid) → home with null pid for boot to resolve', () => {
  assert.deepEqual(parse('/app/', ''), {
    name: 'home', tab: 'chats', kind: null, id: null, pid: null, overlay: null, overlayValue: null, aside: null,
  })
})

test('parse: legacy /c/{id} and /t/{id} still parse', () => {
  assert.deepEqual(parse('/app/work/c/web-abc', ''), {
    name: 'chat', tab: 'chats', kind: 'c', id: 'web-abc', pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
  assert.deepEqual(parse('/app/work/t/t-42', ''), {
    name: 'task', tab: 'tasks', kind: 't', id: 't-42', pid: 'work', overlay: null, overlayValue: null, aside: null,
  })
})

test('parse: #settings=<section> opens Settings on that Section', () => {
  const r = parse('/app/work/chats', '#settings=models')
  assert.equal(r.overlay, 'settings')
  assert.equal(r.overlayValue, 'models')
  // The Page underneath is untouched by the Modal hash.
  assert.equal(r.tab, 'chats')
  assert.equal(r.name, 'home')
})

test('parse: bare #settings resolves to the General Section', () => {
  assert.deepEqual(parse('/app/work/chats', '#settings'), {
    name: 'home', tab: 'chats', kind: null, id: null, pid: 'work', overlay: 'settings', overlayValue: 'general', aside: null,
  })
})

test('parse: bogus #settings=<bad> falls back to General', () => {
  const r = parse('/app/work/chats', '#settings=not-a-section')
  assert.equal(r.overlay, 'settings')
  assert.equal(r.overlayValue, 'general')
})

test('parse: unrecognised #… fragment opens no Modal', () => {
  const r = parse('/app/work/chats', '#memory')
  assert.equal(r.overlay, null)
  assert.equal(r.overlayValue, null)
})

test('parse: hash split on the FIRST = (value may contain =)', () => {
  // settings values are simple slugs, but the grammar must split on the first =.
  const r = parse('/app/work/chats', '#settings=models=extra')
  // 'models=extra' is not a known Section → General.
  assert.equal(r.overlay, 'settings')
  assert.equal(r.overlayValue, 'general')
})

test('parse: hash on a cold /app/ load (deep-link before any Chat)', () => {
  const r = parse('/app/', '#settings=secrets')
  assert.equal(r.pid, null)
  assert.equal(r.overlay, 'settings')
  assert.equal(r.overlayValue, 'secrets')
})

// ── resolve(current, intent) → url ──────────────────────────────────────────
// (current URL + nav intent) in, next URL out.

test('resolve: openOverlay preserves the path, writes the hash', () => {
  assert.equal(
    resolve({ pathname: '/app/work/files/c/web-abc', hash: '' }, { type: 'openOverlay', name: 'settings', value: 'general' }),
    '/app/work/files/c/web-abc#settings=general',
  )
})

test('resolve: bare openOverlay (no value) → #settings', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '' }, { type: 'openOverlay', name: 'settings', value: null }),
    '/app/work/chats#settings',
  )
})

test('resolve: replaceOverlay produces the same URL as openOverlay', () => {
  const cur = { pathname: '/app/work/chats/c/web-abc', hash: '#settings=general' }
  assert.equal(
    resolve(cur, { type: 'replaceOverlay', name: 'settings', value: 'models' }),
    '/app/work/chats/c/web-abc#settings=models',
  )
})

test('resolve: switch Tab preserves the open Thread suffix', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats/c/web-abc', hash: '' }, { type: 'go', path: '/files' }),
    '/app/work/files/c/web-abc',
  )
})

test('resolve: switch Tab with no open Thread', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '' }, { type: 'go', path: '/tasks' }),
    '/app/work/tasks',
  )
})

test('resolve: open Thread preserves the current Tab', () => {
  // On the Files Tab, opening a chat keeps the Files Tab (orthogonal).
  assert.equal(
    resolve({ pathname: '/app/work/files', hash: '' }, { type: 'go', path: '/c/web-xyz' }),
    '/app/work/files/c/web-xyz',
  )
})

test('resolve: go preserves the current hash (Settings stays open across Tab switch)', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats/c/web-abc', hash: '#settings=models' }, { type: 'go', path: '/files' }),
    '/app/work/files/c/web-abc#settings=models',
  )
})

test('resolve: closeOverlay strips only the hash, keeping the Page', () => {
  assert.equal(
    resolve({ pathname: '/app/work/files/c/web-abc', hash: '#settings=models' }, { type: 'closeOverlay' }),
    '/app/work/files/c/web-abc',
  )
})

test('resolve: redirectToProfile preserves the hash (cold deep-links survive boot)', () => {
  assert.equal(
    resolve({ pathname: '/app/', hash: '#settings=secrets' }, { type: 'redirectToProfile', pid: 'work' }),
    '/app/work/#settings=secrets',
  )
})

test('resolve: redirectToProfile with no hash', () => {
  assert.equal(
    resolve({ pathname: '/app/', hash: '' }, { type: 'redirectToProfile', pid: 'work' }),
    '/app/work/',
  )
})

test('resolve: go with an explicit pid switches profile', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '' }, { type: 'go', path: '/chats', pid: 'home' }),
    '/app/home/chats',
  )
})

// ── aside (ADR 0009): the right-rail occupant, a second hash key ─────────────
// The hash grows from a single overlay slot to a multi-key fragment so the Modal
// (`settings`) and the aside coexist. `aside` parses to null (closed),
// { kind: 'file', path }, or { kind: 'inspector' }.

test('parse: #aside=file:<path> → a file preview occupant', () => {
  const r = parse('/app/work/chats', '#aside=file:reports/x.md')
  assert.deepEqual(r.aside, { kind: 'file', path: 'reports/x.md' })
  assert.equal(r.overlay, null) // a lone #aside opens no Modal
})

test('parse: #aside=inspector → the Inspector occupant', () => {
  const r = parse('/app/work/chats', '#aside=inspector')
  assert.deepEqual(r.aside, { kind: 'inspector' })
  assert.equal(r.overlay, null)
})

test('parse: missing / empty / bogus aside → null (rail closed)', () => {
  assert.equal(parse('/app/work/chats', '').aside, null)
  assert.equal(parse('/app/work/chats', '#aside').aside, null)
  assert.equal(parse('/app/work/chats', '#aside=').aside, null)
  assert.equal(parse('/app/work/chats', '#aside=file:').aside, null) // file: with no path
  assert.equal(parse('/app/work/chats', '#aside=nonsense').aside, null)
})

test('parse: a lone #settings=… yields aside: null', () => {
  assert.equal(parse('/app/work/chats', '#settings=models').aside, null)
})

test('parse: multi-key hash yields both the Modal Section and the aside', () => {
  const r = parse('/app/work/chats', '#settings=models&aside=file:reports/x.md')
  assert.equal(r.overlay, 'settings')
  assert.equal(r.overlayValue, 'models')
  assert.deepEqual(r.aside, { kind: 'file', path: 'reports/x.md' })
})

test('parse: multi-key hash is order-independent (aside first)', () => {
  const r = parse('/app/work/chats', '#aside=file:reports/x.md&settings=models')
  assert.equal(r.overlay, 'settings')
  assert.equal(r.overlayValue, 'models')
  assert.deepEqual(r.aside, { kind: 'file', path: 'reports/x.md' })
})

test('parse: an aside file path round-trips through %-encoding', () => {
  const r = parse('/app/work/chats', '#aside=file:a%20b/c%26d.md')
  assert.deepEqual(r.aside, { kind: 'file', path: 'a b/c&d.md' })
})

// ── resolve: aside intents (open = push, switch/close = replace at the shell) ──
// The pure algebra only computes the next URL; the shell chooses push vs replace.

test('resolve: openAside sets only the aside key, preserving the Modal key', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '#settings=models' },
      { type: 'openAside', aside: { kind: 'file', path: 'reports/x.md' } }),
    '/app/work/chats#settings=models&aside=file:reports/x.md',
  )
})

test('resolve: openAside on a bare path writes only the aside key', () => {
  assert.equal(
    resolve({ pathname: '/app/work/files/c/web-abc', hash: '' },
      { type: 'openAside', aside: { kind: 'inspector' } }),
    '/app/work/files/c/web-abc#aside=inspector',
  )
})

test('resolve: opening a file while the Inspector is open replaces it (one occupant)', () => {
  const next = resolve({ pathname: '/app/work/chats', hash: '#aside=inspector' },
    { type: 'replaceAside', aside: { kind: 'file', path: 'reports/x.md' } })
  assert.equal(next, '/app/work/chats#aside=file:reports/x.md')
  // Structural mutual exclusion: exactly one aside value in the result.
  assert.equal((next.match(/aside=/g) || []).length, 1)
})

test('resolve: closeAside strips only the aside key, keeping the Modal', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '#settings=models&aside=file:reports/x.md' },
      { type: 'closeAside' }),
    '/app/work/chats#settings=models',
  )
})

test('resolve: closeAside with no Modal leaves a bare path', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '#aside=inspector' }, { type: 'closeAside' }),
    '/app/work/chats',
  )
})

test('resolve: a Modal intent preserves the aside key', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '#aside=file:reports/x.md' },
      { type: 'openOverlay', name: 'settings', value: 'models' }),
    '/app/work/chats#settings=models&aside=file:reports/x.md',
  )
})

test('resolve: closeOverlay preserves the aside key (only the Modal closes)', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '#settings=models&aside=file:reports/x.md' },
      { type: 'closeOverlay' }),
    '/app/work/chats#aside=file:reports/x.md',
  )
})

test('resolve: go preserves the whole multi-key hash across a Tab switch', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats/c/web-abc', hash: '#settings=models&aside=file:reports/x.md' },
      { type: 'go', path: '/files' }),
    '/app/work/files/c/web-abc#settings=models&aside=file:reports/x.md',
  )
})

test('resolve: an aside file path with special chars is encoded segment-wise', () => {
  assert.equal(
    resolve({ pathname: '/app/work/chats', hash: '' },
      { type: 'openAside', aside: { kind: 'file', path: 'a b/c&d.md' } }),
    '/app/work/chats#aside=file:a%20b/c%26d.md',
  )
})

test('resolve: goTab switches to a bare Tab and drops any open Thread suffix', () => {
  // Unlike 'go', which keeps an open Thread as a suffix across a Tab switch,
  // 'goTab' is for leaving/closing a Thread entirely (e.g. after deleting it).
  assert.equal(
    resolve({ pathname: '/app/p1/tasks/t/task-9', hash: '' }, { type: 'goTab', tab: 'tasks' }),
    '/app/p1/tasks',
  )
})

test('resolve: goTab preserves the current hash', () => {
  assert.equal(
    resolve({ pathname: '/app/p1/tasks/t/task-9', hash: '#settings=models' }, { type: 'goTab', tab: 'tasks' }),
    '/app/p1/tasks#settings=models',
  )
})

test('run thread routes parse and resolve', () => {
  const r = parse('/app/p1/tasks/r/run_9', '')
  assert.equal(r.name, 'run')
  assert.equal(r.kind, 'r')
  assert.equal(r.id, 'run_9')
  // tab switch keeps the open run thread
  const url = resolve({ pathname: '/app/p1/tasks/r/run_9', hash: '' }, { type: 'go', path: '/chats' })
  assert.equal(url, '/app/p1/chats/r/run_9')
  // /r/ shorthand opens in the current tab
  const url2 = resolve({ pathname: '/app/p1/tasks', hash: '' }, { type: 'go', path: '/r/run_9' })
  assert.equal(url2, '/app/p1/tasks/r/run_9')
})
