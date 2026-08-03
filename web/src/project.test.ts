// The event → items projection. Covers the turn-ending rules: a stopped turn must
// stay stopped when the stream replays (isBusy is what drives the thinking dots).
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { foldEvent, isBusy, queueMessage } from './project.ts'
import type { ThreadItem, WireEvent } from './schemas/events.ts'

const user = (text: string): WireEvent => ({
  type: 'ag2.events.ModelRequest',
  data: { parts: [{ __event__: 'ag2.events.TextInput', content: text }] },
})
const chunk = (text: string): WireEvent => ({ type: 'ag2.events.ModelMessageChunk', data: { content: text } })
const cancelled = (reason: string): WireEvent => ({ type: 'assistant.events.TurnCancelled', data: { reason } })
const failed = (error: string): WireEvent => ({ type: 'assistant.events.TurnFailed', data: { error } })
const a2uiAction = (): WireEvent => ({ type: 'assistant.events.A2UIActionSubmitted', data: { surface_id: 'demo', action_name: 'continue' } })
const response = (content: string): WireEvent => ({
  type: 'ag2.events.ModelResponse',
  data: { message: { content }, tool_calls: { calls: [] } },
})

// Narrow to the one kind a test asserts on, failing the test if it is another.
function itemOfKind<K extends ThreadItem['kind']>(
  item: ThreadItem | undefined,
  kind: K,
): Extract<ThreadItem, { kind: K }> {
  assert.equal(item?.kind, kind)
  return item as Extract<ThreadItem, { kind: K }>
}

test('a user message with no reply yet is busy', () => {
  const items: ThreadItem[] = []
  foldEvent(items, user('hello'))
  assert.equal(isBusy(items), true)
})

test('a message fed to a running turn renders as a user bubble where it landed', () => {
  const items: ThreadItem[] = []
  foldEvent(items, user('research widgets'))
  foldEvent(items, chunk('searching…'))
  // AG2 re-emits a fed message as DrainedModelRequest when the running turn picks it up
  foldEvent(items, { ...user('focus on 2026'), type: 'ag2.events.DrainedModelRequest' })

  assert.deepEqual(
    items.filter((i) => i.kind === 'user').map((i) => i.text),
    ['research widgets', 'focus on 2026'],
  )
  assert.equal(isBusy(items), true)   // the turn is still running after the steer
})

test('a queued message resolves in place when the agent drains it — no duplicate', () => {
  const items: ThreadItem[] = []
  foldEvent(items, user('research widgets'))
  queueMessage(items, 'focus on 2026')   // server acked the feed; agent hasn't reached it yet

  const pending = itemOfKind(items.at(-1), 'user')
  assert.equal(pending.queued, true)

  // …a tool round later, AG2 drains the inbox and re-emits it
  foldEvent(items, { ...user('focus on 2026'), type: 'ag2.events.DrainedModelRequest' })

  assert.equal(items.filter((i) => i.kind === 'user' && i.text === 'focus on 2026').length, 1)
  assert.equal(pending.queued, false)    // same bubble, no longer pending
})

test('TurnCancelled ends the turn, keeps the partial reply, and clears busy', () => {
  const items: ThreadItem[] = []
  foldEvent(items, user('research widgets'))
  foldEvent(items, chunk('I found '))
  assert.equal(isBusy(items), true)

  foldEvent(items, cancelled('Stopped'))

  const agent = itemOfKind(items.find((i) => i.kind === 'agent'), 'agent')
  assert.equal(agent.text, 'I found ')   // what it said before the stop is kept
  assert.equal(agent.streaming, false)   // and finalized, so it renders as a bubble
  const note = itemOfKind(items.at(-1), 'note')
  assert.equal(note.text, 'Stopped')
  assert.equal(note.ends, true)
  assert.equal(isBusy(items), false)     // replay of a cancelled turn is never "thinking"
})

test('TurnFailed keeps the work, explains why, and clears busy', () => {
  const items: ThreadItem[] = []
  foldEvent(items, user('list the open PRs'))
  foldEvent(items, chunk('Fetching '))
  assert.equal(isBusy(items), true)

  foldEvent(items, failed('The turn timed out before it finished.'))

  const agent = itemOfKind(items.find((i) => i.kind === 'agent'), 'agent')
  assert.equal(agent.text, 'Fetching ')  // work done before the failure survives
  assert.equal(agent.streaming, false)   // finalized, so it renders as a bubble
  const note = itemOfKind(items.at(-1), 'note')
  assert.equal(note.text, 'The turn timed out before it finished.')
  assert.equal(note.alert, true)         // reads as a failure, not a normal note
  assert.equal(note.ends, true)
  assert.equal(isBusy(items), false)     // a reloaded failed chat is never "thinking"
})

test('TurnFailed falls back to a generic reason when the error is empty', () => {
  const items: ThreadItem[] = []
  foldEvent(items, user('hello'))
  foldEvent(items, failed(''))
  assert.equal(itemOfKind(items.at(-1), 'note').text, 'The turn failed unexpectedly.')
})

test('an A2UI action shows one transient indicator and retires it on the reply', () => {
  const items: ThreadItem[] = []
  foldEvent(items, a2uiAction())
  foldEvent(items, a2uiAction())

  assert.equal(items.filter((i) => i.kind === 'note' && i.a2uiActionPending).length, 1)

  foldEvent(items, response('Your adventure continues.'))
  assert.equal(items.filter((i) => i.kind === 'note' && i.a2uiActionPending).length, 0)
  assert.equal(itemOfKind(items.at(-1), 'agent').text, 'Your adventure continues.')
})

test('an unknown event type leaves the item list untouched', () => {
  const items: ThreadItem[] = []
  foldEvent(items, { type: 'ag2.events.SomeFutureEvent', data: {} })
  assert.equal(items.length, 0)
})

test('a payload that does not match its schema is skipped, not thrown', () => {
  const items: ThreadItem[] = []
  foldEvent(items, user('hello'))
  // ImageGenerated without its required `path`
  foldEvent(items, { type: 'ag2.events.ImageGenerated', data: { prompt: 'a cat' } })
  assert.equal(items.length, 1)
})

test('items folded here and surfaces built by a2ui share one id space', () => {
  const items: ThreadItem[] = []
  foldEvent(items, user('draw me a plan'))
  foldEvent(items, {
    type: 'assistant.events.A2UIMessageEvent',
    data: { message: { createSurface: { surfaceId: 's1' } } },
  })
  foldEvent(items, user('and again'))

  const ids = items.map((i) => i.id)
  assert.equal(items.length, 3)
  assert.equal(new Set(ids).size, 3)
  assert.equal(ids.every((id) => typeof id === 'number'), true)
})
