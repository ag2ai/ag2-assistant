import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ChatRow, Transcript } from './chat.ts'

// A row for an owner-started chat: gateway ships response_model_exclude_unset,
// so the origin_* trio is genuinely absent, never null.
test('ChatRow parses a plain chat with no origin fields', () => {
  const parsed = ChatRow.parse({
    chat_id: 'c1', updated: '2026-01-01T00:00:00Z', title: 'Hi',
    starred: false, preview: 'hi', turns: 1,
  })
  assert.equal(parsed.origin_platform, undefined)
  assert.equal(parsed.origin_name, undefined)
  assert.equal(parsed.origin_live, undefined)
})

test('ChatRow parses an ACP chat carrying its origin', () => {
  const parsed = ChatRow.parse({
    chat_id: 'acp-1', updated: '2026-01-01T00:00:00Z', title: '',
    starred: false, preview: 'hi', turns: 1,
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
