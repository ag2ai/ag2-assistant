import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EventData, EventMeta, ServerFrame, WireEvent } from './events.ts'

test('WireEvent keeps a known event and defaults its data', () => {
  const ev = WireEvent.parse({ type: 'ModelMessageChunk', data: { content: 'hi' } })
  assert.equal(ev.type, 'ModelMessageChunk')
  assert.equal(ev.data.content, 'hi')
  assert.deepEqual(WireEvent.parse({ type: 'TurnCancelled' }).data, {})
})

test('WireEvent keeps an unknown event type instead of failing', () => {
  const ev = WireEvent.parse({ type: 'SomeFutureEvent', data: { whatever: 1 } })
  assert.equal(ev.type, 'SomeFutureEvent')
})

test('WireEvent tolerates a fully qualified AG2 event name', () => {
  const ev = WireEvent.parse({ type: 'ag2.events.ModelResponse', data: {} })
  assert.equal(ev.type, 'ag2.events.ModelResponse')
})

test('EventMeta reads the production stamp the reducer copies onto items', () => {
  assert.equal(EventMeta.parse({ path: '/a', created_at: 1754035200 }).created_at, 1754035200)
  assert.equal(EventMeta.parse({ path: '/a' }).created_at, undefined)
})

test('EventData narrows a ToolCallsEvent payload to its calls', () => {
  const d = EventData.ToolCallsEvent.parse({ calls: [{ name: 'read_file', arguments: { path: '/a' } }] })
  assert.equal(d.calls?.[0].name, 'read_file')
})

test('EventData keeps the task lifecycle payload loose enough for a subagent', () => {
  const d = EventData.TaskLifecycle.parse({
    agent_name: 'researcher', task_id: 'st1', objective: 'find refs', result: 'ok',
  })
  assert.equal(d.objective, 'find refs')
})

test('EventData pins the feedback target to a ratable kind', () => {
  const d = EventData.FeedbackGiven.parse({ target_kind: 'image', target_id: '/a.png', sentiment: 'up' })
  assert.equal(d.target_kind, 'image')
  assert.throws(() => EventData.FeedbackGiven.parse({ target_kind: 'note', target_id: 'x' }))
})

test('ServerFrame parses the ready frame', () => {
  const frame = ServerFrame.parse({ type: 'ready', chat: 'c1' })
  assert.equal('type' in frame && frame.type, 'ready')
})

test('ServerFrame parses an event frame', () => {
  const frame = ServerFrame.parse({ event: { type: 'Attachment', data: { path: '/a', name: 'a' } } })
  assert.equal('event' in frame, true)
})

test('ServerFrame rejects a frame that is neither an event nor a control type', () => {
  assert.throws(() => ServerFrame.parse({ nonsense: 1 }))
})
