import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ChatEvents, ChatRow, Transcript } from './chat.ts'

test('chat history rejects malformed protocol events and preserves valid event fields', () => {
  const page = (events: unknown[]) => ({ events, before: null, start: null })
  assert.equal(ChatEvents.safeParse(page([{}])).success, false)
  assert.equal(ChatEvents.safeParse(page([{ type: 'TEXT_MESSAGE_CONTENT', messageId: 'm' }])).success, false)
  const events = [
    { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm', delta: 'hello' },
    { type: 'TEXT_MESSAGE_END', messageId: 'm', rawEvent: { created_at: 7 } },
    { type: 'CUSTOM', name: 'assistant.events.Attachment', value: { path: 'uploads/a.txt' } },
  ]
  assert.deepEqual(ChatEvents.parse(page(events)).events, events)
})

// A row for an owner-started chat: gateway ships response_model_exclude_unset,
// so the origin_* trio is genuinely absent, never null.
test('ChatRow parses a plain chat with no origin fields', () => {
  const parsed = ChatRow.parse({
    chat_id: 'c1', updated: '2026-01-01T00:00:00Z', title: 'Hi',
    starred: false, preview: 'hi', turns: 1,
    status: null, status_version: '', status_updated: 0, unread: false,
    turn_started_at: 0, waiting_until: null,
  })
  assert.equal(parsed.origin_platform, undefined)
  assert.equal(parsed.origin_name, undefined)
  assert.equal(parsed.origin_live, undefined)
})

test('ChatRow parses an ACP chat carrying its origin', () => {
  const parsed = ChatRow.parse({
    chat_id: 'acp-1', updated: '2026-01-01T00:00:00Z', title: '',
    starred: false, preview: 'hi', turns: 1,
    status: 'running', status_version: 'turn-1', status_updated: 1, unread: false,
    turn_started_at: 1, waiting_until: null,
    origin_platform: 'acp', origin_name: 'Space', origin_live: true,
  })
  assert.equal(parsed.origin_platform, 'acp')
  assert.equal(parsed.origin_name, 'Space')
  assert.equal(parsed.origin_live, true)
})

test('Transcript parses with and without the origin trio', () => {
  const plain = Transcript.parse({
    chat_id: 'c1', messages: [], model: '', effective_model: 'gpt',
  })
  assert.equal(plain.origin_platform, undefined)

  const acp = Transcript.parse({
    chat_id: 'acp-1', messages: [{ role: 'user', text: 'hi' }],
    model: '', effective_model: 'gpt',
    origin_platform: 'acp', origin_name: 'Space', origin_live: false,
  })
  assert.equal(acp.origin_name, 'Space')
  assert.equal(acp.origin_live, false)
})
