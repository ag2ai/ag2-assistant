import { test } from 'node:test'
import assert from 'node:assert/strict'
import { installBrowserGlobals } from '../testing/browserGlobals.ts'

// stream.ts reaches lib/profile.js → store.js → router.js, which touches location
// and window while initialising. Install those before the chain loads, hence the
// dynamic import.
installBrowserGlobals()
const { readFrame, StreamClient } = await import('./stream.ts')

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

// A fake socket recording every frame the client sends.
class FakeSocket {
  static readonly OPEN = 1
  static last: FakeSocket | null = null
  readyState = 1
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: unknown) => void) | null = null
  onclose: ((e: unknown) => void) | null = null
  constructor() { FakeSocket.last = this }
  send(raw: string) { this.sent.push(raw) }
  close() {}
}

function connected() {
  Object.defineProperty(globalThis, 'WebSocket', { value: FakeSocket, configurable: true, writable: true })
  const client = new StreamClient('c1').connect()
  FakeSocket.last?.onopen?.()
  return { client, socket: FakeSocket.last as FakeSocket }
}

test('a click sends the instance data model beside the action envelope', () => {
  const { client, socket } = connected()
  client.a2ui({ version: 'v1.0', action: { name: 'buy' } }, { surfaceId: 's1', data: { rows: [1, 2] } })
  const frame = JSON.parse(socket.sent[0])

  assert.deepEqual(frame.state, { surfaceId: 's1', data: { rows: [1, 2] } })
  assert.deepEqual(frame.message, { version: 'v1.0', action: { name: 'buy' } })
})

test('a click with no model sends no state key at all', () => {
  const { client, socket } = connected()
  client.a2ui({ version: 'v1.0', action: { name: 'buy' } })

  assert.deepEqual(JSON.parse(socket.sent[0]), { type: 'a2ui', message: { version: 'v1.0', action: { name: 'buy' } } })
})
