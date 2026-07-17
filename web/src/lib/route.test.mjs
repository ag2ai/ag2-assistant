import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parse, resolve } from './route.js'

// ── parse(pathname, hash) → route ───────────────────────────────────────────
// Asserts only external behaviour: URL string in, route object out.

test('parse: each Tab, no Thread', () => {
  assert.deepEqual(parse('/app/work/chats', ''), {
    name: 'home', tab: 'chats', kind: null, id: null, pid: 'work', overlay: null, overlayValue: null,
  })
  assert.deepEqual(parse('/app/work/tasks', ''), {
    name: 'tasks', tab: 'tasks', kind: null, id: null, pid: 'work', overlay: null, overlayValue: null,
  })
  assert.deepEqual(parse('/app/work/files', ''), {
    name: 'files', tab: 'files', kind: null, id: null, pid: 'work', overlay: null, overlayValue: null,
  })
})

test('parse: open Thread under a Tab (chat and task), preserved across Tabs', () => {
  assert.deepEqual(parse('/app/work/chats/c/web-abc', ''), {
    name: 'chat', tab: 'chats', kind: 'c', id: 'web-abc', pid: 'work', overlay: null, overlayValue: null,
  })
  // A Chat kept open while on the Files Tab — Tab and Thread are orthogonal.
  assert.deepEqual(parse('/app/work/files/c/web-abc', ''), {
    name: 'chat', tab: 'files', kind: 'c', id: 'web-abc', pid: 'work', overlay: null, overlayValue: null,
  })
  assert.deepEqual(parse('/app/work/tasks/t/t-42', ''), {
    name: 'task', tab: 'tasks', kind: 't', id: 't-42', pid: 'work', overlay: null, overlayValue: null,
  })
})

test('parse: bare /app/{pid}/ → home on chats', () => {
  assert.deepEqual(parse('/app/work/', ''), {
    name: 'home', tab: 'chats', kind: null, id: null, pid: 'work', overlay: null, overlayValue: null,
  })
})

test('parse: bare /app/ (no pid) → home with null pid for boot to resolve', () => {
  assert.deepEqual(parse('/app/', ''), {
    name: 'home', tab: 'chats', kind: null, id: null, pid: null, overlay: null, overlayValue: null,
  })
})

test('parse: legacy /c/{id} and /t/{id} still parse', () => {
  assert.deepEqual(parse('/app/work/c/web-abc', ''), {
    name: 'chat', tab: 'chats', kind: 'c', id: 'web-abc', pid: 'work', overlay: null, overlayValue: null,
  })
  assert.deepEqual(parse('/app/work/t/t-42', ''), {
    name: 'task', tab: 'tasks', kind: 't', id: 't-42', pid: 'work', overlay: null, overlayValue: null,
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
    name: 'home', tab: 'chats', kind: null, id: null, pid: 'work', overlay: 'settings', overlayValue: 'general',
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
