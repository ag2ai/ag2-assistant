import { test } from 'node:test'
import assert from 'node:assert/strict'
import { installBrowserGlobals } from '../testing/browserGlobals.ts'

// stream.ts reaches lib/profile.js → store.js → router.js, which touches location
// and window while initialising. Install those before the chain loads, hence the
// dynamic import.
installBrowserGlobals()
const { readFrame } = await import('./stream.ts')

test('readFrame returns null for malformed JSON', () => {
  assert.equal(readFrame('{not json'), null)
})

test('readFrame parses an event frame', () => {
  const frame = readFrame(JSON.stringify({ event: { type: 'Attachment', data: { path: '/a' } } }))
  assert.ok(frame && 'event' in frame)
})

test('readFrame parses a control frame', () => {
  const frame = readFrame(JSON.stringify({ type: 'turn_end' }))
  assert.deepEqual(frame, { type: 'turn_end' })
})

test('readFrame returns null for an unrecognised frame', () => {
  assert.equal(readFrame(JSON.stringify({ nonsense: 1 })), null)
})

test('readFrame keeps the error text the backend sends as `message`', () => {
  const frame = readFrame(JSON.stringify({ type: 'error', message: 'boom', chat: 'c1' }))
  assert.ok(frame && 'type' in frame && frame.type === 'error')
  assert.equal(frame.type === 'error' ? frame.message : null, 'boom')
})

test('readFrame keeps the stream id the bridge stamps on ready and turn_end', () => {
  const ready = readFrame(JSON.stringify({ type: 'ready', chat: 'c1' }))
  assert.equal(ready && 'chat' in ready ? ready.chat : null, 'c1')
  const end = readFrame(JSON.stringify({ type: 'turn_end', chat: 'c1' }))
  assert.equal(end && 'chat' in end ? end.chat : null, 'c1')
})

test('readFrame keeps the chat id queued frames carry alongside the text', () => {
  const frame = readFrame(JSON.stringify({ type: 'queued', text: 'hi', chat: 'c1' }))
  assert.deepEqual(frame, { type: 'queued', text: 'hi', chat: 'c1' })
})
