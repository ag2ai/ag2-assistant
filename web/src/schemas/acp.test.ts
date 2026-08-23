import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AcpListener, AcpListenerCreated, AcpListenerList, AcpListenerTokenRotated } from './acp.ts'

const row = {
  id: 'acp_1', name: 'Space · work', profile: 'p1', port: 8802,
  running: true, error: null, has_token: true,
}

test('AcpListener accepts the shape the gateway returns', () => {
  assert.deepEqual(AcpListener.parse(row), row)
})

test('AcpListener has no field carrying the raw token', () => {
  const parsed = AcpListener.parse(row)
  assert.equal('token' in parsed, false)
})

test('AcpListenerList wraps a list of rows', () => {
  const parsed = AcpListenerList.parse({ listeners: [row] })
  assert.equal(parsed.listeners.length, 1)
})

test('AcpListenerCreated carries the one-time token beside the listener', () => {
  const parsed = AcpListenerCreated.parse({ listener: row, token: 's3cret' })
  assert.equal(parsed.token, 's3cret')
  assert.equal(parsed.listener.id, 'acp_1')
})

test('AcpListenerTokenRotated has the same shape as AcpListenerCreated', () => {
  const parsed = AcpListenerTokenRotated.parse({ listener: row, token: 'new-token' })
  assert.equal(parsed.token, 'new-token')
})

test('a listener with no port or error reads as configured but not live', () => {
  const stdio = { ...row, port: null, running: false, error: 'stdio listeners run by their client' }
  const parsed = AcpListener.parse(stdio)
  assert.equal(parsed.port, null)
  assert.equal(parsed.running, false)
})
