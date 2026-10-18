import { test } from 'node:test'
import assert from 'node:assert/strict'
import { installBrowserGlobals } from '../testing/browserGlobals.ts'

// The module reaches lib/profile.ts → store.ts → router.ts, which touch location and window.
installBrowserGlobals()
const { readFrame } = await import('./cardSourceSocket.ts')

test('readFrame returns null for malformed JSON', () => {
  assert.equal(readFrame('{not json'), null)
})

test('readFrame parses an event frame', () => {
  const frame = readFrame(JSON.stringify({ event: { type: 'CardSourceUpdated', data: { path: '/a' } } }))
  assert.ok(frame && 'event' in frame)
})

test('readFrame parses the ready frame', () => {
  assert.deepEqual(readFrame(JSON.stringify({ type: 'ready', chat: '' })), { type: 'ready', chat: '' })
})

test('readFrame returns null for an unrecognised frame', () => {
  assert.equal(readFrame(JSON.stringify({ nonsense: 1 })), null)
})
