import { test } from 'node:test'
import assert from 'node:assert/strict'
import { projectHistory } from './chatHistory.ts'
import { foldEvent } from '../project.ts'
import type { WireEvent } from '../schemas/events.ts'

const request = (content: string, at: number): WireEvent => ({ type: 'ModelRequest', data: { created_at: at, parts: [{ __event__: 'TextInput', content }] } })
const response = (content: string, at: number): WireEvent => ({ type: 'ModelResponse', data: { created_at: at, message: { content } } })

test('prepending older history preserves visible identities and live responses', () => {
  const old = [request('Old question', 1), response('Old answer', 2)]
  const recent = [request('Recent question', 3), response('Recent answer', 4)]
  const visible = projectHistory(recent, [])
  const ids = visible.map(item => item.id)
  const live = request('Live question', 5)
  foldEvent(visible, live)
  const combined = projectHistory([...old, ...recent, live], visible)
  assert.deepEqual(combined.slice(2, 4).map(item => item.id), ids)
  assert.equal(combined.at(-1)?.id, visible.at(-1)?.id)
  assert.equal(new Set(combined.map(item => item.id)).size, combined.length)
})

test('later feedback and inquiry resolutions also apply to newly loaded items', () => {
  const events: WireEvent[] = [request('Old', 1), response('Answer', 2),
    { type: 'InquiryRaised', data: { created_at: 3, inquiry_id: 'q1', question: 'Proceed?' } },
    request('New', 4),
    { type: 'FeedbackGiven', data: { created_at: 5, target_kind: 'message', target_id: '2', sentiment: 'positive', reason: 'Useful' } },
    { type: 'InquiryAnswered', data: { created_at: 6, inquiry_id: 'q1', answer: 'Yes' } }]
  const visible = projectHistory(events.slice(3), [])
  const all = projectHistory(events, visible)
  assert.equal(all.find(item => item.kind === 'agent')?.feedback?.sentiment, 'positive')
  const inquiry = all.find(item => item.kind === 'inquiry')
  assert.ok(inquiry?.resolved)
  assert.equal(inquiry.answer, 'Yes')
})

test('reconnect updates a card while keeping its mounted identity', () => {
  const surface: WireEvent = { type: 'A2UISurface', data: { created_at: 2, surface_id: 'chart', component: { id: 'root', component: 'Text', text: 'Chart' }, data: { value: 1 } } }
  const events = [request('Chart', 1), surface, response('Done', 3)]
  const visible = projectHistory(events, [])
  const updated = { ...surface, data: { ...surface.data, data: { value: 2 } } }
  const reconnected = projectHistory([events[0], updated, events[2]], visible)
  assert.deepEqual(reconnected.map(item => item.id), visible.map(item => item.id))
  assert.equal(reconnected.find(item => item.kind === 'a2ui')?.data.value, 2)
})
