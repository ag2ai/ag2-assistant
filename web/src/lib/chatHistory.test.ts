import { test } from 'node:test'
import assert from 'node:assert/strict'
import { projectHistory } from './chatHistory.ts'
import { AguiState, foldAgui } from '../projectAgui.ts'
import { activity, custom, say } from '../testing/agui.ts'

test('prepending older history preserves visible identities and live responses', () => {
  const old = [...say('user', 'u1', 'Old question', 1), ...say('assistant', 'a1', 'Old answer', 2)]
  const recent = [...say('user', 'u2', 'Recent question', 3), ...say('assistant', 'a2', 'Recent answer', 4)]
  const visible = projectHistory(recent, [])
  const ids = visible.map(item => item.id)
  const live = say('user', 'u3', 'Live question', 5)
  const state = new AguiState()
  for (const event of live) foldAgui(visible, event, state)
  const combined = projectHistory([...old, ...recent, ...live], visible)
  assert.deepEqual(combined.slice(2, 4).map(item => item.id), ids)
  assert.equal(combined.at(-1)?.id, visible.at(-1)?.id)
  assert.equal(new Set(combined.map(item => item.id)).size, combined.length)
})

test('later feedback and inquiry resolutions also apply to newly loaded items', () => {
  const events = [
    ...say('user', 'u1', 'Old', 1), ...say('assistant', 'a1', 'Answer', 2),
    custom('assistant.events.InquiryRaised', { created_at: 3, inquiry_id: 'q1', question: 'Proceed?' }),
    ...say('user', 'u2', 'New', 4),
    custom('assistant.events.FeedbackGiven', { created_at: 5, target_kind: 'message', target_id: '2', sentiment: 'positive', reason: 'Useful' }),
    custom('assistant.events.InquiryAnswered', { created_at: 6, inquiry_id: 'q1', answer: 'Yes' }),
  ]
  const visible = projectHistory(events.slice(7), [])
  const all = projectHistory(events, visible)
  assert.equal(all.find(item => item.kind === 'agent')?.feedback?.sentiment, 'positive')
  const inquiry = all.find(item => item.kind === 'inquiry')
  assert.ok(inquiry?.resolved)
  assert.equal(inquiry.answer, 'Yes')
})

test('reconnect updates a card while keeping its mounted identity', () => {
  const surface = (value: number) => activity({
    created_at: 2, surface_id: 'chart', component: { id: 'root', component: 'Text', text: 'Chart' }, data: { value },
  })
  const events = [...say('user', 'u1', 'Chart', 1), surface(1), ...say('assistant', 'a1', 'Done', 3)]
  const visible = projectHistory(events, [])
  const reconnected = projectHistory([...events.slice(0, 3), surface(2), ...events.slice(4)], visible)
  assert.deepEqual(reconnected.map(item => item.id), visible.map(item => item.id))
  assert.equal(reconnected.find(item => item.kind === 'a2ui')?.data.value, 2)
})
