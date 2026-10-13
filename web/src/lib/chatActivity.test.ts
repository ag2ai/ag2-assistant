import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chatEventStatus, mergeChatRows, mergeChatStatus, pendingChatDeadlines } from './chatActivity.ts'
import type { ChatRow } from '../schemas/chat.ts'
import type { WireEvent } from '../schemas/events.ts'
import { foldEvent } from '../project.ts'
import type { ThreadItem } from '../schemas/events.ts'

const row: ChatRow = {
  chat_id: 'chat', title: 'Chat', starred: false, updated: '', preview: 'Hello', turns: 1,
  status: 'completed', status_version: 'turn-1', status_updated: 10, unread: true,
  turn_started_at: 9, waiting_until: null,
}
const event = (type: string, data: Record<string, unknown>): WireEvent => ({ type: 'assistant.events.' + type, data })

test('a new Turn replaces a prior unread outcome and its receipt cannot hide the new result', () => {
  const running = mergeChatStatus(row, chatEventStatus(row, event('TurnStarted', { turn_id: 'turn-2', created_at: 11 }), [])!)
  assert.equal(running.status, 'running')
  assert.equal(running.unread, false)
  assert.equal(running.turn_started_at, 11)
  const done = mergeChatStatus(running, chatEventStatus(running, event('TurnCompleted', { turn_id: 'turn-2', created_at: 12 }), [])!)
  assert.equal(done.status, 'completed')
  assert.equal(done.unread, true)
  assert.equal(chatEventStatus(done, event('ChatSeen', { turn_id: 'turn-1' }), []), null)
  const seen = chatEventStatus(done, event('ChatSeen', { turn_id: 'turn-2', created_at: 13 }), [])!
  assert.equal(seen.status, null)
  assert.equal(seen.unread, false)
})

test('an answered inquiry keeps waiting while another inquiry is pending', () => {
  const waiting = { ...row, ...chatEventStatus(row, event('InquiryRaised', { inquiry_id: 'question' }), [310, 320])! }
  assert.equal(waiting.status, 'waiting')
  assert.equal(waiting.waiting_until, 310)
  const remaining = chatEventStatus(waiting, event('InquiryAnswered', { status: 'expired' }), [320])!
  assert.equal(remaining.status, 'waiting')
  assert.equal(remaining.waiting_until, 320)
  const resumed = chatEventStatus(waiting, event('InquiryAnswered', { status: 'expired' }), [])!
  assert.equal(resumed.status, 'running')
  assert.equal(resumed.waiting_until, null)
  assert.equal(resumed.turn_started_at, row.turn_started_at)
})

test('a failure stays unread and a cancellation removes the status', () => {
  assert.equal(chatEventStatus(row, event('TurnFailed', {}), [])?.status, 'error')
  assert.equal(chatEventStatus(row, event('TurnCancelled', {}), [])?.status, null)
  assert.equal(chatEventStatus(row, event('ModelResponse', {}), []), null)
})

test('a stale poll refreshes chat metadata without rolling back live activity', () => {
  const current = { ...row, status: 'waiting' as const, status_version: 'turn-2', status_updated: 12, turn_started_at: 11, waiting_until: 310, unread: false }
  const result = mergeChatRows([current], [{ ...row, title: 'Renamed' }], new Map())
  assert.equal(result[0].title, 'Renamed')
  assert.equal(result[0].status, 'waiting')
  assert.equal(result[0].status_version, 'turn-2')
  assert.equal(result[0].turn_started_at, 11)
  assert.equal(result[0].waiting_until, 310)
  assert.equal(mergeChatStatus(current, row).status, 'waiting')
})

test('polling cannot restore a read outcome, but a later Turn becomes unread', () => {
  const seen = new Map([['chat', 'turn-1']])
  assert.equal(mergeChatRows([], [row], seen)[0].status, null)
  const later = { ...row, status_version: 'turn-2', status_updated: 12 }
  assert.equal(mergeChatRows([], [later], seen)[0].status, 'completed')
  assert.equal(mergeChatRows([], [later], seen)[0].unread, true)
})

test('a new Turn never inherits the countdown of an unanswered older inquiry', () => {
  const items: ThreadItem[] = []
  foldEvent(items, event('InquiryRaised', { inquiry_id: 'old', question: 'Old?', created_at: 10, expires_at: 310 }))
  foldEvent(items, event('TurnCancelled', { created_at: 11 }))
  foldEvent(items, event('InquiryRaised', { inquiry_id: 'current', question: 'New?', created_at: 20, expires_at: 320 }))
  assert.deepEqual(pendingChatDeadlines(items, 15), [320])
  foldEvent(items, event('InquiryAnswered', { inquiry_id: 'current', status: 'expired' }))
  assert.deepEqual(pendingChatDeadlines(items, 15), [])
})
