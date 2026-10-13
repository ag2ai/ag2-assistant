import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chatEventStatus, mergeChatRows, mergeChatStatus } from './chatActivity.ts'
import type { ChatRow } from '../schemas/chat.ts'
import type { WireEvent } from '../schemas/events.ts'

const row: ChatRow = {
  chat_id: 'chat', title: 'Chat', starred: false, updated: '', preview: 'Hello', turns: 1,
  status: 'completed', status_version: 'turn-1', status_updated: 10, unread: true,
}
const event = (type: string, data: Record<string, unknown>): WireEvent => ({ type: 'assistant.events.' + type, data })

test('a new Turn replaces a prior unread outcome and its receipt cannot hide the new result', () => {
  const running = mergeChatStatus(row, chatEventStatus(row, event('TurnStarted', { turn_id: 'turn-2', created_at: 11 }), false)!)
  assert.equal(running.status, 'running')
  assert.equal(running.unread, false)
  const done = mergeChatStatus(running, chatEventStatus(running, event('TurnCompleted', { turn_id: 'turn-2', created_at: 12 }), false)!)
  assert.equal(done.status, 'completed')
  assert.equal(done.unread, true)
  assert.equal(chatEventStatus(done, event('ChatSeen', { turn_id: 'turn-1' }), false), null)
  const seen = chatEventStatus(done, event('ChatSeen', { turn_id: 'turn-2', created_at: 13 }), false)!
  assert.equal(seen.status, null)
  assert.equal(seen.unread, false)
})

test('an answered inquiry keeps waiting while another inquiry is pending', () => {
  const waiting = { ...row, ...chatEventStatus(row, event('InquiryRaised', { inquiry_id: 'question' }), true)! }
  assert.equal(waiting.status, 'waiting')
  assert.equal(chatEventStatus(waiting, event('InquiryAnswered', {}), true)?.status, 'waiting')
  assert.equal(chatEventStatus(waiting, event('InquiryAnswered', {}), false)?.status, 'running')
})

test('a failure stays unread and a cancellation removes the status', () => {
  assert.equal(chatEventStatus(row, event('TurnFailed', {}), false)?.status, 'error')
  assert.equal(chatEventStatus(row, event('TurnCancelled', {}), false)?.status, null)
  assert.equal(chatEventStatus(row, event('ModelResponse', {}), false), null)
})

test('a stale poll refreshes chat metadata without rolling back live activity', () => {
  const current = { ...row, status: 'running' as const, status_version: 'turn-2', status_updated: 12, unread: false }
  const result = mergeChatRows([current], [{ ...row, title: 'Renamed' }], new Map())
  assert.equal(result[0].title, 'Renamed')
  assert.equal(result[0].status, 'running')
  assert.equal(result[0].status_version, 'turn-2')
  assert.equal(mergeChatStatus(current, row).status, 'running')
})

test('polling cannot restore a read outcome, but a later Turn becomes unread', () => {
  const seen = new Map([['chat', 'turn-1']])
  assert.equal(mergeChatRows([], [row], seen)[0].status, null)
  const later = { ...row, status_version: 'turn-2', status_updated: 12 }
  assert.equal(mergeChatRows([], [later], seen)[0].status, 'completed')
  assert.equal(mergeChatRows([], [later], seen)[0].unread, true)
})
