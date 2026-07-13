// The event → items projection. Covers the turn-ending rules: a stopped turn must
// stay stopped when the stream replays (isBusy is what drives the thinking dots).
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { foldEvent, isBusy, queueMessage } from './project.js'

const user = (text) => ({
  type: 'ag2.events.ModelRequest',
  data: { parts: [{ __event__: 'ag2.events.TextInput', content: text }] },
})
const chunk = (text) => ({ type: 'ag2.events.ModelMessageChunk', data: { content: text } })
const cancelled = (reason) => ({ type: 'assistant.events.TurnCancelled', data: { reason } })

test('a user message with no reply yet is busy', () => {
  const items = []
  foldEvent(items, user('hello'))
  assert.equal(isBusy(items), true)
})

test('a message fed to a running turn renders as a user bubble where it landed', () => {
  const items = []
  foldEvent(items, user('research widgets'))
  foldEvent(items, chunk('searching…'))
  // AG2 re-emits a fed message as DrainedModelRequest when the running turn picks it up
  foldEvent(items, { ...user('focus on 2026'), type: 'ag2.events.DrainedModelRequest' })

  assert.deepEqual(
    items.filter((i) => i.kind === 'user').map((i) => i.text),
    ['research widgets', 'focus on 2026']
  )
  assert.equal(isBusy(items), true)   // the turn is still running after the steer
})

test('a queued message resolves in place when the agent drains it — no duplicate', () => {
  const items = []
  foldEvent(items, user('research widgets'))
  queueMessage(items, 'focus on 2026')   // server acked the feed; agent hasn't reached it yet

  const pending = items.at(-1)
  assert.equal(pending.queued, true)

  // …a tool round later, AG2 drains the inbox and re-emits it
  foldEvent(items, { ...user('focus on 2026'), type: 'ag2.events.DrainedModelRequest' })

  assert.equal(items.filter((i) => i.kind === 'user' && i.text === 'focus on 2026').length, 1)
  assert.equal(pending.queued, false)    // same bubble, no longer pending
})

test('TurnCancelled ends the turn, keeps the partial reply, and clears busy', () => {
  const items = []
  foldEvent(items, user('research widgets'))
  foldEvent(items, chunk('I found '))
  assert.equal(isBusy(items), true)

  foldEvent(items, cancelled('Stopped'))

  const agent = items.find((i) => i.kind === 'agent')
  assert.equal(agent.text, 'I found ')   // what it said before the stop is kept
  assert.equal(agent.streaming, false)   // and finalized, so it renders as a bubble
  const note = items.at(-1)
  assert.equal(note.kind, 'note')
  assert.equal(note.text, 'Stopped')
  assert.equal(note.ends, true)
  assert.equal(isBusy(items), false)     // replay of a cancelled turn is never "thinking"
})
