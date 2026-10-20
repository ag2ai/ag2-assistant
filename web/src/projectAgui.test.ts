// The AG-UI → items projection: what each event the gateway sends becomes in the thread.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { AguiState, foldAgui, runningAfter } from './projectAgui.ts'
import { queueMessage } from './project.ts'
import { activity, custom, raw, runError, runFinished, runStarted, say, toolCall } from './testing/agui.ts'
import type { AguiEvent } from './schemas/agui.ts'
import type { ThreadItem } from './schemas/events.ts'

function fold(events: AguiEvent[], items: ThreadItem[] = [], state = new AguiState()): ThreadItem[] {
  for (const event of events) foldAgui(items, event, state)
  return items
}

test('a message folds into one bubble per role and keeps its AG-UI id', () => {
  const items = fold([...say('user', 'u1', 'hi', 1), ...say('assistant', 'a1', 'hello', 2)])
  assert.deepEqual(items.map((i) => [i.kind, 'text' in i && i.text, i.msg]), [['user', 'hi', 'u1'], ['agent', 'hello', 'a1']])
})

test('an assistant message streams and takes the log time its end carries', () => {
  const items: ThreadItem[] = []
  const state = new AguiState()
  const [start, content, end] = say('assistant', 'a1', 'par', 7)
  foldAgui(items, start, state)
  foldAgui(items, content, state)
  assert.equal(items[0].kind === 'agent' && items[0].streaming, true)
  foldAgui(items, end, state)
  assert.equal(items[0].kind === 'agent' && items[0].streaming, false)
  assert.equal(items[0].at, 7)
})

test('a message met twice, in history and live, is drawn once', () => {
  const events = say('assistant', 'a1', 'hello', 2)
  const items = fold(events)
  fold(events, items, new AguiState())
  assert.equal(items.length, 1)
  assert.equal(items[0].kind === 'agent' && items[0].text, 'hello')
})

test('a response resumed under the same id replaces the interrupted text', () => {
  const state = new AguiState()
  const items = fold(say('assistant', 'a1', 'Before ', 2), [], state)
  const id = items[0].id
  fold([runFinished({ type: 'interrupt', interrupts: [{ id: 'q1', reason: 'choice' }] }), runStarted(), ...say('assistant', 'a1', 'Before after', 3)], items, state)
  assert.equal(items.filter(item => item.kind === 'agent').length, 1)
  const answer = items.find(item => item.kind === 'agent')
  assert.ok(answer?.kind === 'agent')
  assert.equal(answer.id, id)
  assert.equal(answer.text, 'Before after')
  assert.equal(answer.streaming, false)
  assert.equal(answer.at, 3)
})

test('a subscription snapshot replaces an open prefix rather than appending it twice', () => {
  const state = new AguiState()
  const items = fold(say('assistant', 'a1', 'prefix:', 2).slice(0, 2), [], state)
  fold(say('assistant', 'a1', 'prefix:done', 3), items, state)
  assert.equal(items.length, 1)
  assert.equal(items[0].kind === 'agent' && items[0].text, 'prefix:done')
})

test("the echo of a fed message resolves its queued bubble in place", () => {
  const items = queueMessage([], 'one more thing')
  fold(say('user', 'u2', 'one more thing', 3), items)
  assert.equal(items.length, 1)
  assert.equal(items[0].kind === 'user' && items[0].queued, false)
  assert.equal(items[0].msg, 'u2')
})

test('a tool call folds into a chip once its arguments have arrived', () => {
  const items = fold(toolCall('t1', 'read_file', { path: 'a.txt' }))
  assert.equal(items.length, 1)
  assert.equal(items[0].kind, 'tools')
  assert.equal(items[0].kind === 'tools' && items[0].runs[0].detail, 'a.txt')
})

test('a Card surface keeps its kind from the content it carries', () => {
  const component = { id: 'root', component: 'Text', text: 'x' }
  const items = fold([
    activity({ surface_id: 's', component }),
    activity({ surface_id: 'f', component, file_path: 'cards/a.json', file_surface_id: 'inst' }, 'surface-f'),
  ])
  assert.equal(items.length, 2)
  assert.equal(items[1].kind === 'a2ui' && items[1].filePath, 'cards/a.json')
})

test('an assistant event rides CUSTOM and folds as it always did', () => {
  const items = fold([custom('assistant.events.ObserverAlert', { message: 'stuck?' })])
  assert.equal(items[0].kind === 'note' && items[0].text, 'stuck?')
})

test('a root task is a note, a subagent task a card that finishes', () => {
  const items = fold([
    raw('SUBAGENT_STARTED', { subagentRunId: 'r', name: 'ag2-assistant' }),
    raw('SUBAGENT_STARTED', { subagentRunId: 's', name: 'researcher', description: 'find it' }),
    raw('SUBAGENT_FINISHED', { subagentRunId: 's', result: 'found' }),
  ])
  assert.equal(items[0].kind === 'note' && items[0].text, 'Task started')
  const card = items[1]
  assert.ok(card.kind === 'subagent')
  assert.deepEqual([card.agent, card.objective, card.status, card.result], ['researcher', 'find it', 'completed', 'found'])
})

test('a cancelled subagent says so', () => {
  const items = fold([
    raw('SUBAGENT_STARTED', { subagentRunId: 's', name: 'researcher' }),
    raw('SUBAGENT_ERROR', { subagentRunId: 's', message: 'stopped', rawEvent: { status: 'cancelled' } }),
  ])
  assert.ok(items[0].kind === 'subagent')
  assert.equal(items[0].status, 'cancelled')
})

test('an interrupt becomes the Inquiry card, once', () => {
  const interrupted = runFinished({ type: 'interrupt', interrupts: [{ id: 'q1', reason: 'choice', message: 'Which?', metadata: { options: ['a', 'b'], detail: 'd' } }] })
  const items = fold([interrupted, interrupted])
  assert.equal(items.length, 1)
  const card = items[0]
  assert.ok(card.kind === 'inquiry')
  assert.deepEqual([card.inquiryId, card.question, card.options, card.qkind], ['q1', 'Which?', ['a', 'b'], 'choice'])
})

test('a failed run leaves an alert note', () => {
  const items = fold([runError('boom')])
  assert.deepEqual(items.map((i) => i.kind === 'note' && [i.text, i.alert]), [['boom', true]])
})

test('only run events say whether a run is in flight', () => {
  assert.equal(runningAfter(runStarted()), true)
  assert.equal(runningAfter(runFinished()), false)
  assert.equal(runningAfter(runError('x')), false)
  assert.equal(runningAfter(say('user', 'u', 'x', 1)[0]), undefined)
})
